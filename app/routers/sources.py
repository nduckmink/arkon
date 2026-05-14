"""Sources router — CRUD + upload + arq ingestion pipeline (compiles into wiki).

Permission model v2:
  - doc:read:own_dept → only own department + global docs
  - doc:read:all → all docs
  - Upload creates source_departments M2M entries
"""

import uuid
from typing import Optional

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import Employee, ScopeType, Source, SourceDepartment, WikiPage
from app.database.repository import Repository
from app.services.audit_service import log_audit
from app.services.auth_service import (
    get_current_user,
    require_permission,
)
from app.services.permission_engine import (
    _get_user_permissions,
    get_scope_level,
)

router = APIRouter()

_arq_pool: Optional[ArqRedis] = None


async def get_arq_pool() -> ArqRedis:
    """Lazy-init arq Redis connection pool."""
    global _arq_pool
    if _arq_pool is None:
        from app.worker import _get_redis_settings
        _arq_pool = await create_pool(_get_redis_settings())
    return _arq_pool


class SourceResponse(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    source_type: Optional[str]
    file_name: Optional[str]
    url: Optional[str]
    status: str
    error_message: Optional[str] = None
    progress: int = 0
    progress_message: Optional[str] = None
    job_id: Optional[str] = None
    page_count: int = 0
    wiki_page_count: int = 0
    knowledge_type_id: Optional[uuid.UUID] = None
    knowledge_type_name: Optional[str] = None
    knowledge_type_color: Optional[str] = None
    # Multi-department (v2)
    department_ids: list[str] = []
    department_names: list[str] = []
    contributed_by_employee_id: Optional[uuid.UUID] = None
    contributed_by_name: Optional[str] = None
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class SourceDetail(SourceResponse):
    full_text: Optional[str] = None
    outline: Optional[list] = None
    download_url: Optional[str] = None


class SourceCreateURL(BaseModel):
    url: str
    title: Optional[str] = None
    knowledge_type_id: Optional[uuid.UUID] = None
    department_ids: list[uuid.UUID] = []


class SourceUpdate(BaseModel):
    title: Optional[str] = None
    knowledge_type_id: Optional[uuid.UUID] = None
    department_ids: Optional[list[uuid.UUID]] = None
    scope_type: Optional[str] = None
    scope_id: Optional[uuid.UUID] = None


async def _wiki_page_count(session: AsyncSession, source_id: uuid.UUID) -> int:
    """How many wiki pages reference this source in their source_ids array."""
    stmt = select(func.count()).select_from(WikiPage).where(WikiPage.source_ids.any(source_id))  # type: ignore[arg-type]
    return (await session.execute(stmt)).scalar_one()


def _to_response(source: Source, wiki_page_count: int = 0) -> SourceResponse:
    # Extract departments from M2M relationship
    dept_ids = []
    dept_names = []
    if hasattr(source, 'departments') and source.departments:
        for sd in source.departments:
            dept_ids.append(str(sd.department_id))
            if hasattr(sd, 'department') and sd.department:
                dept_names.append(sd.department.name)

    return SourceResponse(
        id=source.id,
        title=source.title,
        source_type=source.source_type,
        file_name=source.file_name,
        url=source.url,
        status=source.status,
        error_message=source.error_message,
        progress=source.progress,
        progress_message=source.progress_message,
        job_id=source.job_id,
        page_count=len(source.page_offsets or []),
        wiki_page_count=wiki_page_count,
        knowledge_type_id=source.knowledge_type_id,
        knowledge_type_name=source.knowledge_type.name if source.knowledge_type else None,
        knowledge_type_color=source.knowledge_type.color if source.knowledge_type else None,
        department_ids=dept_ids,
        department_names=dept_names,
        contributed_by_employee_id=source.contributed_by_employee_id,
        contributed_by_name=source.contributor.name if source.contributor else None,
        scope_type=source.scope_type or "global",
        scope_id=source.scope_id,
        created_at=source.created_at.isoformat(),
        updated_at=source.updated_at.isoformat(),
    )


def _source_load_options():
    """Common selectinload options for Source queries."""
    return [
        selectinload(Source.knowledge_type),
        selectinload(Source.departments).selectinload(SourceDepartment.department),
        selectinload(Source.contributor),
    ]


@router.get("/sources")
async def list_sources(
    knowledge_type_id: Optional[uuid.UUID] = Query(None),
    department_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List sources with scoped filtering based on user permissions."""
    # Check user has at least some doc:read permission
    perms = _get_user_permissions(user)
    if user.role != "admin" and not any(p.startswith("doc:read:") for p in perms):
        raise HTTPException(403, "Permission required: doc:read")

    base = select(Source).options(*_source_load_options())
    count_base = select(func.count(Source.id))

    # --- Scope filtering ---
    scope_level = "all" if user.role == "admin" else get_scope_level(list(perms), "doc", "read")

    if scope_level == "own_dept":
        # Only show: global docs (no departments) OR docs in user's department
        dept_filter = or_(
            # Source has no departments → global
            ~exists(
                select(SourceDepartment.source_id)
                .where(SourceDepartment.source_id == Source.id)
            ),
            # Source has user's department
            exists(
                select(SourceDepartment.source_id)
                .where(
                    SourceDepartment.source_id == Source.id,
                    SourceDepartment.department_id == user.department_id,
                )
            ),
        )
        base = base.where(dept_filter)
        count_base = count_base.where(dept_filter)
    elif scope_level is None:
        # No doc:read permission at all
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1}

    # --- Additional filters ---
    if knowledge_type_id:
        base = base.where(Source.knowledge_type_id == knowledge_type_id)
        count_base = count_base.where(Source.knowledge_type_id == knowledge_type_id)
    if department_id:
        dept_exists = exists(
            select(SourceDepartment.source_id)
            .where(
                SourceDepartment.source_id == Source.id,
                SourceDepartment.department_id == department_id,
            )
        )
        base = base.where(dept_exists)
        count_base = count_base.where(dept_exists)
    if status:
        base = base.where(Source.status == status)
        count_base = count_base.where(Source.status == status)
    if search:
        like = f"%{search}%"
        base = base.where(Source.title.ilike(like) | Source.file_name.ilike(like))
        count_base = count_base.where(Source.title.ilike(like) | Source.file_name.ilike(like))

    total = (await db.execute(count_base)).scalar() or 0

    offset = (max(page, 1) - 1) * page_size
    stmt = base.order_by(Source.created_at.desc()).offset(offset).limit(page_size)
    sources = (await db.execute(stmt)).scalars().all()

    items: list[SourceResponse] = []
    for s in sources:
        items.append(_to_response(s, await _wiki_page_count(db, s.id)))

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


@router.get("/sources/{source_id}")
async def get_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    source = (await db.execute(
        select(Source)
        .options(*_source_load_options())
        .where(Source.id == source_id)
    )).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Check access using permission engine
    from app.services.permission_engine import can_access_document
    if not await can_access_document(db, user, source, "read"):
        raise HTTPException(403, "Access denied")

    wiki_count = await _wiki_page_count(db, source_id)
    download_url = None
    if source.minio_key:
        try:
            from app.services.storage_service import storage_service
            download_url = storage_service.get_presigned_url(source.minio_key)
        except Exception:
            pass

    base = _to_response(source, wiki_count)
    return SourceDetail(
        **base.model_dump(),
        full_text=source.full_text,
        outline=source.outline_json,
        download_url=download_url,
    )


@router.get("/sources/{source_id}/progress")
async def get_source_progress(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:read"),
):
    source = await db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    wiki_count = await _wiki_page_count(db, source_id)
    return {
        "id": str(source.id),
        "status": source.status,
        "progress": source.progress,
        "progress_message": source.progress_message,
        "page_count": len(source.page_offsets or []),
        "wiki_page_count": wiki_count,
    }


@router.post("/sources/upload", response_model=SourceResponse)
async def upload_source(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    knowledge_type_id: Optional[str] = Form(None),
    department_ids: Optional[str] = Form(None),  # comma-separated UUIDs
    scope_type: Optional[str] = Form(None),
    scope_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:create"),
):
    file_data = await file.read()
    file_name = file.filename or "unknown"

    # Parse department_ids
    dept_uuids: list[uuid.UUID] = []
    if department_ids:
        for d in department_ids.split(","):
            d = d.strip()
            if d:
                try:
                    dept_uuids.append(uuid.UUID(d))
                except ValueError:
                    raise HTTPException(400, f"Invalid department_id: {d}")

    # Scope validation: own_dept users can only assign their own department
    perms = _get_user_permissions(user)
    if user.role != "admin" and "doc:create:all" not in perms:
        # User only has doc:create:own_dept
        for did in dept_uuids:
            if did != user.department_id:
                raise HTTPException(403, "You can only assign documents to your own department")

    repo = Repository(db)
    source = Source(
        title=title or file.filename,
        source_type="file",
        file_name=file_name,
        file_size=len(file_data),
        status="pending",
        progress=0,
        progress_message="Queued for ingestion...",
        knowledge_type_id=uuid.UUID(knowledge_type_id) if knowledge_type_id else None,
        contributed_by_employee_id=user.id,
        scope_type=scope_type or ScopeType.GLOBAL.value,
        scope_id=uuid.UUID(scope_id) if scope_id else None,
    )
    source = await repo.create(source)
    await db.flush()

    # Create M2M department links
    for did in dept_uuids:
        db.add(SourceDepartment(source_id=source.id, department_id=did))
    await db.flush()

    await log_audit(db, user, "create", "source", str(source.id), reason=source.title)
    await db.commit()
    await db.refresh(source)

    # Upload to MinIO before enqueuing so the worker downloads from storage
    from app.services.kb_service import _guess_content_type
    from app.services.storage_service import storage_service
    minio_key = f"sources/{source.id}/original/{file_name}"
    storage_service.upload_file(
        object_name=minio_key,
        data=file_data,
        content_type=_guess_content_type(file_name),
    )
    source.minio_key = minio_key
    source.file_name = file_name
    await db.commit()

    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "ingest_file_task", str(source.id),
    )
    if job:
        source.job_id = job.job_id
    await db.commit()

    source = (await db.execute(
        select(Source)
        .options(*_source_load_options())
        .where(Source.id == source.id)
    )).scalar_one()

    logger.info(f"Enqueued ingestion job {job.job_id if job else 'N/A'} for source {source.id}")
    return _to_response(source)


@router.post("/sources/url", response_model=SourceResponse)
async def add_url_source(
    req: SourceCreateURL,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:create"),
):
    repo = Repository(db)
    source = Source(
        title=req.title or req.url,
        source_type="url",
        url=req.url,
        status="pending",
        progress=0,
        progress_message="Queued for ingestion...",
        knowledge_type_id=req.knowledge_type_id,
        contributed_by_employee_id=user.id,
        scope_type=ScopeType.GLOBAL.value,
    )
    source = await repo.create(source)
    await db.flush()

    # Create M2M department links
    for did in req.department_ids:
        db.add(SourceDepartment(source_id=source.id, department_id=did))
    await db.flush()

    await log_audit(db, user, "create", "source", str(source.id), reason=source.title)
    await db.commit()
    await db.refresh(source)

    pool = await get_arq_pool()
    job = await pool.enqueue_job("ingest_url_task", str(source.id))
    if job:
        source.job_id = job.job_id
    await db.commit()

    source = (await db.execute(
        select(Source)
        .options(*_source_load_options())
        .where(Source.id == source.id)
    )).scalar_one()

    logger.info(f"Enqueued URL ingestion job {job.job_id if job else 'N/A'} for source {source.id}")
    return _to_response(source)


@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:edit"),
):
    from app.services import wiki_service

    source = await db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if body.title is not None:
        source.title = body.title
    if body.knowledge_type_id is not None:
        source.knowledge_type_id = body.knowledge_type_id
    if body.scope_type is not None:
        source.scope_type = body.scope_type
        source.scope_id = body.scope_id

    # Detect department changes and trigger re-ingestion when needed
    dept_changed = False
    if body.department_ids is not None:
        # Permission check: own_dept users may only assign their own department
        perms = _get_user_permissions(_user)
        if _user.role != "admin" and "doc:edit:all" not in perms:
            for did in body.department_ids:
                if did != _user.department_id:
                    raise HTTPException(403, "You can only assign documents to your own department")

        old_dept_rows = (await db.execute(
            select(SourceDepartment.department_id).where(SourceDepartment.source_id == source_id)
        )).scalars().all()
        old_dept_ids = set(old_dept_rows)
        new_dept_ids = set(body.department_ids)

        if old_dept_ids != new_dept_ids and source.status == "ready":
            dept_changed = True

            # Snapshot old scopes before detaching so we can regenerate their indexes
            from app.ai.mrp.pipeline import _resolve_wiki_scopes
            old_scopes = await _resolve_wiki_scopes(db, source)

            # Detach source from wiki pages in old scopes
            await wiki_service.detach_source_from_wiki(db, source.id)

            # Regenerate index for each old scope after detach
            for st, sid in old_scopes:
                await wiki_service.regenerate_index(db, scope_type=st, scope_id=sid)

        # Replace M2M rows
        await db.execute(
            sql_delete(SourceDepartment).where(SourceDepartment.source_id == source_id)
        )
        for did in body.department_ids:
            db.add(SourceDepartment(source_id=source_id, department_id=did))

    await log_audit(db, _user, "update", "source", str(source.id), reason=source.title)
    await db.flush()

    if dept_changed:
        source.status = "processing"
        source.progress = 0
        source.progress_message = "Re-queued after department change..."
        source.error_message = None
        await db.flush()

        pool = await get_arq_pool()
        job = await pool.enqueue_job("ingest_map_reduce_task", str(source_id))
        if job:
            source.job_id = job.job_id

    await db.commit()

    source = (await db.execute(
        select(Source)
        .options(*_source_load_options())
        .where(Source.id == source_id)
    )).scalar_one()
    return _to_response(source, await _wiki_page_count(db, source_id))


@router.post("/sources/{source_id}/retry", response_model=SourceResponse)
async def retry_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:edit"),
):
    """
    Retry ingestion for a source whose previous attempt failed.

    Only allowed when the source is in `error` status — successful sources
    cannot be re-ingested.
    """
    source = (await db.execute(
        select(Source)
        .options(*_source_load_options())
        .where(Source.id == source_id)
    )).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    allowed_statuses = ("error", "plan_ready")
    if source.status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Retry is only allowed for sources in {allowed_statuses} status",
        )
    if source.source_type == "url" and not source.url:
        raise HTTPException(status_code=400, detail="Source has no URL to retry")
    if source.source_type == "file" and not source.minio_key:
        raise HTTPException(status_code=400, detail="Source file not found in storage")

    source.status = "pending"
    source.progress = 0
    source.progress_message = "Queued for retry..."
    source.error_message = None
    await db.flush()

    pool = await get_arq_pool()
    # Route to the right task based on pipeline phase
    pipeline_phase = source.pipeline_phase
    if pipeline_phase in ("refine", "verify", "commit"):
        task_name = "ingest_refine_task"
    elif pipeline_phase in ("map", "reduce", "plan_review") or source.status == "plan_ready":
        task_name = "ingest_map_reduce_task"
    else:
        task_name = "ingest_url_task" if source.source_type == "url" else "ingest_file_task"
    job = await pool.enqueue_job(task_name, str(source_id))

    if job:
        source.job_id = job.job_id
    await db.commit()
    await db.refresh(source)

    source = (await db.execute(
        select(Source)
        .options(*_source_load_options())
        .where(Source.id == source_id)
    )).scalar_one()
    logger.info(f"Queued retry job {job.job_id if job else 'N/A'} for source {source_id}")
    return _to_response(source)


# ---------------------------------------------------------------------------
# Compilation Plan review endpoints (MRP Phase 2.5)
# ---------------------------------------------------------------------------

class PlanApproveRequest(BaseModel):
    note: Optional[str] = None


class PlanRejectRequest(BaseModel):
    note: str


class PlanRegenerateRequest(BaseModel):
    note: str


@router.get("/sources/{source_id}/plan")
async def get_compilation_plan(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:read"),
):
    """Return the current compilation plan for a source (MRP Phase 2.5)."""
    from app.database.models import SourceCompilationPlan
    plan = (await db.execute(
        select(SourceCompilationPlan).where(SourceCompilationPlan.source_id == source_id)
    )).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="No compilation plan found for this source")

    plan_json = dict(plan.plan_json or {})
    # Strip internal keys before returning
    plan_json.pop("_claims", None)
    plan_json.pop("_entities", None)
    plan_json.pop("_concepts", None)
    plan_json.pop("_page_drafts", None)

    return {
        "id": str(plan.id),
        "source_id": str(plan.source_id),
        "status": plan.status,
        "plan": plan_json,
        "created_at": plan.created_at.isoformat(),
        "reviewed_at": plan.reviewed_at.isoformat() if plan.reviewed_at else None,
        "review_note": plan.review_note,
    }


@router.post("/sources/{source_id}/plan/approve")
async def approve_compilation_plan(
    source_id: uuid.UUID,
    body: PlanApproveRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:edit"),
):
    """Approve (and optionally modify) the compilation plan, then enqueue REFINE task."""
    from datetime import datetime, timezone

    from app.database.models import SourceCompilationPlan

    plan = (await db.execute(
        select(SourceCompilationPlan)
        .where(SourceCompilationPlan.source_id == source_id)
        .with_for_update()
    )).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="No plan found for this source")
    if plan.status == "regenerating":
        raise HTTPException(
            status_code=409,
            detail="Plan is being regenerated. Wait for it to finish before approving.",
        )
    if plan.status != "pending_review":
        raise HTTPException(
            status_code=400,
            detail=f"Plan is not pending review (status={plan.status})",
        )

    plan.status = "approved"
    plan.reviewed_by = user.id
    plan.review_note = body.note
    plan.reviewed_at = datetime.now(timezone.utc)
    await log_audit(db, user, "approve", "compilation_plan", str(plan.id), reason=body.note or None)

    source = await db.get(Source, source_id)
    if source:
        source.status = "processing"
        source.progress = 78
        source.progress_message = "Plan approved — compiling wiki pages..."

    await db.flush()

    pool = await get_arq_pool()
    job = await pool.enqueue_job("ingest_refine_task", str(source_id))

    if job and source:
        source.job_id = job.job_id
    await db.commit()

    logger.info(f"Plan approved for source {source_id} by user {user.id}, refine job: {job.job_id if job else 'N/A'}")
    return {"approved": True, "job_id": job.job_id if job else None}


@router.post("/sources/{source_id}/plan/regenerate")
async def regenerate_compilation_plan(
    source_id: uuid.UUID,
    body: PlanRegenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:edit"),
):
    """
    Enqueue a background task to re-run planning with reviewer feedback.

    Plan status transitions: pending_review/rejected → regenerating → pending_review.
    Frontend should poll GET /sources/{id}/plan to detect completion (status flips
    back to pending_review and plan content updates).
    """
    from app.database.models import SourceCompilationPlan

    if not body.note.strip():
        raise HTTPException(status_code=400, detail="Note is required to regenerate plan")

    # SELECT FOR UPDATE — atomic state transition, prevents concurrent regenerate/approve.
    plan = (await db.execute(
        select(SourceCompilationPlan)
        .where(SourceCompilationPlan.source_id == source_id)
        .with_for_update()
    )).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="No plan found for this source")
    if plan.status not in ("pending_review", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=f"Plan cannot be regenerated (status={plan.status})",
        )

    plan.status = "regenerating"
    plan.review_note = body.note[:1000]
    await log_audit(db, user, "regenerate", "compilation_plan", str(plan.id), reason=body.note[:200])
    await db.commit()

    pool = await get_arq_pool()
    job = await pool.enqueue_job("regenerate_plan_task", str(source_id), body.note)

    logger.info(f"Plan regenerate queued for source {source_id} by user {user.id}, job: {job.job_id if job else 'N/A'}")
    return {
        "queued": True,
        "status": plan.status,
        "job_id": job.job_id if job else None,
    }


@router.post("/sources/{source_id}/plan/reject")
async def reject_compilation_plan(
    source_id: uuid.UUID,
    body: PlanRejectRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("doc:edit"),
):
    """Reject the compilation plan. Source moves to error status."""
    from datetime import datetime, timezone

    from app.database.models import SourceCompilationPlan

    plan = (await db.execute(
        select(SourceCompilationPlan)
        .where(SourceCompilationPlan.source_id == source_id)
        .with_for_update()
    )).scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="No plan found for this source")
    if plan.status == "regenerating":
        raise HTTPException(
            status_code=409,
            detail="Plan is being regenerated. Wait for it to finish before rejecting.",
        )
    if plan.status != "pending_review":
        raise HTTPException(
            status_code=400,
            detail=f"Plan is not pending review (status={plan.status})",
        )

    plan.status = "rejected"
    plan.reviewed_by = user.id
    plan.review_note = body.note
    plan.reviewed_at = datetime.now(timezone.utc)
    await log_audit(db, user, "reject", "compilation_plan", str(plan.id), reason=body.note)

    source = await db.get(Source, source_id)
    if source:
        source.status = "error"
        source.error_message = f"Compilation plan rejected: {body.note}"

    await db.commit()
    logger.info(f"Plan rejected for source {source_id} by user {user.id}: {body.note}")
    return {"rejected": True}


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:delete"),
):
    repo = Repository(db)
    source = await repo.get_by_id(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        from app.services.storage_service import storage_service
        storage_service.delete_prefix(f"sources/{source_id}/")
    except Exception as e:
        logger.warning(f"Failed to clean MinIO files for source {source_id}: {e}")

    # Detach from wiki — single-source pages are deleted, then rebuild index.
    from app.services import wiki_service
    await wiki_service.detach_source_from_wiki(db, source_id)
    await wiki_service.regenerate_index(
        db,
        scope_type=source.scope_type or "global",
        scope_id=source.scope_id,
    )

    await log_audit(db, _user, "delete", "source", str(source.id), reason=source.title)
    await repo.delete_by_id(Source, source_id)
    return {"deleted": True}
