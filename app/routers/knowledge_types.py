"""
Knowledge Types router — admin-defined document categories.

Admin creates types like "SOP", "Product Spec", "HR Policy", etc.
Each type has a slug, display name, icon, and color for the UI.
"""

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import KnowledgeType, Employee
from app.services.auth_service import require_permission

router = APIRouter()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class KnowledgeTypeCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    color: str = "#6366f1"
    description: Optional[str] = None

    @field_validator("slug", mode="before")
    @classmethod
    def generate_slug(cls, v, info):
        if v:
            return re.sub(r"[^a-z0-9-]", "", v.lower().replace(" ", "-"))
        # Will be generated from name in the endpoint
        return v


class KnowledgeTypeOut(BaseModel):
    id: str
    slug: str
    name: str
    color: str
    description: Optional[str]
    sort_order: int
    source_count: int = 0

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("/knowledge-types", response_model=list[KnowledgeTypeOut])
async def list_knowledge_types(db: AsyncSession = Depends(get_db)):
    """List all knowledge types, ordered by sort_order."""
    from app.database.models import Source
    from sqlalchemy import func

    stmt = select(KnowledgeType).order_by(KnowledgeType.sort_order, KnowledgeType.name)
    result = await db.execute(stmt)
    types = result.scalars().all()

    # Count sources per type
    count_stmt = (
        select(Source.knowledge_type_id, func.count(Source.id))
        .group_by(Source.knowledge_type_id)
    )
    count_result = await db.execute(count_stmt)
    counts = {str(row[0]): row[1] for row in count_result.all() if row[0]}

    return [
        KnowledgeTypeOut(
            id=str(t.id),
            slug=t.slug,
            name=t.name,
            color=t.color or "#6366f1",
            description=t.description,
            sort_order=t.sort_order,
            source_count=counts.get(str(t.id), 0),
        )
        for t in types
    ]


@router.post("/knowledge-types", status_code=201, response_model=KnowledgeTypeOut)
async def create_knowledge_type(body: KnowledgeTypeCreate, db: AsyncSession = Depends(get_db), _user: Employee = require_permission("documents.create")):
    """Create a new knowledge type."""
    # Generate slug from name if not provided
    slug = body.slug or re.sub(r"[^a-z0-9-]", "", body.name.lower().replace(" ", "-"))

    # Check uniqueness
    existing = await db.execute(select(KnowledgeType).where(KnowledgeType.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Knowledge type with slug '{slug}' already exists")

    # Get next sort_order
    max_order_result = await db.execute(
        select(KnowledgeType.sort_order).order_by(KnowledgeType.sort_order.desc()).limit(1)
    )
    max_order = max_order_result.scalar() or 0

    kt = KnowledgeType(
        slug=slug,
        name=body.name,
        color=body.color,
        description=body.description,
        sort_order=max_order + 1,
    )
    db.add(kt)
    await db.flush()

    return KnowledgeTypeOut(
        id=str(kt.id),
        slug=kt.slug,
        name=kt.name,
        color=kt.color or "#6366f1",
        description=kt.description,
        sort_order=kt.sort_order,
        source_count=0,
    )


@router.put("/knowledge-types/{kt_id}", response_model=KnowledgeTypeOut)
async def update_knowledge_type(
    kt_id: str,
    body: KnowledgeTypeCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("documents.edit"),
):
    """Update a knowledge type."""
    kt = await db.get(KnowledgeType, uuid.UUID(kt_id))
    if not kt:
        raise HTTPException(404, "Knowledge type not found")

    kt.name = body.name
    if body.slug:
        kt.slug = body.slug
    kt.color = body.color
    kt.description = body.description
    await db.flush()

    return KnowledgeTypeOut(
        id=str(kt.id),
        slug=kt.slug,
        name=kt.name,
        color=kt.color or "#6366f1",
        description=kt.description,
        sort_order=kt.sort_order,
        source_count=0,
    )


@router.delete("/knowledge-types/{kt_id}")
async def delete_knowledge_type(kt_id: str, db: AsyncSession = Depends(get_db), _user: Employee = require_permission("documents.delete")):
    """Delete a knowledge type. Sources using it will have their type set to NULL."""
    kt = await db.get(KnowledgeType, uuid.UUID(kt_id))
    if not kt:
        raise HTTPException(404, "Knowledge type not found")
    await db.delete(kt)
    return {"deleted": True, "slug": kt.slug}


@router.patch("/knowledge-types/reorder")
async def reorder_knowledge_types(
    order: list[str],
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("documents.edit"),
):
    """
    Reorder knowledge types.
    Args:
        order: List of knowledge type IDs in desired order.
    """
    for idx, kt_id in enumerate(order):
        kt = await db.get(KnowledgeType, uuid.UUID(kt_id))
        if kt:
            kt.sort_order = idx
    await db.flush()
    return {"reordered": len(order)}
