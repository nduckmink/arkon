"""
Wiki REST router — admin/portal access to LLM-compiled wiki pages.

Permission model v2:
  - wiki:read:own_dept → global wiki + project-scoped wiki (if member)
  - wiki:read:all → all wiki pages regardless of scope
  - Admin → full access

Scope filtering:
  - Global wiki pages (scope_type='global') → visible to all with wiki:read
  - Project-scoped wiki (scope_type='project') → visible only to workspace members + admin
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Employee, ProjectMember, WikiPage, WikiPageRevision
from app.services import wiki_service
from app.services.audit_service import log_audit
from app.services.auth_service import get_current_user, require_permission
from app.services.permission_engine import (
    _get_user_permissions,
    get_scope_level,
    get_workspace_role,
    workspace_role_can,
)

router = APIRouter()


class WikiPageSummary(BaseModel):
    slug: str
    title: str
    page_type: str
    summary: str
    knowledge_type_slugs: list[str]
    source_ids: list[uuid.UUID]
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None
    version: int
    updated_at: str


class WikiPageDetail(WikiPageSummary):
    content_md: str
    backlinks: list[str]
    outlinks: list[str]
    orphaned: bool = False


class WikiDirectEditRequest(BaseModel):
    content_md: str
    change_note: Optional[str] = None

    @field_validator("content_md")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content_md must not be empty")
        return v


class WikiRevisionSummary(BaseModel):
    id: uuid.UUID
    version: int
    change_type: str
    changed_by_name: Optional[str] = None
    change_note: Optional[str] = None
    created_at: str


def _summary(p: WikiPage) -> WikiPageSummary:
    return WikiPageSummary(
        slug=p.slug,
        title=p.title,
        page_type=p.page_type,
        summary=p.summary or "",
        knowledge_type_slugs=p.knowledge_type_slugs or [],
        source_ids=list(p.source_ids or []),
        scope_type=p.scope_type or "global",
        scope_id=p.scope_id,
        version=p.version or 1,
        updated_at=p.updated_at.isoformat() if p.updated_at else "",
    )


def _detail(p: WikiPage, backlinks: list[str], outlinks: list[str]) -> WikiPageDetail:
    return WikiPageDetail(
        **_summary(p).model_dump(),
        content_md=p.content_md or "",
        backlinks=sorted(backlinks),
        outlinks=sorted(outlinks),
        orphaned=p.orphaned or False,
    )


def _build_wiki_scope_filter(user: Employee):
    """Build SQLAlchemy filter for wiki pages based on user permissions.

    Returns None if user can see everything (admin / wiki:read:all).
    Returns a filter clause otherwise.
    """
    if user.role == "admin":
        return None  # No filter

    perms = _get_user_permissions(user)
    scope_level = get_scope_level(list(perms), "wiki", "read")

    if scope_level == "all":
        return None  # No filter

    if scope_level == "own_dept":
        # Show: global wiki + project-scoped wiki where user is a member + dept wiki for user's dept
        return or_(
            WikiPage.scope_type == "global",
            WikiPage.scope_id.in_(
                select(ProjectMember.project_id)
                .where(ProjectMember.employee_id == user.id)
            ),
            and_(
                WikiPage.scope_type == "department",
                WikiPage.scope_id == user.department_id,
            ),
        )

    # No wiki:read permission at all — should have been caught by require_permission
    return WikiPage.id == None  # noqa: E711 — empty result


@router.get("/wiki/pages", response_model=list[WikiPageSummary])
async def list_wiki_pages(
    page_type: Optional[str] = Query(None),
    knowledge_type_slug: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """List wiki pages filtered by user's permission scope."""
    stmt = (
        select(WikiPage)
        .where(WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG]))
        .order_by(WikiPage.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )

    # Apply scope filter
    scope_filter = _build_wiki_scope_filter(user)
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)

    if page_type:
        stmt = stmt.where(WikiPage.page_type == page_type)
    if knowledge_type_slug:
        stmt = stmt.where(WikiPage.knowledge_type_slugs.any(knowledge_type_slug))  # type: ignore[arg-type]

    result = await db.execute(stmt)
    return [_summary(p) for p in result.scalars().all()]


@router.get("/wiki/pages/{slug:path}", response_model=WikiPageDetail)
async def get_wiki_page(
    slug: str,
    scope_type: Optional[str] = Query(None),
    scope_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    sid = uuid.UUID(scope_id) if scope_id else None
    if scope_type:
        page = await wiki_service.get_page_by_slug(db, slug, scope_type=scope_type, scope_id=sid)
    else:
        page = await wiki_service.get_page_by_slug(db, slug, scope_type="global", scope_id=None)
        if not page:
            page = await wiki_service.get_page_by_slug_any_scope(db, slug)

    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    # Check scope access
    if page.scope_type == "project" and page.scope_id:
        if user.role != "admin":
            perms = _get_user_permissions(user)
            if "wiki:read:all" not in perms:
                # Check workspace membership
                member = (await db.execute(
                    select(ProjectMember.role)
                    .where(
                        ProjectMember.project_id == page.scope_id,
                        ProjectMember.employee_id == user.id,
                    )
                )).scalar_one_or_none()
                if not member:
                    raise HTTPException(403, "Access denied — you are not a member of this workspace")

    if page.scope_type == "department" and page.scope_id:
        if user.role != "admin":
            perms = _get_user_permissions(user)
            if "wiki:read:all" not in perms:
                if user.department_id != page.scope_id:
                    raise HTTPException(403, "Access denied — this page belongs to another department")

    backlinks = await wiki_service.get_backlinks(db, slug)
    outlinks = await wiki_service.get_outlinks(db, slug)
    return _detail(page, backlinks, outlinks)


@router.get("/wiki/index")
async def get_wiki_index(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("wiki:read"),
):
    page = await wiki_service.get_page_by_slug(db, wiki_service.INDEX_SLUG)
    return {"content_md": page.content_md if page else ""}


@router.get("/wiki/log")
async def get_wiki_log(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("wiki:read"),
):
    page = await wiki_service.get_page_by_slug(db, wiki_service.LOG_SLUG)
    return {"content_md": page.content_md if page else ""}


@router.put("/wiki/pages/{slug:path}", response_model=WikiPageDetail)
async def direct_edit_wiki_page(
    slug: str,
    body: WikiDirectEditRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Direct sync edit by an editor or admin. No review step. Creates a revision."""
    if slug in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        raise HTTPException(400, "Cannot directly edit reserved pages")

    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    # Permission: workspace editor+ OR wiki:write:all OR admin
    if user.role != "admin":
        if page.scope_type == "project" and page.scope_id:
            member_role = await get_workspace_role(db, user, page.scope_id)
            if not member_role or not workspace_role_can(member_role, "editor"):
                raise HTTPException(403, "Requires editor role or above in this workspace")
        else:
            perms = _get_user_permissions(user)
            if "wiki:write:all" not in perms:
                raise HTTPException(403, "Requires wiki:write:all permission to directly edit global wiki pages")

    await wiki_service.direct_edit_page(db, page, user.id, body.content_md, body.change_note)
    await log_audit(db, user, "update", "wiki_page", str(page.id), reason=f"direct edit: {slug}")
    await db.commit()
    await db.refresh(page)

    backlinks = await wiki_service.get_backlinks(db, slug)
    outlinks = await wiki_service.get_outlinks(db, slug)
    return _detail(page, backlinks, outlinks)


@router.get("/wiki/pages/{slug:path}/revisions", response_model=list[WikiRevisionSummary])
async def list_wiki_page_revisions(
    slug: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """List revision history for a wiki page (most recent first)."""
    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    from app.database.models import Employee as Emp
    rows = (await db.execute(
        select(WikiPageRevision, Emp.name.label("changed_by_name"))
        .outerjoin(Emp, WikiPageRevision.changed_by_id == Emp.id)
        .where(WikiPageRevision.page_id == page.id)
        .order_by(WikiPageRevision.version.desc())
        .limit(limit)
    )).all()

    return [
        WikiRevisionSummary(
            id=r.WikiPageRevision.id,
            version=r.WikiPageRevision.version,
            change_type=r.WikiPageRevision.change_type,
            changed_by_name=r.changed_by_name,
            change_note=r.WikiPageRevision.change_note,
            created_at=r.WikiPageRevision.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/wiki/pages/{slug:path}/revisions/{version}/rollback", response_model=WikiPageDetail)
async def rollback_wiki_page(
    slug: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Rollback a wiki page to a specific version. Admin only."""
    if user.role != "admin":
        raise HTTPException(403, "Only admins can rollback wiki pages")

    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    try:
        await wiki_service.rollback_to_revision(db, page, version, user.id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    await log_audit(db, user, "update", "wiki_page", str(page.id), reason=f"rollback to v{version}: {slug}")
    await db.commit()
    await db.refresh(page)

    backlinks = await wiki_service.get_backlinks(db, slug)
    outlinks = await wiki_service.get_outlinks(db, slug)
    return _detail(page, backlinks, outlinks)


@router.get("/wiki/orphaned", response_model=list[WikiPageSummary])
async def list_orphaned_wiki_pages(
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List wiki pages that have no source document (orphaned). Admin only."""
    if user.role != "admin":
        raise HTTPException(403, "Only admins can view orphaned pages")

    pages = (await db.execute(
        select(WikiPage).where(WikiPage.orphaned == True).order_by(WikiPage.updated_at.desc())  # noqa: E712
    )).scalars().all()
    return [_summary(p) for p in pages]


@router.delete("/wiki/pages/{slug:path}")
async def delete_wiki_page(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:delete"),
):
    """Delete a wiki page and cascade-cleanup all references."""
    if slug in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        raise HTTPException(400, "Cannot delete reserved pages")

    page = await wiki_service.get_page_by_slug(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    # Check admin role (additional safeguard)
    if user.role not in ("admin", "super_admin"):
        raise HTTPException(403, "Only admins can delete wiki pages")

    deleted_title = page.title
    await log_audit(db, user, "delete", "wiki", slug, reason=deleted_title)
    await wiki_service.delete_page_cascade(db, slug)
    await wiki_service.regenerate_index(db)
    await wiki_service.append_log(db, f"Deleted page: {deleted_title} ({slug})")
    await db.commit()
    return {"ok": True, "deleted_slug": slug}


@router.get("/wiki/graph")
async def get_wiki_graph(
    slug: Optional[str] = Query(None, description="Center the graph on this slug; omit for full graph"),
    depth: int = Query(1, ge=1, le=3),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """Return nodes/edges for visualization with scope filtering."""
    if slug:
        # Neighborhood view — check access to center page first
        return await wiki_service.get_neighborhood(db, slug, depth=depth)

    # Full graph — paginated, with scope filtering
    from sqlalchemy import case, func as sqlfunc

    from app.database.models import Department, Project, WikiLink

    base_filter = WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG])

    # Apply scope filter
    scope_filter = _build_wiki_scope_filter(user)

    # Total count
    count_stmt = select(sqlfunc.count()).select_from(WikiPage).where(base_filter)
    if scope_filter is not None:
        count_stmt = count_stmt.where(scope_filter)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch paginated nodes
    stmt = (
        select(
            WikiPage.slug,
            WikiPage.title,
            WikiPage.page_type,
            WikiPage.scope_type,
            WikiPage.scope_id,
            case(
                (WikiPage.scope_type == "project", Project.name),
                (WikiPage.scope_type == "department", Department.name),
                else_=None,
            ).label("scope_name"),
        )
        .select_from(WikiPage)
        .outerjoin(Project, and_(WikiPage.scope_id == Project.id, WikiPage.scope_type == "project"))
        .outerjoin(Department, and_(WikiPage.scope_id == Department.id, WikiPage.scope_type == "department"))
        .where(base_filter)
        .order_by(WikiPage.slug)
        .offset(offset)
        .limit(limit)
    )
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)

    pages = (await db.execute(stmt)).all()

    # Edges — return ALL on first batch (offset=0)
    if offset == 0:
        edges = (await db.execute(
            select(WikiLink.from_slug, WikiLink.to_slug)
        )).all()
    else:
        edges = []

    return {
        "nodes": [
            {
                "slug": r.slug,
                "title": r.title,
                "page_type": r.page_type,
                "scope_type": r.scope_type or "global",
                "scope_id": str(r.scope_id) if r.scope_id else None,
                "scope_name": r.scope_name,
            }
            for r in pages
        ],
        "edges": [{"from": r.from_slug, "to": r.to_slug} for r in edges],
        "total": total,
        "offset": offset,
        "has_more": offset + limit < total,
    }
