"""
arq Worker — async Redis queue for document ingestion.

The worker now compiles each source into the LLM Wiki (markdown pages stored
in PostgreSQL) instead of producing chunk embeddings. See app/ai/wiki_compiler.py.

Start with:
    arq app.worker.WorkerSettings
"""

import asyncio
import uuid
import zipfile
from typing import Optional

from arq import cron, func as arq_func
from arq.connections import ArqRedis, RedisSettings, create_pool
from loguru import logger
from sqlalchemy import select

from app.config import settings


def _get_redis_settings() -> RedisSettings:
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
        password=settings.redis_password or None,
    )


# arq Redis pool (lazy init)
_arq_pool: Optional[ArqRedis] = None


async def get_arq_pool() -> ArqRedis:
    """Lazy-init arq Redis connection pool."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(_get_redis_settings())
    return _arq_pool


# ---------------------------------------------------------------------------
# Progress helper (re-exported from utils for backward compatibility)
# ---------------------------------------------------------------------------

from app.utils.progress import ProgressTracker  # noqa: E402


# ---------------------------------------------------------------------------
# Ingestion tasks
# ---------------------------------------------------------------------------

async def ingest_file_task(ctx: dict, source_id: str):
    """
    arq task: full file ingestion → wiki compilation.
    Steps: download from MinIO → extract text → outline → enqueue MRP + caption_images_task.
    Image captioning is offloaded to caption_images_task so this job is not blocked by image count.
    File must already be uploaded to MinIO before this task is enqueued.
    """
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source, SourceImage
    from app.services.image_service import extract_images
    from app.services.kb_service import (
        _extract_text_from_file,
        _inline_image_markers,
    )
    from app.services.source_outline import assemble_full_text, build_outline
    from app.services.storage_service import storage_service

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            logger.warning(f"Source {source_id} not found, it may have been deleted.")
            return
        if not source.minio_key:
            raise ValueError(f"Source {source_id} has no file in storage")

        file_name = source.file_name or source.minio_key.split("/")[-1]

        try:
            source.status = "processing"
            source.progress = 0
            source.progress_message = "Starting processing..."
            await session.commit()

            # --- Step 1: Download from MinIO (10%) ---
            await tracker.update(5, "Loading file...")
            file_data = storage_service.download_file(source.minio_key)
            await tracker.update(10, "File loaded")

            # --- Step 2: Extract text per page (25%) ---
            await tracker.update(15, "Extracting text (per page)...")
            pages_data = await _extract_text_from_file(file_data, file_name)

            if not pages_data or not any((p.get("content") or "").strip() for p in pages_data):
                source.status = "error"
                source.error_message = "Unable to extract text content"
                source.progress = 0
                await session.commit()
                return {"status": "error", "message": "No text content"}

            await tracker.update(25, "Text extraction complete")

            # --- Step 3: Extract images (40%) ---
            # Captioning is offloaded to caption_images_task (enqueued below) so
            # this job is not blocked by the number of images in the document.
            await tracker.update(30, "Extracting images...")
            images = extract_images(file_data, file_name, source_id)

            # Persist images so wiki content_md can reference them by uuid.
            for img in images:
                row = SourceImage(
                    source_id=uuid.UUID(source_id),
                    minio_key=img.minio_key,
                    page_number=img.page_number,
                    image_index=img.image_index,
                    caption=img.caption,
                    content_type=img.content_type,
                    size_bytes=img.size_bytes,
                )
                session.add(row)
                await session.flush()
                img.image_id = str(row.id)

            # Inline image markers into per-page text so the compiler sees them.
            _inline_image_markers(pages_data, images)
            await tracker.update(40, f"Analyzed {len(images)} images")

            # --- Step 4: Build outline + assemble full_text (50%) ---
            await tracker.update(45, "Building document outline...")
            source.outline_json = build_outline(pages_data)
            full_text, page_offsets = assemble_full_text(pages_data)
            source.full_text = full_text
            source.page_offsets = page_offsets
            await session.commit()
            await tracker.update(50, f"Outline: {len(source.outline_json or [])} top-level sections")

            # --- Step 5: Resolve KnowledgeType context (52%) ---
            kt_slug = kt_name = kt_desc = None
            if source.knowledge_type_id:
                kt = await session.get(KnowledgeType, source.knowledge_type_id)
                if kt:
                    kt_slug, kt_name, kt_desc = kt.slug, kt.name, kt.description

            # --- Step 6: Enqueue captioning (if images) OR MRP directly ---
            # Captioning MUST complete before MAP so wiki pages get real image captions
            # baked into full_text. caption_images_task chains into ingest_map_reduce_task
            # itself when it finishes.
            await tracker.update(55, "Queuing compilation pipeline...")
            pool = await get_arq_pool()
            if images:
                job = await pool.enqueue_job("caption_images_task", source_id)
                source.progress_message = f"Captioning {len(images)} images before extraction..."
            else:
                job = await pool.enqueue_job("ingest_map_reduce_task", source_id)
                source.progress_message = "Extraction queued..."
            source.status = "processing"
            source.progress = 55
            if job:
                source.job_id = job.job_id
            await session.commit()

            logger.info(f"Source {source_id} pre-processing done; next: {'caption→MRP' if images else 'MRP'}")
            return {"status": "processing", "images": len(images)}

        except BaseException as e:
            logger.error(f"Pre-processing failed for {source_id}: {e}")
            error_msg = str(e)[:500]
            progress_msg = f"Error: {str(e)[:200]}"

            async def _mark_error_file() -> None:
                from app.database import async_session_factory as _sf
                from app.database.models import Source as _Source
                async with _sf() as err_session:
                    src = await err_session.get(_Source, sid)
                    if src:
                        src.status = "error"
                        src.error_message = error_msg
                        src.progress = 0
                        src.progress_message = progress_msg
                        await err_session.commit()

            try:
                await asyncio.shield(_mark_error_file())
            except Exception:
                pass
            raise


async def ingest_url_task(ctx: dict, source_id: str):
    """arq task: URL ingestion → wiki compilation."""
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source
    from app.services.kb_service import _extract_text_from_url
    from app.services.source_outline import assemble_full_text, build_outline

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            logger.warning(f"Source {source_id} not found, it may have been deleted.")
            return

        try:
            source.status = "processing"
            source.progress = 0
            await session.commit()

            await tracker.update(15, "Fetching content from URL...")
            if not source.url:
                source.status = "error"
                source.error_message = "Source has no URL"
                await session.commit()
                return {"status": "error"}
            pages_data = await _extract_text_from_url(source.url)

            if not pages_data or not any((p.get("content") or "").strip() for p in pages_data):
                source.status = "error"
                source.error_message = "Unable to fetch content from URL"
                await session.commit()
                return {"status": "error"}

            await tracker.update(40, "Building outline...")
            source.outline_json = build_outline(pages_data)
            full_text, page_offsets = assemble_full_text(pages_data)
            source.full_text = full_text
            source.page_offsets = page_offsets
            await session.commit()

            kt_slug = kt_name = kt_desc = None
            if source.knowledge_type_id:
                kt = await session.get(KnowledgeType, source.knowledge_type_id)
                if kt:
                    kt_slug, kt_name, kt_desc = kt.slug, kt.name, kt.description

            await tracker.update(55, "Queuing compilation pipeline...")
            pool = await get_arq_pool()
            job = await pool.enqueue_job("ingest_map_reduce_task", source_id)
            source.status = "processing"
            source.progress = 55
            source.progress_message = "Extraction queued..."
            if job:
                source.job_id = job.job_id
            await session.commit()

            logger.info(f"URL source {source_id} pre-processing done, MRP task enqueued: {job.job_id if job else 'n/a'}")
            return {"status": "processing"}

        except BaseException as e:
            logger.error(f"URL ingestion failed for {source_id}: {e}")
            error_msg = str(e)[:500]

            async def _mark_error_url() -> None:
                from app.database import async_session_factory as _sf
                from app.database.models import Source as _Source
                async with _sf() as err_session:
                    src = await err_session.get(_Source, sid)
                    if src:
                        src.status = "error"
                        src.error_message = error_msg
                        src.progress = 0
                        await err_session.commit()

            try:
                await asyncio.shield(_mark_error_url())
            except Exception:
                pass
            raise


# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------


async def ingest_skill_task(ctx: dict, skill_id: str, version_id: str, file_path: str, file_name: str):
    """
    arq task: unzip skill package from disk buffer, store in MinIO, and extract metadata.
    """
    import os

    from app.database import async_session_factory
    from app.database.models import Skill, SkillVersion
    from app.services.storage_service import storage_service

    sid = uuid.UUID(skill_id)
    vid = uuid.UUID(version_id)
    skill_name = file_name.rsplit(".", 1)[0]
    
    logger.info(f"Starting ingestion for skill: {skill_name} ({skill_id})")

    async with async_session_factory() as session:
        skill = await session.get(Skill, sid)
        version = await session.get(SkillVersion, vid)
        
        if not skill or not version:
            logger.error(f"Skill {skill_id} or Version {version_id} not found in DB")
            return

        try:
            skill.status = "processing"
            await session.commit()

            if not os.path.exists(file_path):
                logger.error(f"Disk buffer file not found: {file_path}")
                skill.status = "error"
                await session.commit()
                return

            import asyncio

            from app.services.kb_service import _guess_content_type

            # 1. Unzip with streaming, security checks, and concurrent uploads
            MAX_UNCOMPRESSED_SIZE = 10 * 1024 * 1024  # 10 MB
            MAX_FILE_COUNT = 100

            total_size = 0
            file_count = 0

            upload_tasks = []
            semaphore = asyncio.Semaphore(10)

            async def _upload_worker(zf_path, member_name, obj_name, file_size):
                async with semaphore:
                    # Open a fresh ZipFile instance in the thread to avoid GIL lock contention
                    with zipfile.ZipFile(zf_path) as local_zf:
                        with local_zf.open(member_name) as f_stream:
                            await storage_service.upload_stream_async(
                                obj_name, f_stream, file_size, _guess_content_type(member_name)
                            )

            with zipfile.ZipFile(file_path) as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    
                    filename = member.filename
                    
                    # [Security] Zip Slip check
                    if filename.startswith("/") or filename.startswith("\\") or "../" in filename or "..\\" in filename:
                        raise ValueError(f"Security risk: Zip Slip detected in {filename}")
                        
                    # [Security] File count check
                    file_count += 1
                    if file_count > MAX_FILE_COUNT:
                        raise ValueError(f"Too many files (exceeds {MAX_FILE_COUNT})")
                        
                    # [Security] Zip Bomb check
                    total_size += member.file_size
                    if total_size > MAX_UNCOMPRESSED_SIZE:
                        raise ValueError("Uncompressed size too large (exceeds 10MB)")

                    object_name = f"skills/{skill_id}/versions/{version.version_number}/content/{filename}"
                    target_readme = f"{skill_name}/SKILL.md".lower()

                    if filename.lower() == target_readme or filename.lower().endswith("/skill.md"):
                        with zf.open(member) as f:
                            content = f.read()
                        
                        storage_service.upload_file(
                            object_name=object_name,
                            data=content,
                            content_type=_guess_content_type(filename)
                        )
                    else:
                        upload_tasks.append(
                            _upload_worker(file_path, filename, object_name, member.file_size)
                        )

            if upload_tasks:
                await asyncio.gather(*upload_tasks)

            # 3. Calculate content-based hash (consistent with contribution workflow)
            storage_path = f"skills/{skill_id}/versions/{version.version_number}/content/"
            file_hash = storage_service.calculate_prefix_hash(storage_path)

            # 4. Update DB with extracted metadata

            skill.version_hash = file_hash
            skill.current_version = version.version_number
            skill.storage_path = storage_path
            skill.status = "active"
            
            version.version_hash = file_hash
            version.storage_path = storage_path
            
            await session.commit()
            logger.success(f"Skill {skill_name} version {version.version_number} processed successfully")

        except Exception as e:
            logger.exception(f"Failed to process skill {skill_name}: {e}")
            skill.status = "error"
            await session.commit()
        finally:
            # Clean up disk buffer
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"Cleaned up disk buffer: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {file_path}: {e}")


async def delete_skill_task(ctx: dict, skill_id: str):
    """
    arq task: delete skill files from MinIO and remove from DB.
    """
    from app.database import async_session_factory
    from app.database.models import Skill
    from app.services.storage_service import storage_service

    sid = uuid.UUID(skill_id)
    
    logger.info(f"Starting deletion task for skill: {skill_id}")

    async with async_session_factory() as session:
        skill = await session.get(Skill, sid)
        if not skill:
            logger.warning(f"Skill {skill_id} already deleted or not found")
            return

        try:
            from sqlalchemy.orm import selectinload
            # 1. Fetch skill with contributions to get their storage paths
            stmt = select(Skill).where(Skill.id == sid).options(selectinload(Skill.contributions))
            res = await session.execute(stmt)
            skill = res.scalars().first()
            if not skill:
                return

            # 2. Delete files from MinIO for the skill itself
            prefix = f"skills/{skill_id}/"
            storage_service.delete_prefix(prefix)
            
            # 3. Delete files for all associated contributions
            for contrib in skill.contributions:
                if contrib.storage_path:
                    logger.info(f"Deleting storage for contribution {contrib.id}: {contrib.storage_path}")
                    storage_service.delete_prefix(contrib.storage_path)

            # 4. Delete skill from DB (cascades to SkillVersion and SkillContribution DB rows)
            await session.delete(skill)
            await session.commit()
            
            logger.success(f"Skill {skill_id} and all related assets (versions, contributions) deleted successfully")

        except Exception as e:
            logger.exception(f"Failed to delete skill {skill_id}: {e}")
            raise


async def cleanup_temp_uploads_cron(ctx: dict):
    """
    Cronjob: Quét và dọn các file rác trong temp_uploads do server crash để lại (cũ hơn 1 giờ).
    """
    import os
    import time
    
    temp_dir = "temp_uploads"
    if not os.path.exists(temp_dir):
        return
        
    cutoff_time = time.time() - 3600  # 1 hour ago
    
    for filename in os.listdir(temp_dir):
        file_path = os.path.join(temp_dir, filename)
        if os.path.isfile(file_path):
            if os.path.getmtime(file_path) < cutoff_time:
                try:
                    os.remove(file_path)
                    logger.info(f"Cronjob: Cleaned up orphaned temp file {filename}")
                except Exception as e:
                    logger.debug(f"Cronjob: Failed to clean {filename}: {e}")


# ---------------------------------------------------------------------------
# Embedding migration: re-embed every wiki page with a new model
# ---------------------------------------------------------------------------

async def reembed_all_pages_task(ctx: dict, job_id: str) -> None:
    """
    Re-embed every wiki page using the model spec referenced by the job.

    On success, atomically flips `app_config.active_embedding_model_spec_id`
    to the new spec — search keeps using the OLD model until that flip lands,
    so there is no zero-result window during the migration.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.ai.embedding_catalog import get_spec
    from app.ai.registry import ProviderRegistry
    from app.database import async_session_factory
    from app.database.models import EmbeddingJob, WikiPage
    from app.services.config_service import (
        ACTIVE_EMBEDDING_MODEL_KEY,
        ConfigService,
    )
    from app.services.embedding_storage import (
        cleanup_stale_embeddings,
        compute_content_hash,
        embedding_input_text,
        upsert_page_embedding,
    )

    job_uuid = uuid.UUID(job_id)
    BATCH = 50

    async with async_session_factory() as session:
        job = await session.get(EmbeddingJob, job_uuid)
        if job is None:
            logger.error(f"reembed: job {job_id} not found")
            return
        if job.status not in ("pending", "running"):
            logger.info(f"reembed: job {job_id} status={job.status}, skipping")
            return

        try:
            spec = get_spec(job.model_spec_id)
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Unknown model spec: {e}"
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            return

        # Provision a provider bound to the NEW spec (not the active one).
        registry = ProviderRegistry(session)
        try:
            provider = await registry.get_embedding(
                task="document", spec_id=spec.id
            )
        except Exception as e:
            job.status = "failed"
            job.error_message = f"Provider init failed: {e}"
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            return

        # Count work and mark running.
        total = (
            await session.execute(
                select(WikiPage.id).where(
                    WikiPage.slug.notin_(["_index", "_log"])
                )
            )
        ).scalars().all()
        job.total_pages = len(total)
        job.done_pages = 0
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info(
        f"reembed: starting job {job_id} model={spec.id} dim={spec.dimension} "
        f"total={len(total)}"
    )

    # Process batches in independent sessions so progress is visible to UI poll.
    for offset in range(0, len(total), BATCH):
        batch_ids = total[offset : offset + BATCH]
        async with async_session_factory() as session:
            # Re-check cancellation flag.
            job = await session.get(EmbeddingJob, job_uuid)
            if job is None or job.status == "cancelled":
                logger.info(f"reembed: job {job_id} cancelled at offset={offset}")
                return

            pages = (
                await session.execute(
                    select(WikiPage).where(WikiPage.id.in_(batch_ids))
                )
            ).scalars().all()
            inputs = [
                embedding_input_text(p.title, p.summary or "", p.content_md or "")
                for p in pages
            ]
            try:
                vectors = await provider.embed_batch(inputs)
            except Exception as e:
                job.status = "failed"
                job.error_message = f"Embedding API failed: {e}"
                job.finished_at = datetime.now(timezone.utc)
                await session.commit()
                logger.exception(f"reembed: job {job_id} failed at offset={offset}")
                return

            for page, vec in zip(pages, vectors):
                await upsert_page_embedding(
                    session,
                    page_id=page.id,
                    spec=spec,
                    vector=list(vec),
                    content_hash=compute_content_hash(
                        page.title, page.summary or "", page.content_md or ""
                    ),
                )
            job.done_pages = min(offset + len(pages), job.total_pages)
            await session.commit()

    # Atomic flip + cleanup of old model's vectors.
    async with async_session_factory() as session:
        job = await session.get(EmbeddingJob, job_uuid)
        if job is None or job.status == "cancelled":
            return
        svc = ConfigService(session)
        await svc.set(ACTIVE_EMBEDDING_MODEL_KEY, spec.id)
        deleted = await cleanup_stale_embeddings(session, keep_spec_id=spec.id)
        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info(
            f"reembed: job {job_id} complete — flipped to {spec.id}, "
            f"cleaned up {deleted} stale embedding rows"
        )


# ---------------------------------------------------------------------------
# MRP arq tasks
# ---------------------------------------------------------------------------

async def ingest_map_reduce_task(ctx: dict, source_id: str):
    """
    arq task: Phase 0-2 of MRP pipeline (Triage + MAP + REDUCE).

    Reads source.full_text and outline_json (set by ingest_file_task / ingest_url_task),
    runs parallel chunk extraction, entity deduplication, KB reconciliation, and
    produces a Compilation Plan saved to source_compilation_plans.

    If mrp_auto_approve_plan=True → immediately enqueues ingest_refine_task.
    Otherwise → sets source.status='plan_ready' and waits for human approval via API.
    """
    from app.ai.mrp.pipeline import run_mrp_pipeline
    from app.ai.registry import ProviderRegistry
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            logger.warning(f"Source {source_id} not found, it may have been deleted.")
            return
        if not source.full_text:
            raise ValueError(f"Source {source_id} has no full_text — run pre-processing first")

        try:
            source.status = "processing"
            source.progress = 56
            source.progress_message = "Extracting knowledge from document..."
            await session.commit()

            registry = ProviderRegistry(session)

            kt_slug = kt_name = kt_desc = None
            if source.knowledge_type_id:
                kt = await session.get(KnowledgeType, source.knowledge_type_id)
                if kt:
                    kt_slug, kt_name, kt_desc = kt.slug, kt.name, kt.description

            result = await run_mrp_pipeline(
                session=session,
                source=source,
                full_text=source.full_text,
                tracker=tracker,
                registry=registry,
                kt_slug=kt_slug,
                kt_name=kt_name,
                kt_desc=kt_desc,
            )

            if result.get("status") == "plan_ready":
                src = await session.get(Source, sid)
                if src:
                    src.status = "plan_ready"
                    src.progress = 80
                    src.progress_message = "Compilation plan ready — awaiting review"
                    await session.commit()
                logger.info(f"Source {source_id} plan ready: {result.get('plan_id')}")
            elif result.get("status") == "plan_auto_approved":
                logger.info(f"Source {source_id} plan auto-approved, refine task enqueued")
            else:
                logger.info(f"Source {source_id} map-reduce result: {result}")

            return result

        except BaseException as e:
            logger.error(f"MAP-REDUCE failed for {source_id}: {e}")
            error_msg = str(e)[:500]

            async def _mark_error_mr() -> None:
                from app.database import async_session_factory as _sf
                from app.database.models import Source as _Source
                async with _sf() as err_session:
                    src = await err_session.get(_Source, sid)
                    if src:
                        src.status = "error"
                        src.error_message = error_msg
                        src.progress = 0
                        src.progress_message = f"Error: {str(e)[:200]}"
                        await err_session.commit()

            try:
                await asyncio.shield(_mark_error_mr())
            except Exception:
                pass
            raise


async def ingest_refine_task(ctx: dict, source_id: str):
    """
    arq task: Phase 3-5 of MRP pipeline (REFINE + VERIFY + COMMIT).

    Enqueued by either:
    - Plan approval API endpoint (POST /sources/{id}/plan/approve)
    - Auto-approve from ingest_map_reduce_task when mrp_auto_approve_plan=True
    """
    from app.ai.mrp.pipeline import run_refine_pipeline
    from app.ai.registry import ProviderRegistry
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            logger.warning(f"Source {source_id} not found, it may have been deleted.")
            return
        if not source.full_text:
            raise ValueError(f"Source {source_id} has no full_text")

        try:
            source.status = "processing"
            source.progress = 78
            source.progress_message = "Writing wiki pages..."
            await session.commit()

            registry = ProviderRegistry(session)

            kt_slug = kt_name = kt_desc = None
            if source.knowledge_type_id:
                kt = await session.get(KnowledgeType, source.knowledge_type_id)
                if kt:
                    kt_slug, kt_name, kt_desc = kt.slug, kt.name, kt.description

            result = await run_refine_pipeline(
                session=session,
                source=source,
                full_text=source.full_text,
                tracker=tracker,
                registry=registry,
                kt_slug=kt_slug,
                kt_name=kt_name,
                kt_desc=kt_desc,
            )

            logger.success(
                f"Source {source_id} MRP complete: "
                f"+{result.get('pages_created', 0)} created, "
                f"~{result.get('pages_updated', 0)} updated"
            )
            return result

        except BaseException as e:
            logger.error(f"REFINE failed for {source_id}: {e}")
            error_msg = str(e)[:500]

            async def _mark_error_refine() -> None:
                from app.database import async_session_factory as _sf
                from app.database.models import Source as _Source
                async with _sf() as err_session:
                    src = await err_session.get(_Source, sid)
                    if src:
                        src.status = "error"
                        src.error_message = error_msg
                        src.progress = 0
                        src.progress_message = f"Error: {str(e)[:200]}"
                        await err_session.commit()

            try:
                await asyncio.shield(_mark_error_refine())
            except Exception:
                pass
            raise


async def regenerate_plan_task(ctx: dict, source_id: str, user_note: str):
    """
    arq task: re-run KB reconciliation + planning call with reviewer feedback.

    Toggles plan.status: pending_review/rejected → regenerating → pending_review.
    Frontend polls GET /sources/{id}/plan to observe completion.
    """
    from app.ai.mrp.reducer import reconcile_with_kb, run_planning_call
    from app.ai.registry import ProviderRegistry
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source, SourceCompilationPlan

    sid = uuid.UUID(source_id)

    async with async_session_factory() as session:
        from sqlalchemy.orm import selectinload
        source = (await session.execute(
            select(Source)
            .options(selectinload(Source.knowledge_type))
            .where(Source.id == sid)
        )).scalar_one_or_none()
        if not source:
            logger.warning(f"regenerate_plan_task: source {source_id} not found")
            return

        plan = (await session.execute(
            select(SourceCompilationPlan).where(SourceCompilationPlan.source_id == sid)
        )).scalar_one_or_none()
        if not plan:
            logger.warning(f"regenerate_plan_task: no plan for source {source_id}")
            return

        plan_json = plan.plan_json or {}
        canonical_entities = plan_json.get("_entities", [])
        canonical_concepts = plan_json.get("_concepts", [])

        try:
            registry = ProviderRegistry(session)
            llm = await registry.get_llm()
            embedding_provider = None
            try:
                embedding_provider = await registry.get_embedding(task="document")
            except Exception:
                pass

            reconciliation: dict = {}
            if embedding_provider and (canonical_entities or canonical_concepts):
                try:
                    reconciliation = await reconcile_with_kb(
                        session, canonical_entities, canonical_concepts, embedding_provider, source, llm=llm,
                    )
                except Exception as exc:
                    logger.warning(f"regenerate_plan_task: KB reconcile failed: {exc}")

            kt_name = source.knowledge_type.name if source.knowledge_type else None
            kt_desc = source.knowledge_type.description if source.knowledge_type else None
            strategy = source.pipeline_strategy or "standard"

            new_plan_dict = await run_planning_call(
                llm=llm,
                source=source,
                strategy=strategy,
                canonical_entities=canonical_entities,
                canonical_concepts=canonical_concepts,
                reconciliation=reconciliation,
                kt_name=kt_name,
                kt_desc=kt_desc,
                user_note=user_note,
            )

            internal_keys = {
                k: plan_json[k] for k in ("_claims", "_entities", "_concepts") if k in plan_json
            }
            new_plan_dict.update(internal_keys)

            plan.plan_json = new_plan_dict
            plan.status = "pending_review"
            plan.reviewed_by = None
            plan.review_note = None
            plan.reviewed_at = None
            await session.commit()
            logger.success(f"regenerate_plan_task: plan refreshed for source {source_id}")
        except Exception as exc:
            logger.exception(f"regenerate_plan_task failed for {source_id}: {exc}")
            # Restore plan to pending_review so user isn't stuck on 'regenerating'
            plan2 = await session.get(SourceCompilationPlan, plan.id)
            if plan2 and plan2.status == "regenerating":
                plan2.status = "pending_review"
                plan2.review_note = f"Regeneration failed: {str(exc)[:200]}"
                await session.commit()


async def caption_images_task(ctx: dict, source_id: str):
    """
    arq task: vision-caption all SourceImage rows for a source.

    Runs independently from the MRP pipeline — enqueued by ingest_file_task
    immediately after images are persisted to DB. Updates each row's caption
    field as soon as the vision call returns, so captions are available by the
    time ingest_refine_task writes wiki pages.

    Each image opens its own DB session for the UPDATE so concurrent coroutines
    never share session state.
    """
    from sqlalchemy import update as sa_update

    from app.ai.registry import ProviderRegistry
    from app.database import async_session_factory
    from app.database.models import Source, SourceImage
    from app.services.storage_service import storage_service

    sid = uuid.UUID(source_id)

    # Load vision provider and image rows in a short-lived session, then close it.
    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            logger.warning(f"caption_images_task: source {source_id} not found")
            return

        registry = ProviderRegistry(session)
        vision_provider = await registry.get_vision()
        if not vision_provider:
            logger.info(f"caption_images_task: no vision provider configured, skipping")
            return

        rows = (await session.execute(
            select(SourceImage).where(SourceImage.source_id == sid)
        )).scalars().all()

        # Snapshot only the fields we need — session closes after this block.
        image_records = [(row.id, row.minio_key, row.content_type) for row in rows]

    if not image_records:
        return

    logger.info(f"caption_images_task: captioning {len(image_records)} images for {source_id}")

    MAX_CONCURRENCY = 4
    PER_IMAGE_TIMEOUT = 120
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    total = len(image_records)

    async def _caption_one(image_id, minio_key: str, content_type: str, idx: int) -> None:
        async with sem:
            try:
                img_bytes = storage_service.download_file(minio_key)
                vision_prompt = (
                    "Describe this image concisely in 1-3 sentences. "
                    "Focus on what is shown (diagrams, charts, photos, illustrations) "
                    "and what information it conveys. Be specific — mention key elements, "
                    "labels, numbers, or steps visible in the image. Do not start with "
                    "'Based on the image' or similar filler phrases."
                )
                caption = await asyncio.wait_for(
                    vision_provider.analyze_image(img_bytes, content_type, prompt=vision_prompt),
                    timeout=PER_IMAGE_TIMEOUT,
                )
                # Each image gets its own session — no concurrent session access.
                async with async_session_factory() as upd_session:
                    await upd_session.execute(
                        sa_update(SourceImage).where(SourceImage.id == image_id).values(caption=caption)
                    )
                    await upd_session.commit()
                logger.info(f"caption_images_task: image {idx}/{total} done for {source_id}")
            except Exception as e:
                logger.warning(f"caption_images_task: failed {minio_key}: {type(e).__name__}: {e}")

    await asyncio.gather(*[
        _caption_one(img_id, mkey, ctype, idx)
        for idx, (img_id, mkey, ctype) in enumerate(image_records, 1)
    ])
    logger.success(f"caption_images_task: {total} images processed for {source_id}")

    # Bake captions into source.full_text so MAP-phase LLM sees ![<caption>](image://uuid)
    # instead of the empty ![](image://uuid) marker, then chain into MRP.
    import re

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            return
        rows = (await session.execute(
            select(SourceImage).where(SourceImage.source_id == sid)
        )).scalars().all()
        caption_by_id = {str(r.id): (r.caption or "").replace("\n", " ").strip() for r in rows}

        if source.full_text and caption_by_id:
            def _sub(match: re.Match) -> str:
                uid = match.group(1)
                cap = caption_by_id.get(uid, "")
                return f"![{cap}](image://{uid})"
            # Replace any marker (empty or already-captioned) so re-runs are idempotent.
            new_text = re.sub(r"!\[[^\]]*\]\(image://([0-9a-fA-F-]+)\)", _sub, source.full_text)
            if new_text != source.full_text:
                source.full_text = new_text
                await session.commit()
                logger.info(f"caption_images_task: refreshed full_text with {len(caption_by_id)} captions for {source_id}")

    # Chain into MAP-REDUCE (only now that captions are baked in).
    pool = await get_arq_pool()
    job = await pool.enqueue_job("ingest_map_reduce_task", source_id)
    if job:
        async with async_session_factory() as session:
            source = await session.get(Source, sid)
            if source:
                source.job_id = job.job_id
                source.progress_message = "Extraction queued..."
                await session.commit()
    logger.info(f"caption_images_task: enqueued ingest_map_reduce_task for {source_id}")


class WorkerSettings:
    """arq worker configuration."""

    functions = [
        ingest_file_task,
        ingest_url_task,
        arq_func(caption_images_task, timeout=3600),
        ingest_map_reduce_task,
        ingest_refine_task,
        regenerate_plan_task,
        reembed_all_pages_task,
    ]
    redis_settings = _get_redis_settings()
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout
    max_tries = 3
    retry_delay = 10
    health_check_interval = 30

    @staticmethod
    async def on_startup(ctx: dict):
        logger.info("arq worker started — listening for ingestion jobs...")

    @staticmethod
    async def on_shutdown(ctx: dict):
        logger.info("arq worker shutting down...")


class SkillWorkerSettings:
    """arq worker configuration dedicated to Skills."""

    functions = [ingest_skill_task, delete_skill_task]
    queue_name = "skills_queue"
    redis_settings = _get_redis_settings()
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout
    max_tries = 3
    retry_delay = 10
    health_check_interval = 30
    
    cron_jobs = [
        cron(cleanup_temp_uploads_cron, minute=0)
    ]

    @staticmethod
    async def on_startup(ctx: dict):
        logger.info("arq skills worker started — listening for skill jobs...")

    @staticmethod
    async def on_shutdown(ctx: dict):
        logger.info("arq skills worker shutting down...")
