"""
MRP Pipeline orchestrator.

Two entry points:
  run_mrp_pipeline()     — Phase 0-2 (MAP + REDUCE). Ends at plan_review status.
                           If mrp_auto_approve_plan=True, immediately enqueues
                           ingest_refine_task; otherwise waits for human approval.

  run_refine_pipeline()  — Phase 3-5 (REFINE + VERIFY + COMMIT).
                           Called from ingest_refine_task after plan is approved.

Phase 5 (COMMIT) is implemented inline here. It reuses existing wiki_service
functions (apply_create / apply_update) and embedding_storage utilities.
"""

import asyncio
import uuid
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.mrp.mapper import run_map_phase
from app.ai.mrp.reducer import run_reduce_phase
from app.ai.mrp.verifier import run_verify_phase
from app.ai.mrp.writer import PageWriteResult, run_refine_phase
from app.utils.progress import ProgressTracker


async def _resolve_wiki_scopes(session: AsyncSession, source) -> list[tuple[str, Optional[uuid.UUID]]]:
    """Return the list of (scope_type, scope_id) tuples to commit wiki pages into.

    Project scope takes priority. If source has department assignments, one scope
    per department. Falls back to global.
    """
    from app.database.models import SourceDepartment
    if source.scope_type == "project":
        return [("project", source.scope_id)]
    rows = (await session.execute(
        select(SourceDepartment.department_id).where(SourceDepartment.source_id == source.id)
    )).all()
    dept_ids = [r[0] for r in rows]
    if dept_ids:
        return [("department", did) for did in dept_ids]
    return [("global", None)]


# ---------------------------------------------------------------------------
# Phase 5 — COMMIT
# ---------------------------------------------------------------------------

async def run_commit_phase(
    session: AsyncSession,
    source,
    page_results: list[PageWriteResult],
    plan,
    embedding_provider,
    embedding_spec,
    kt_slug: Optional[str],
    tracker: ProgressTracker,
) -> dict:
    """
    Write all pages to the DB atomically and update embeddings.

    Uses apply_create / apply_update from wiki_service (idempotent via upsert
    fallback). All pages are flushed then committed in a single transaction.
    """
    from app.ai.mrp.merger import merge_page_content
    from app.database.models import Source, SourceCompilationPlan
    from app.services import wiki_service
    from app.services.embedding_storage import (
        compute_content_hash,
        embedding_input_text,
        upsert_page_embedding,
    )

    wiki_scopes = await _resolve_wiki_scopes(session, source)

    total_created = 0
    total_updated = 0

    # Provision LLM for merge operations
    from app.ai.registry import ProviderRegistry
    merge_llm = None
    try:
        merge_registry = ProviderRegistry(session)
        merge_llm = await merge_registry.get_llm()
    except Exception as exc:
        logger.warning(f"MRP COMMIT: could not load LLM for merge: {exc}")

    await tracker.update(95, f"Committing {len(page_results)} pages to wiki ({len(wiki_scopes)} scope(s))...")

    for scope_type, scope_id in wiki_scopes:
        pages_created = 0
        pages_updated = 0

        for pr in page_results:
            try:
                # Acquire advisory lock per (slug, scope) to prevent race conditions
                from sqlalchemy import select, func
                lock_key = func.hashtext(f"{pr.slug}:{scope_type}:{scope_id}")
                await session.execute(select(func.pg_advisory_xact_lock(lock_key)))

                # Reset action per scope — each scope processes independently
                action = pr.action

                if action == "CREATE":
                    # Check if already created in this scope by a concurrent pipeline
                    existing = await wiki_service.get_page_by_slug(
                        session, pr.slug, scope_type=scope_type, scope_id=scope_id
                    )
                    if existing is not None:
                        action = "UPDATE"
                    else:
                        page = await wiki_service.apply_create(
                            session,
                            slug=pr.slug,
                            title=pr.title,
                            page_type=pr.page_type,
                            content_md=pr.content_md,
                            summary=pr.summary,
                            knowledge_type_slugs=[kt_slug] if kt_slug else [],
                            source_ids=[source.id],
                            scope_type=scope_type,
                            scope_id=scope_id,
                        )
                        pages_created += 1

                if action == "UPDATE":
                    existing_page = await wiki_service.get_page_by_slug(
                        session, pr.slug, scope_type=scope_type, scope_id=scope_id,
                    )
                    final_content = pr.content_md

                    if existing_page and existing_page.content_md and merge_llm:
                        existing_sources = set(str(sid) for sid in (existing_page.source_ids or []))
                        is_new_source = str(source.id) not in existing_sources

                        if is_new_source and len(existing_page.content_md.strip()) > 100:
                            final_content = await merge_page_content(
                                merge_llm,
                                existing_page.content_md,
                                pr.content_md,
                                pr.slug,
                            )

                    page = await wiki_service.apply_update(
                        session,
                        slug=pr.slug,
                        new_content_md=final_content,
                        summary=pr.summary,
                        title=pr.title,
                        add_knowledge_type_slug=kt_slug,
                        add_source_id=source.id,
                        scope_type=scope_type,
                        scope_id=scope_id,
                    )
                    if page is None:
                        page = await wiki_service.apply_create(
                            session,
                            slug=pr.slug,
                            title=pr.title,
                            page_type=pr.page_type,
                            content_md=pr.content_md,
                            summary=pr.summary,
                            knowledge_type_slugs=[kt_slug] if kt_slug else [],
                            source_ids=[source.id],
                            scope_type=scope_type,
                            scope_id=scope_id,
                        )
                        pages_created += 1
                    else:
                        pages_updated += 1

                await session.flush()

                if embedding_provider is not None and embedding_spec is not None and page is not None:
                    try:
                        embed_text = embedding_input_text(pr.title, pr.summary, pr.content_md)
                        vector = await embedding_provider.embed(embed_text)
                        content_hash = compute_content_hash(pr.title, pr.summary, pr.content_md)
                        await upsert_page_embedding(session, page.id, embedding_spec, vector, content_hash)
                    except Exception as embed_exc:
                        logger.warning(f"MRP COMMIT embed failed for '{pr.slug}' scope={scope_type}: {embed_exc}")

            except Exception as exc:
                logger.error(f"MRP COMMIT failed for '{pr.slug}' scope={scope_type}: {exc}")

        await wiki_service.regenerate_index(session, scope_type=scope_type, scope_id=scope_id)

        log_entry = (
            f"MRP: ingested '{source.title or source.file_name or str(source.id)}': "
            f"+{pages_created} created, ~{pages_updated} updated"
        )
        await wiki_service.append_log(session, log_entry, scope_type=scope_type, scope_id=scope_id)

        total_created += pages_created
        total_updated += pages_updated

    pages_created = total_created
    pages_updated = total_updated

    # Mark plan and source as done
    if plan is not None:
        plan.status = "done"

    src = await session.get(Source, source.id)
    if src:
        src.pipeline_phase = "commit"
        src.status = "ready"
        src.progress = 100
        src.progress_message = "Done"
        src.error_message = None

    await session.commit()

    logger.success(
        f"MRP COMMIT complete: +{pages_created} created, ~{pages_updated} updated "
        f"for source={source.id}"
    )
    return {"pages_created": pages_created, "pages_updated": pages_updated}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_plan(session: AsyncSession, source_id: uuid.UUID):
    """Load SourceCompilationPlan for a source."""
    from app.database.models import SourceCompilationPlan
    return (await session.execute(
        select(SourceCompilationPlan).where(SourceCompilationPlan.source_id == source_id)
    )).scalar_one_or_none()


async def _load_chunk_extracts(session: AsyncSession, source_id: uuid.UUID) -> list:
    """Load all done SourceChunkExtract rows for a source."""
    from app.database.models import SourceChunkExtract
    rows = (await session.execute(
        select(SourceChunkExtract).where(
            SourceChunkExtract.source_id == source_id,
            SourceChunkExtract.status == "done",
        )
    )).scalars().all()
    return list(rows)


async def _get_embedding_spec(registry):
    """Get the active embedding spec for use in COMMIT. Returns (provider, spec) or (None, None)."""
    try:
        spec_id = await registry.get_active_embedding_spec_id()
        if not spec_id:
            return None, None
        from app.ai.embedding_catalog import get_spec
        spec = get_spec(spec_id)
        provider = await registry.get_embedding(task="document", spec_id=spec_id)
        return provider, spec
    except Exception as exc:
        logger.warning(f"MRP: could not load embedding spec: {exc}")
        return None, None


# ---------------------------------------------------------------------------
# Entry point 1: Phase 0-2
# ---------------------------------------------------------------------------

async def run_mrp_pipeline(
    session: AsyncSession,
    source,
    full_text: str,
    tracker: ProgressTracker,
    registry,
    kt_slug: Optional[str],
    kt_name: Optional[str],
    kt_desc: Optional[str],
) -> dict:
    """
    Orchestrate Phase 0 (Triage) → Phase 1 (MAP) → Phase 2 (REDUCE).

    Saves plan to DB with status 'pending_review'. If mrp_auto_approve_plan=True,
    immediately enqueues ingest_refine_task; otherwise returns {"status": "plan_ready"}.

    Resume: if source.pipeline_phase == 'plan_review' and plan already exists
    (e.g. after a crash in MAP/REDUCE), re-enter at REDUCE rather than re-doing MAP.
    """
    from app.config import settings
    from app.database.models import Source

    source_id = source.id

    # Resume check: if already at plan_review or beyond, don't re-run MAP+REDUCE
    current_phase = source.pipeline_phase
    if current_phase == "plan_review":
        plan = await _load_plan(session, source_id)
        if plan and plan.status in ("pending_review", "approved"):
            logger.info(f"MRP: source={source_id} already at plan_review, skipping MAP+REDUCE")
            if plan.status == "approved" or settings.mrp_auto_approve_plan:
                return await _auto_trigger_refine(source_id, plan)
            return {"status": "plan_ready", "plan_id": str(plan.id)}

    if current_phase in ("refine", "verify", "commit"):
        logger.info(f"MRP: source={source_id} already in {current_phase} phase, skipping")
        return {"status": f"already_in_{current_phase}"}

    # Provision LLM + embedding
    llm = await registry.get_llm()
    embedding_provider = None
    try:
        embedding_provider = await registry.get_embedding(task="document")
    except Exception:
        logger.warning(f"MRP: no embedding provider for source={source_id}")

    # Phase 0 + 1: MAP
    strategy, chunk_extracts = await run_map_phase(
        session=session,
        source_id=source_id,
        full_text=full_text,
        outline_json=source.outline_json,
        tracker=tracker,
        llm=llm,
    )

    if not chunk_extracts:
        raise ValueError(f"MAP phase produced no successful chunks for source={source_id}")

    # Phase 2: REDUCE
    src = await session.get(Source, source_id)
    if src:
        src.pipeline_phase = "reduce"
        await session.commit()

    plan = await run_reduce_phase(
        session=session,
        source=source,
        chunk_extracts=chunk_extracts,
        llm=llm,
        embedding_provider=embedding_provider,
        kt_name=kt_name,
        kt_desc=kt_desc,
        tracker=tracker,
    )

    await tracker.update(80, "Compilation plan ready")

    if settings.mrp_auto_approve_plan:
        return await _auto_trigger_refine(source_id, plan)

    return {"status": "plan_ready", "plan_id": str(plan.id)}


async def _auto_trigger_refine(source_id: uuid.UUID, plan) -> dict:
    """Auto-approve plan and enqueue ingest_refine_task."""
    from datetime import datetime, timezone

    from app.worker import get_arq_pool

    # Mark plan as approved
    try:
        from app.database import async_session_factory
        async with async_session_factory() as sess:
            from app.database.models import Source, SourceCompilationPlan
            p = await sess.get(SourceCompilationPlan, plan.id)
            if p and p.status == "pending_review":
                p.status = "approved"
                p.review_note = "Auto-approved"
                p.reviewed_at = datetime.now(timezone.utc)
            src = await sess.get(Source, source_id)
            if src:
                src.status = "processing"
                src.progress_message = "Plan approved — compiling wiki pages..."
            await sess.commit()
    except Exception as exc:
        logger.warning(f"MRP auto-approve state update failed: {exc}")

    pool = await get_arq_pool()
    job = await pool.enqueue_job("ingest_refine_task", str(source_id))
    return {"status": "plan_auto_approved", "job_id": job.job_id if job else None}


# ---------------------------------------------------------------------------
# Entry point 2: Phase 3-5
# ---------------------------------------------------------------------------

async def run_refine_pipeline(
    session: AsyncSession,
    source,
    full_text: str,
    tracker: ProgressTracker,
    registry,
    kt_slug: Optional[str],
    kt_name: Optional[str],
    kt_desc: Optional[str],
) -> dict:
    """
    Orchestrate Phase 3 (REFINE) → Phase 4 (VERIFY) → Phase 5 (COMMIT).

    Called from ingest_refine_task after the plan is approved.
    Resumes from 'verify' or 'commit' phase if interrupted.
    """
    from app.database.models import Source

    source_id = source.id
    current_phase = source.pipeline_phase

    # Load plan — fail fast if not approved
    plan = await _load_plan(session, source_id)
    if plan is None:
        raise ValueError(f"No compilation plan found for source={source_id}")
    if plan.status not in ("approved", "in_progress", "done"):
        raise ValueError(
            f"Plan for source={source_id} is not approved (status={plan.status}). "
            "Approve the plan before running REFINE."
        )

    # Load chunk extracts (needed for evidence assembly and coverage check)
    chunk_extracts = await _load_chunk_extracts(session, source_id)

    # Provision providers
    llm = await registry.get_llm()
    embedding_provider = None
    embedding_spec = None
    try:
        embedding_provider, embedding_spec = await _get_embedding_spec(registry)
    except Exception:
        pass

    page_results: list[PageWriteResult] = []

    if current_phase not in ("verify", "commit"):
        # Phase 3: REFINE
        src = await session.get(Source, source_id)
        if src:
            src.pipeline_phase = "refine"
        plan.status = "in_progress"
        await session.commit()

        page_results = await run_refine_phase(
            session=session,
            source=source,
            plan=plan,
            chunk_extracts=chunk_extracts,
            full_text=full_text,
            llm=llm,
            embedding_provider=embedding_provider,
            kt_slug=kt_slug,
            tracker=tracker,
        )

        src = await session.get(Source, source_id)
        if src:
            src.pipeline_phase = "verify"
        await session.commit()
    else:
        logger.info(f"MRP: source={source_id} resuming at {current_phase} phase — loading persisted drafts")
        drafts = (plan.plan_json or {}).get("_page_drafts") or []
        if drafts:
            page_results = [PageWriteResult.from_dict(d) for d in drafts]
            logger.info(f"MRP: loaded {len(page_results)} persisted page drafts for source={source_id}")
        else:
            logger.warning(
                f"MRP: source={source_id} at {current_phase} phase but no drafts persisted — re-running REFINE"
            )
            src = await session.get(Source, source_id)
            if src:
                src.pipeline_phase = "refine"
            await session.commit()

            page_results = await run_refine_phase(
                session=session,
                source=source,
                plan=plan,
                chunk_extracts=chunk_extracts,
                full_text=full_text,
                llm=llm,
                embedding_provider=embedding_provider,
                kt_slug=kt_slug,
                tracker=tracker,
            )

        src = await session.get(Source, source_id)
        if src:
            src.pipeline_phase = "verify"
        await session.commit()

    # Phase 4: VERIFY
    page_results = await run_verify_phase(
        session=session,
        source=source,
        page_results=page_results,
        chunk_extracts=chunk_extracts,
        full_text=full_text,
        llm=llm,
        embedding_provider=embedding_provider,
        tracker=tracker,
    )

    # Phase 5: COMMIT
    return await run_commit_phase(
        session=session,
        source=source,
        page_results=page_results,
        plan=plan,
        embedding_provider=embedding_provider,
        embedding_spec=embedding_spec,
        kt_slug=kt_slug,
        tracker=tracker,
    )
