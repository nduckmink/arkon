"""Sources router — CRUD + upload + arq ingestion pipeline (compiles into wiki)."""

import uuid
from typing import Optional

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import Employee, Source, WikiPage
from app.database.repository import Repository
from app.services.auth_service import require_admin, require_permission

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
    department_id: Optional[uuid.UUID] = None
    department_name: Optional[str] = None
    contributed_by_employee_id: Optional[uuid.UUID] = None
    contributed_by_name: Optional[str] = None
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
    department_id: Optional[uuid.UUID] = None


class SourceUpdate(BaseModel):
    title: Optional[str] = None
    knowledge_type_id: Optional[uuid.UUID] = None
    department_id: Optional[uuid.UUID] = None


async def _wiki_page_count(session: AsyncSession, source_id: uuid.UUID) -> int:
    """How many wiki pages reference this source in their source_ids array."""
    stmt = select(func.count()).select_from(WikiPage).where(WikiPage.source_ids.any(source_id))
    return (await session.execute(stmt)).scalar_one()


def _to_response(source: Source, wiki_page_count: int = 0) -> SourceResponse:
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
        department_id=source.department_id,
        department_name=source.department.name if source.department else None,
        contributed_by_employee_id=source.contributed_by_employee_id,
        contributed_by_name=source.contributor.name if source.contributor else None,
        created_at=source.created_at.isoformat(),
        updated_at=source.updated_at.isoformat(),
    )


@router.get("/sources", response_model=list[SourceResponse])
async def list_sources(
    knowledge_type_id: Optional[uuid.UUID] = Query(None),
    department_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    stmt = (
        select(Source)
        .options(
            selectinload(Source.knowledge_type),
            selectinload(Source.department),
            selectinload(Source.contributor),
        )
        .order_by(Source.created_at.desc())
    )
    if knowledge_type_id:
        stmt = stmt.where(Source.knowledge_type_id == knowledge_type_id)
    if department_id:
        stmt = stmt.where(Source.department_id == department_id)
    if status:
        stmt = stmt.where(Source.status == status)

    sources = (await db.execute(stmt)).scalars().all()
    out: list[SourceResponse] = []
    for s in sources:
        out.append(_to_response(s, await _wiki_page_count(db, s.id)))
    return out


@router.get("/sources/{source_id}")
async def get_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    source = (await db.execute(
        select(Source)
        .options(
            selectinload(Source.knowledge_type),
            selectinload(Source.department),
            selectinload(Source.contributor),
        )
        .where(Source.id == source_id)
    )).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

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
    _admin: Employee = Depends(require_admin),
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
    department_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("kb.upload"),
):
    file_data = await file.read()
    repo = Repository(db)
    source = Source(
        title=title or file.filename,
        source_type="file",
        file_name=file.filename,
        file_size=len(file_data),
        status="pending",
        progress=0,
        progress_message="Queued for ingestion...",
        knowledge_type_id=uuid.UUID(knowledge_type_id) if knowledge_type_id else None,
        department_id=uuid.UUID(department_id) if department_id else None,
        contributed_by_employee_id=user.id,
    )
    source = await repo.create(source)
    await db.commit()
    await db.refresh(source)

    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "ingest_file_task", str(source.id), file_data, file.filename or "unknown",
    )
    source.job_id = job.job_id
    await db.commit()

    source = (await db.execute(
        select(Source)
        .options(
            selectinload(Source.knowledge_type),
            selectinload(Source.department),
            selectinload(Source.contributor),
        )
        .where(Source.id == source.id)
    )).scalar_one()

    logger.info(f"Enqueued ingestion job {job.job_id} for source {source.id}")
    return _to_response(source)


@router.post("/sources/url", response_model=SourceResponse)
async def add_url_source(
    req: SourceCreateURL,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("kb.upload"),
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
        department_id=req.department_id,
        contributed_by_employee_id=user.id,
    )
    source = await repo.create(source)
    await db.commit()
    await db.refresh(source)

    pool = await get_arq_pool()
    job = await pool.enqueue_job("ingest_url_task", str(source.id))
    source.job_id = job.job_id
    await db.commit()

    source = (await db.execute(
        select(Source)
        .options(
            selectinload(Source.knowledge_type),
            selectinload(Source.department),
            selectinload(Source.contributor),
        )
        .where(Source.id == source.id)
    )).scalar_one()

    logger.info(f"Enqueued URL ingestion job {job.job_id} for source {source.id}")
    return _to_response(source)


@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("kb.manage"),
):
    source = await db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if body.title is not None:
        source.title = body.title
    if body.knowledge_type_id is not None:
        source.knowledge_type_id = body.knowledge_type_id
    if body.department_id is not None:
        source.department_id = body.department_id
    await db.flush()

    source = (await db.execute(
        select(Source)
        .options(
            selectinload(Source.knowledge_type),
            selectinload(Source.department),
            selectinload(Source.contributor),
        )
        .where(Source.id == source_id)
    )).scalar_one()
    return _to_response(source, await _wiki_page_count(db, source_id))


@router.post("/sources/{source_id}/recompile", response_model=SourceResponse)
async def recompile_source(
    source_id: uuid.UUID,
    force: bool = Query(False, description="If true, detach this source from existing wiki pages first"),
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("kb.manage"),
):
    """
    Re-run the wiki compiler for this source. Without `force`, the compiler
    merges new ops into the existing wiki state. With `force=True`, the
    source is first detached from all wiki pages (orphans deleted) so the
    wiki effectively starts fresh from this source's perspective.
    """
    source = (await db.execute(
        select(Source)
        .options(
            selectinload(Source.knowledge_type),
            selectinload(Source.department),
            selectinload(Source.contributor),
        )
        .where(Source.id == source_id)
    )).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.source_type == "url" and not source.url:
        raise HTTPException(status_code=400, detail="Source has no URL to recompile")
    if source.source_type == "file" and not source.minio_key:
        raise HTTPException(status_code=400, detail="Source file not found in storage")

    source.status = "pending"
    source.progress = 0
    source.progress_message = "Queued for recompile..."
    source.error_message = None
    await db.flush()

    pool = await get_arq_pool()
    if source.source_type == "url":
        job = await pool.enqueue_job("ingest_url_task", str(source_id))
    else:
        job = await pool.enqueue_job("reingest_file_task", str(source_id), force)

    source.job_id = job.job_id
    await db.commit()
    await db.refresh(source)

    source = (await db.execute(
        select(Source)
        .options(
            selectinload(Source.knowledge_type),
            selectinload(Source.department),
            selectinload(Source.contributor),
        )
        .where(Source.id == source_id)
    )).scalar_one()
    logger.info(f"Queued recompile job {job.job_id} for source {source_id} (force={force})")
    return _to_response(source)


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("kb.manage"),
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

    # Detach from wiki — orphan pages are removed.
    from app.services import wiki_service
    await wiki_service.detach_source_from_wiki(db, source_id)

    await repo.delete_by_id(Source, source_id)
    return {"deleted": True}
