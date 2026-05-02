"""
Search router — semantic search across the knowledge base.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import Employee
from app.services.kb_service import search_kb, suggest_contacts
from app.services.auth_service import require_admin

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    min_similarity: float = 0.2


class SearchResultItem(BaseModel):
    source_title: Optional[str]
    content: str
    similarity: float
    page_number: Optional[int]
    image_urls: list[str]
    source_download_url: Optional[str]


class ContactSuggestion(BaseModel):
    name: str
    role: Optional[str]
    phone: Optional[str]
    email: Optional[str]


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    contacts: list[ContactSuggestion]
    message: Optional[str] = None


@router.post("/search", response_model=SearchResponse)
async def search_knowledge_base(req: SearchRequest, db: AsyncSession = Depends(get_db)):
    """
    Semantic search across the knowledge base.
    If no results found, suggests relevant contacts.
    """
    results = await search_kb(db, req.query, top_k=req.top_k, min_similarity=req.min_similarity)

    items = [
        SearchResultItem(
            source_title=r.source_title,
            content=r.content,
            similarity=r.similarity,
            page_number=r.page_number,
            image_urls=r.image_urls,
            source_download_url=r.source_download_url,
        )
        for r in results
    ]

    # If no results, suggest contacts
    contacts: list[ContactSuggestion] = []
    message = None
    if not items:
        contact_data = await suggest_contacts(db, req.query)
        contacts = [ContactSuggestion(**c) for c in contact_data]
        if contacts:
            message = "Không tìm thấy thông tin trong tài liệu. Đề xuất liên hệ:"
        else:
            message = "Không tìm thấy thông tin liên quan trong hệ thống."

    return SearchResponse(results=items, contacts=contacts, message=message)


class PreviewRequest(BaseModel):
    query: str
    employee_id: Optional[uuid.UUID] = None
    top_k: int = 10
    min_similarity: float = 0.2


class PreviewResultItem(SearchResultItem):
    source_id: uuid.UUID
    knowledge_type_name: Optional[str] = None
    department_name: Optional[str] = None


class PreviewResponse(BaseModel):
    results: list[PreviewResultItem]
    scope_summary: str  # human-readable description of what scope was applied
    employee_name: Optional[str] = None


@router.post("/search/preview", response_model=PreviewResponse)
async def search_preview(
    req: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    """
    Admin-only: preview search results as a specific employee would see them.
    If no employee_id, runs unscoped (admin view of all sources).
    """
    from app.ai.registry import ProviderRegistry
    from app.database.vector_search import semantic_search
    from app.database.models import Source, KnowledgeType, Department
    from app.services.storage_service import storage_service
    from app.services.mcp_auth_service import MCPAuthService, apply_scope_filter

    employee_name = None
    scope_summary = "Unscoped — showing all sources (admin view)"

    # Resolve employee scope if requested
    identity = None
    if req.employee_id:
        emp_result = await db.execute(
            select(Employee)
            .options(selectinload(Employee.department))
            .where(Employee.id == req.employee_id)
        )
        emp = emp_result.scalar_one_or_none()
        if not emp:
            raise HTTPException(404, "Employee not found")
        employee_name = emp.name
        auth_svc = MCPAuthService(db)
        identity = await auth_svc._resolve_scope(emp)

        if identity.allowed_source_ids is None and identity.allowed_knowledge_types is None:
            scope_summary = f"Open access — {emp.name} can see all sources"
        else:
            parts = []
            if identity.allowed_knowledge_types:
                parts.append(f"types: {', '.join(identity.allowed_knowledge_types)}")
            if identity.allowed_source_ids:
                parts.append(f"{len(identity.allowed_source_ids)} specific source(s)")
            if identity.project_source_ids:
                parts.append(f"{len(identity.project_source_ids)} project source(s)")
            scope_summary = f"Restricted for {emp.name}: " + "; ".join(parts) if parts else f"No access rules for {emp.name}"

    # Embed query
    registry = ProviderRegistry(db)
    embedding_provider = await registry.get_embedding(task="search_query")
    query_embedding = await embedding_provider.embed(req.query)

    # Build scoped search query
    from sqlalchemy import select as sa_select
    from app.database.models import SourceChunk, ChunkImage
    from sqlalchemy import func

    # Use existing semantic_search then filter, OR build scoped query
    if identity is not None:
        # Build query with scope filter applied before similarity ranking
        from pgvector.sqlalchemy import Vector
        stmt = (
            sa_select(
                SourceChunk.id,
                SourceChunk.source_id,
                SourceChunk.content,
                SourceChunk.page_number,
                Source.title.label("source_title"),
                Source.minio_key.label("source_minio_key"),
                Source.knowledge_type_id,
                Source.department_id,
                (1 - SourceChunk.embedding.cosine_distance(query_embedding)).label("similarity"),
            )
            .join(Source, Source.id == SourceChunk.source_id)
            .where(Source.status == "ready")
            .order_by(SourceChunk.embedding.cosine_distance(query_embedding))
            .limit(req.top_k * 3)
        )
        # Wrap in a subquery-like approach: apply scope filter on Source side
        scoped_source_stmt = sa_select(Source.id).where(Source.status == "ready")
        scoped_source_stmt = apply_scope_filter(scoped_source_stmt, identity)
        scoped_source_result = await db.execute(scoped_source_stmt)
        allowed_ids = [r[0] for r in scoped_source_result.all()]

        stmt = (
            sa_select(
                SourceChunk.id,
                SourceChunk.source_id,
                SourceChunk.content,
                SourceChunk.page_number,
                Source.title.label("source_title"),
                Source.minio_key.label("source_minio_key"),
                Source.knowledge_type_id,
                Source.department_id,
                (1 - SourceChunk.embedding.cosine_distance(query_embedding)).label("similarity"),
            )
            .join(Source, Source.id == SourceChunk.source_id)
            .where(Source.status == "ready", SourceChunk.source_id.in_(allowed_ids))
            .order_by(SourceChunk.embedding.cosine_distance(query_embedding))
            .limit(req.top_k)
        )
    else:
        stmt = (
            sa_select(
                SourceChunk.id,
                SourceChunk.source_id,
                SourceChunk.content,
                SourceChunk.page_number,
                Source.title.label("source_title"),
                Source.minio_key.label("source_minio_key"),
                Source.knowledge_type_id,
                Source.department_id,
                (1 - SourceChunk.embedding.cosine_distance(query_embedding)).label("similarity"),
            )
            .join(Source, Source.id == SourceChunk.source_id)
            .where(Source.status == "ready")
            .order_by(SourceChunk.embedding.cosine_distance(query_embedding))
            .limit(req.top_k)
        )

    rows_result = await db.execute(stmt)
    rows = rows_result.all()

    # Load type/department names in bulk
    type_ids = {r.knowledge_type_id for r in rows if r.knowledge_type_id}
    dept_ids = {r.department_id for r in rows if r.department_id}

    type_map: dict = {}
    dept_map: dict = {}
    if type_ids:
        kt_result = await db.execute(sa_select(KnowledgeType).where(KnowledgeType.id.in_(type_ids)))
        type_map = {kt.id: kt.name for kt in kt_result.scalars().all()}
    if dept_ids:
        d_result = await db.execute(sa_select(Department).where(Department.id.in_(dept_ids)))
        dept_map = {d.id: d.name for d in d_result.scalars().all()}

    items = []
    for row in rows:
        sim = float(row.similarity) if row.similarity else 0.0
        if sim < req.min_similarity:
            continue

        # Image URLs
        img_result = await db.execute(
            sa_select(ChunkImage.minio_key).where(ChunkImage.chunk_id == row.id)
        )
        image_urls = []
        for key_row in img_result.all():
            try:
                image_urls.append(storage_service.get_presigned_url(key_row[0]))
            except Exception:
                pass

        # Source download URL
        source_download_url = None
        if row.source_minio_key:
            try:
                source_download_url = storage_service.get_presigned_url(row.source_minio_key)
            except Exception:
                pass

        items.append(PreviewResultItem(
            source_id=row.source_id,
            source_title=row.source_title,
            content=row.content,
            similarity=sim,
            page_number=row.page_number,
            image_urls=image_urls,
            source_download_url=source_download_url,
            knowledge_type_name=type_map.get(row.knowledge_type_id),
            department_name=dept_map.get(row.department_id),
        ))

    return PreviewResponse(results=items, scope_summary=scope_summary, employee_name=employee_name)
