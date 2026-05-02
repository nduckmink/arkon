"""
Sources router — CRUD + upload + arq job ingestion pipeline.
"""

import uuid
from typing import Optional

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Form, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db, async_session_factory
from app.database.models import Source, SourceChunk, SourceInsight, ChunkImage, Employee
from app.database.repository import Repository
from app.services.auth_service import require_admin

router = APIRouter()

# arq Redis pool (lazy init)
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
    chunk_count: int = 0
    image_count: int = 0
    knowledge_type_id: Optional[uuid.UUID] = None
    knowledge_type_name: Optional[str] = None
    knowledge_type_color: Optional[str] = None
    department_id: Optional[uuid.UUID] = None
    department_name: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class SourceDetail(SourceResponse):
    full_text: Optional[str] = None
    summary: Optional[str] = None
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


async def _get_source_counts(session: AsyncSession, source_id: uuid.UUID) -> tuple[int, int]:
    """Get chunk and image counts for a source."""
    from sqlalchemy import func
    chunk_result = await session.execute(
        select(func.count()).where(SourceChunk.source_id == source_id)
    )
    img_result = await session.execute(
        select(func.count()).where(ChunkImage.source_id == source_id)
    )
    return chunk_result.scalar_one(), img_result.scalar_one()


def _to_response(source: Source, chunk_count: int = 0, image_count: int = 0) -> SourceResponse:
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
        chunk_count=chunk_count,
        image_count=image_count,
        knowledge_type_id=source.knowledge_type_id,
        knowledge_type_name=source.knowledge_type.name if source.knowledge_type else None,
        knowledge_type_color=source.knowledge_type.color if source.knowledge_type else None,
        department_id=source.department_id,
        department_name=source.department.name if source.department else None,
        created_at=source.created_at.isoformat(),
        updated_at=source.updated_at.isoformat(),
    )


# --- List all sources ---
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
        .options(selectinload(Source.knowledge_type), selectinload(Source.department))
        .order_by(Source.created_at.desc())
    )
    if knowledge_type_id:
        stmt = stmt.where(Source.knowledge_type_id == knowledge_type_id)
    if department_id:
        stmt = stmt.where(Source.department_id == department_id)
    if status:
        stmt = stmt.where(Source.status == status)

    result = await db.execute(stmt)
    sources = result.scalars().all()

    results = []
    for s in sources:
        cc, ic = await _get_source_counts(db, s.id)
        results.append(_to_response(s, cc, ic))
    return results


# --- Get single source detail ---
@router.get("/sources/{source_id}")
async def get_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    result = await db.execute(
        select(Source)
        .options(selectinload(Source.knowledge_type), selectinload(Source.department))
        .where(Source.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    cc, ic = await _get_source_counts(db, source_id)

    summary = None
    insight_result = await db.execute(
        select(SourceInsight).where(
            SourceInsight.source_id == source_id,
            SourceInsight.insight_type == "summary",
        )
    )
    insight = insight_result.scalar_one_or_none()
    if insight:
        summary = insight.content

    download_url = None
    if source.minio_key:
        try:
            from app.services.storage_service import storage_service
            download_url = storage_service.get_presigned_url(source.minio_key)
        except Exception:
            pass

    base = _to_response(source, cc, ic)
    return SourceDetail(
        **base.model_dump(),
        full_text=source.full_text,
        summary=summary,
        download_url=download_url,
    )


# --- Get progress for a source ---
@router.get("/sources/{source_id}/progress")
async def get_source_progress(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    """Get real-time progress for a source being processed."""
    source = await db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    cc, ic = await _get_source_counts(db, source_id)
    return {
        "id": str(source.id),
        "status": source.status,
        "progress": source.progress,
        "progress_message": source.progress_message,
        "chunk_count": cc,
        "image_count": ic,
    }


# --- Upload file ---
@router.post("/sources/upload", response_model=SourceResponse)
async def upload_source(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    knowledge_type_id: Optional[str] = Form(None),
    department_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
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
        progress_message="Dang cho xu ly...",
        knowledge_type_id=uuid.UUID(knowledge_type_id) if knowledge_type_id else None,
        department_id=uuid.UUID(department_id) if department_id else None,
    )
    source = await repo.create(source)
    await db.commit()
    await db.refresh(source)

    # Enqueue arq job
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "ingest_file_task",
        str(source.id),
        file_data,
        file.filename or "unknown",
    )

    # Save job ID
    source.job_id = job.job_id
    await db.commit()

    # Reload with relationships for response
    result = await db.execute(
        select(Source)
        .options(selectinload(Source.knowledge_type), selectinload(Source.department))
        .where(Source.id == source.id)
    )
    source = result.scalar_one()

    logger.info(f"Enqueued ingestion job {job.job_id} for source {source.id}")

    # Sync to Neo4j knowledge graph
    try:
        from app.services.neo4j_service import neo4j_service
        if neo4j_service.available:
            await neo4j_service.ensure_document(str(source.id), source.title or "")
    except Exception as e:
        logger.debug(f"Neo4j source sync skipped: {e}")

    return _to_response(source)


# --- Add URL source ---
@router.post("/sources/url", response_model=SourceResponse)
async def add_url_source(
    req: SourceCreateURL,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    repo = Repository(db)
    source = Source(
        title=req.title or req.url,
        source_type="url",
        url=req.url,
        status="pending",
        progress=0,
        progress_message="Dang cho xu ly...",
        knowledge_type_id=req.knowledge_type_id,
        department_id=req.department_id,
    )
    source = await repo.create(source)
    await db.commit()
    await db.refresh(source)

    # Enqueue arq job
    pool = await get_arq_pool()
    job = await pool.enqueue_job("ingest_url_task", str(source.id))
    source.job_id = job.job_id
    await db.commit()

    # Reload with relationships for response
    result = await db.execute(
        select(Source)
        .options(selectinload(Source.knowledge_type), selectinload(Source.department))
        .where(Source.id == source.id)
    )
    source = result.scalar_one()

    logger.info(f"Enqueued URL ingestion job {job.job_id} for source {source.id}")

    # Sync to Neo4j knowledge graph
    try:
        from app.services.neo4j_service import neo4j_service
        if neo4j_service.available:
            await neo4j_service.ensure_document(str(source.id), source.title or "")
    except Exception as e:
        logger.debug(f"Neo4j source sync skipped: {e}")

    return _to_response(source)


# --- Update source metadata ---
@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
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

    # Reload with relationships
    result = await db.execute(
        select(Source)
        .options(selectinload(Source.knowledge_type), selectinload(Source.department))
        .where(Source.id == source_id)
    )
    source = result.scalar_one()
    cc, ic = await _get_source_counts(db, source_id)
    return _to_response(source, cc, ic)


# --- Re-ingest source ---
@router.post("/sources/{source_id}/reingest", response_model=SourceResponse)
async def reingest_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    """Re-queue ingestion for a source. Clears existing chunks and re-processes."""
    result = await db.execute(
        select(Source)
        .options(selectinload(Source.knowledge_type), selectinload(Source.department))
        .where(Source.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if source.source_type == "url" and not source.url:
        raise HTTPException(status_code=400, detail="Source has no URL to re-ingest")
    if source.source_type == "file" and not source.minio_key:
        raise HTTPException(status_code=400, detail="Source file not found in storage")

    # Delete existing chunks so re-ingestion starts clean
    await db.execute(
        select(SourceChunk).where(SourceChunk.source_id == source_id)
    )
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(SourceChunk).where(SourceChunk.source_id == source_id))
    await db.execute(sql_delete(SourceInsight).where(SourceInsight.source_id == source_id))

    source.status = "pending"
    source.progress = 0
    source.progress_message = "Queued for re-ingestion..."
    source.error_message = None
    await db.flush()

    pool = await get_arq_pool()
    if source.source_type == "url":
        job = await pool.enqueue_job("ingest_url_task", str(source_id))
    else:
        job = await pool.enqueue_job("reingest_file_task", str(source_id))

    source.job_id = job.job_id
    await db.commit()

    await db.refresh(source)
    result2 = await db.execute(
        select(Source)
        .options(selectinload(Source.knowledge_type), selectinload(Source.department))
        .where(Source.id == source_id)
    )
    source = result2.scalar_one()

    logger.info(f"Queued re-ingestion job {job.job_id} for source {source_id}")
    return _to_response(source)


# --- Delete source ---
@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    repo = Repository(db)
    source = await repo.get_by_id(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Clean up MinIO files
    try:
        from app.services.storage_service import storage_service
        storage_service.delete_prefix(f"sources/{source_id}/")
    except Exception as e:
        logger.warning(f"Failed to clean MinIO files for source {source_id}: {e}")

    # Clean up Neo4j document node
    try:
        from app.services.neo4j_service import neo4j_service
        if neo4j_service.available:
            await neo4j_service.delete_document(str(source_id))
    except Exception as e:
        logger.debug(f"Neo4j source delete skipped: {e}")

    await repo.delete_by_id(Source, source_id)
    return {"deleted": True}
