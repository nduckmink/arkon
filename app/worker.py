"""
arq Worker — async Redis queue for document ingestion.

The worker now compiles each source into the LLM Wiki (markdown pages stored
in PostgreSQL) instead of producing chunk embeddings. See app/ai/wiki_compiler.py.

Start with:
    arq app.worker.WorkerSettings
"""

import uuid

from arq.connections import RedisSettings
from loguru import logger

from app.config import settings


def _get_redis_settings() -> RedisSettings:
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_db,
        password=settings.redis_password or None,
    )


# ---------------------------------------------------------------------------
# Progress helper
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Updates source.progress + source.progress_message in DB."""

    def __init__(self, source_id: uuid.UUID):
        self.source_id = source_id

    async def update(self, progress: int, message: str):
        from app.database import async_session_factory
        from app.database.models import Source
        async with async_session_factory() as session:
            source = await session.get(Source, self.source_id)
            if source:
                source.progress = progress
                source.progress_message = message
                await session.commit()
        logger.debug(f"[{self.source_id}] Progress: {progress}% — {message}")


# ---------------------------------------------------------------------------
# Ingestion tasks
# ---------------------------------------------------------------------------

async def ingest_file_task(ctx: dict, source_id: str, file_data: bytes, file_name: str):
    """
    arq task: full file ingestion → wiki compilation.
    Steps: upload → extract text → vision captions → outline → compile wiki.
    """
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source
    from app.ai.registry import ProviderRegistry
    from app.ai.wiki_compiler import compile_source_into_wiki
    from app.services.image_service import extract_images
    from app.services.source_outline import assemble_full_text, build_outline
    from app.services.storage_service import storage_service
    from app.services.kb_service import (
        _extract_text_from_file,
        _guess_content_type,
        _inline_image_captions,
    )

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            raise ValueError(f"Source {source_id} not found")

        try:
            source.status = "processing"
            source.progress = 0
            source.progress_message = "Bắt đầu xử lý..."
            await session.commit()

            # --- Step 1: Upload to MinIO (10%) ---
            await tracker.update(5, "Đang tải lên...")
            minio_key = f"sources/{source_id}/original/{file_name}"
            storage_service.upload_file(
                object_name=minio_key,
                data=file_data,
                content_type=_guess_content_type(file_name),
            )
            source.minio_key = minio_key
            source.file_name = file_name
            source.file_size = len(file_data)
            await session.commit()
            await tracker.update(10, "Tải lên hoàn tất")

            # --- Step 2: Extract text per page (25%) ---
            await tracker.update(15, "Đang trích xuất văn bản (theo trang)...")
            pages_data = await _extract_text_from_file(file_data, file_name)

            if not pages_data or not any((p.get("content") or "").strip() for p in pages_data):
                source.status = "error"
                source.error_message = "Không thể trích xuất nội dung văn bản"
                source.progress = 0
                await session.commit()
                return {"status": "error", "message": "No text content"}

            await tracker.update(25, "Trích xuất văn bản hoàn tất")

            # --- Step 3: Extract images + vision captions (40%) ---
            await tracker.update(30, "Extracting and analyzing images...")
            images = extract_images(file_data, file_name, source_id)

            registry = ProviderRegistry(session)
            vision_provider = await registry.get_vision()
            if vision_provider and images:
                for idx, img in enumerate(images, 1):
                    try:
                        if idx % 5 == 0 or idx == 1 or idx == len(images):
                            logger.info(f"Vision AI analyzing image {idx}/{len(images)}...")
                        img_bytes = storage_service.download_file(img.minio_key)
                        mime_type = "image/png" if img.minio_key.lower().endswith(".png") else "image/jpeg"
                        img.caption = await vision_provider.analyze_image(img_bytes, mime_type)
                    except Exception as e:
                        logger.warning(f"Failed to analyze image {img.minio_key}: {e}")
            elif images:
                logger.info("No vision provider configured, skipping image captioning")

            # Inline captions into per-page text so the compiler sees them.
            _inline_image_captions(pages_data, images)
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

            # --- Step 6: Compile into wiki (95%) ---
            await tracker.update(55, "Compiling into wiki (LLM)...")
            result = await compile_source_into_wiki(
                session=session,
                source=source,
                full_text=full_text,
                knowledge_type_slug=kt_slug,
                knowledge_type_name=kt_name,
                knowledge_type_description=kt_desc,
            )
            await session.commit()
            await tracker.update(
                95,
                f"Wiki: +{result['pages_created']} pages, ~{result['pages_updated']} updated",
            )

            # --- Done (100%) ---
            source.status = "ready"
            source.progress = 100
            source.progress_message = "Hoàn tất"
            source.error_message = None
            await session.commit()

            logger.success(
                f"Source {source_id} ingested: {len(images)} images, "
                f"+{result['pages_created']} pages, ~{result['pages_updated']} updated"
            )
            return {
                "status": "ready",
                "images": len(images),
                "pages_created": result["pages_created"],
                "pages_updated": result["pages_updated"],
            }

        except Exception as e:
            logger.error(f"Ingestion failed for {source_id}: {e}")
            source.status = "error"
            source.error_message = str(e)[:500]
            source.progress = 0
            source.progress_message = f"Lỗi: {str(e)[:200]}"
            await session.commit()
            raise


async def ingest_url_task(ctx: dict, source_id: str):
    """arq task: URL ingestion → wiki compilation."""
    from app.database import async_session_factory
    from app.database.models import KnowledgeType, Source
    from app.ai.wiki_compiler import compile_source_into_wiki
    from app.services.kb_service import _extract_text_from_url
    from app.services.source_outline import assemble_full_text, build_outline

    sid = uuid.UUID(source_id)
    tracker = ProgressTracker(sid)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source:
            raise ValueError(f"Source {source_id} not found")

        try:
            source.status = "processing"
            source.progress = 0
            await session.commit()

            await tracker.update(15, "Đang tải nội dung từ URL...")
            pages_data = await _extract_text_from_url(source.url)

            if not pages_data or not any((p.get("content") or "").strip() for p in pages_data):
                source.status = "error"
                source.error_message = "Không thể tải nội dung từ URL"
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

            await tracker.update(55, "Compiling into wiki (LLM)...")
            result = await compile_source_into_wiki(
                session=session,
                source=source,
                full_text=full_text,
                knowledge_type_slug=kt_slug,
                knowledge_type_name=kt_name,
                knowledge_type_description=kt_desc,
            )
            await session.commit()

            source.status = "ready"
            source.progress = 100
            source.progress_message = "Hoàn tất"
            source.error_message = None
            await session.commit()

            logger.success(
                f"URL source {source_id} ingested: "
                f"+{result['pages_created']} pages, ~{result['pages_updated']} updated"
            )
            return {
                "status": "ready",
                "pages_created": result["pages_created"],
                "pages_updated": result["pages_updated"],
            }

        except Exception as e:
            logger.error(f"URL ingestion failed for {source_id}: {e}")
            source.status = "error"
            source.error_message = str(e)[:500]
            source.progress = 0
            await session.commit()
            raise


async def reingest_file_task(ctx: dict, source_id: str, force: bool = False):
    """
    arq task: re-ingest a file already stored in MinIO.

    If `force=True`, detach this source from all wiki pages first (orphan
    pages get deleted). Otherwise the compiler will merge new ops on top of
    the existing wiki state.
    """
    from app.database import async_session_factory
    from app.database.models import Source
    from app.services.storage_service import storage_service
    from app.services import wiki_service

    sid = uuid.UUID(source_id)

    async with async_session_factory() as session:
        source = await session.get(Source, sid)
        if not source or not source.minio_key:
            raise ValueError(f"Source {source_id} not found or has no file")

        if force:
            await wiki_service.detach_source_from_wiki(session, sid)
            await wiki_service.regenerate_index(session)
            await session.commit()

        file_data = storage_service.download_file(source.minio_key)
        file_name = source.file_name or source.minio_key.split("/")[-1]

    await ingest_file_task(ctx, source_id, file_data, file_name)


# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------

class WorkerSettings:
    """arq worker configuration."""

    functions = [ingest_file_task, ingest_url_task, reingest_file_task]
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
