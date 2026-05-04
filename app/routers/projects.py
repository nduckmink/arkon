"""
Projects router — cross-functional knowledge contexts.

A Project groups employees and sources across departments for a specific purpose
(client engagement, event, initiative). Only project members and admins can access
project-scoped sources via MCP.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import Employee, Project, ProjectMember, ProjectSource, ScopeMembership, ScopeRole, ScopeType, Source
from app.services.auth_service import get_current_user, require_admin, require_permission

router = APIRouter()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    workspace_type: Optional[str] = "project"  # "project" or "customer"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # "active" or "archived"
    workspace_type: Optional[str] = None  # "project" or "customer"


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    workspace_type: str = "project"
    status: str
    member_count: int = 0
    source_count: int = 0
    created_at: str

    class Config:
        from_attributes = True


class MemberOut(BaseModel):
    employee_id: str
    employee_name: str
    employee_email: str
    role: str
    added_at: str


class ProjectSourceOut(BaseModel):
    source_id: str
    title: Optional[str]
    source_type: Optional[str]
    file_name: Optional[str] = None
    status: str
    progress: Optional[int] = None
    progress_message: Optional[str] = None
    knowledge_type_name: Optional[str] = None
    added_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_project_or_404(db: AsyncSession, project_id: str) -> Project:
    project = await db.get(Project, uuid.UUID(project_id))
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _project_out(project: Project, member_count: int = 0, source_count: int = 0) -> ProjectOut:
    return ProjectOut(
        id=str(project.id),
        name=project.name,
        description=project.description,
        workspace_type=getattr(project, 'workspace_type', 'project') or 'project',
        status=project.status,
        member_count=member_count,
        source_count=source_count,
        created_at=project.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Admin: returns all projects.
    Employee: returns only projects they are a member of.
    """
    from sqlalchemy import func

    if current_user.role == "admin":
        result = await db.execute(select(Project).order_by(Project.created_at.desc()))
        projects = result.scalars().all()
    else:
        result = await db.execute(
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.employee_id == current_user.id)
            .order_by(Project.created_at.desc())
        )
        projects = result.scalars().all()

    # Fetch counts in bulk
    member_counts_result = await db.execute(
        select(ProjectMember.project_id, func.count(ProjectMember.employee_id))
        .group_by(ProjectMember.project_id)
    )
    member_counts = {str(r[0]): r[1] for r in member_counts_result.all()}

    source_counts_result = await db.execute(
        select(ProjectSource.project_id, func.count(ProjectSource.source_id))
        .group_by(ProjectSource.project_id)
    )
    source_counts = {str(r[0]): r[1] for r in source_counts_result.all()}

    return [
        _project_out(p, member_counts.get(str(p.id), 0), source_counts.get(str(p.id), 0))
        for p in projects
    ]


@router.post("/projects", status_code=201, response_model=ProjectOut)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = require_permission("workspaces.create"),
):
    ws_type = body.workspace_type or "project"
    if ws_type not in ("project", "customer"):
        raise HTTPException(400, "workspace_type must be 'project' or 'customer'")

    project = Project(
        name=body.name,
        description=body.description,
        workspace_type=ws_type,
        status="active",
        created_by_id=current_user.id,
    )
    db.add(project)
    await db.flush()

    # Auto-create scope membership for creator as owner
    scope_membership = ScopeMembership(
        employee_id=current_user.id,
        scope_type=ScopeType.PROJECT.value,
        scope_id=project.id,
        role=ScopeRole.OWNER.value,
        granted_by_id=current_user.id,
    )
    db.add(scope_membership)
    await db.flush()

    return _project_out(project)


@router.put("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("workspaces.edit"),
):
    project = await _get_project_or_404(db, project_id)

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.workspace_type is not None:
        if body.workspace_type not in ("project", "customer"):
            raise HTTPException(400, "workspace_type must be 'project' or 'customer'")
        project.workspace_type = body.workspace_type
    if body.status is not None:
        if body.status not in ("active", "archived"):
            raise HTTPException(400, "Status must be 'active' or 'archived'")
        project.status = body.status

    await db.flush()
    return _project_out(project)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("workspaces.delete"),
):
    project = await _get_project_or_404(db, project_id)
    await db.delete(project)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

class AddMemberBody(BaseModel):
    employee_id: str
    role: str = "member"


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
async def list_members(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    await _get_project_or_404(db, project_id)
    result = await db.execute(
        select(ProjectMember)
        .options(selectinload(ProjectMember.employee))
        .where(ProjectMember.project_id == uuid.UUID(project_id))
    )
    members = result.scalars().all()
    return [
        MemberOut(
            employee_id=str(m.employee_id),
            employee_name=m.employee.name,
            employee_email=m.employee.email,
            role=m.role,
            added_at=m.added_at.isoformat(),
        )
        for m in members
    ]


@router.post("/projects/{project_id}/members", status_code=201)
async def add_member(
    project_id: str,
    body: AddMemberBody,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("workspaces.edit"),
):
    await _get_project_or_404(db, project_id)

    emp = await db.get(Employee, uuid.UUID(body.employee_id))
    if not emp:
        raise HTTPException(404, "Employee not found")

    existing = await db.get(
        ProjectMember,
        (uuid.UUID(project_id), uuid.UUID(body.employee_id)),
    )
    if existing:
        raise HTTPException(409, "Employee is already a member")

    if body.role not in ("owner", "member"):
        raise HTTPException(400, "Role must be 'owner' or 'member'")

    member = ProjectMember(
        project_id=uuid.UUID(project_id),
        employee_id=uuid.UUID(body.employee_id),
        role=body.role,
    )
    db.add(member)

    # Sync: create or update scope membership for the project
    scope_role = ScopeRole.OWNER.value if body.role == "owner" else ScopeRole.CONTRIBUTOR.value
    
    scope_stmt = (
        select(ScopeMembership)
        .where(
            ScopeMembership.employee_id == uuid.UUID(body.employee_id),
            ScopeMembership.scope_type == ScopeType.PROJECT.value,
            ScopeMembership.scope_id == uuid.UUID(project_id),
        )
    )
    scope_membership = (await db.execute(scope_stmt)).scalar_one_or_none()
    
    if scope_membership:
        scope_membership.role = scope_role
    else:
        scope_membership = ScopeMembership(
            employee_id=uuid.UUID(body.employee_id),
            scope_type=ScopeType.PROJECT.value,
            scope_id=uuid.UUID(project_id),
            role=scope_role,
            granted_by_id=_user.id,
        )
        db.add(scope_membership)

    await db.flush()
    return {"added": True}


@router.delete("/projects/{project_id}/members/{employee_id}")
async def remove_member(
    project_id: str,
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("workspaces.edit"),
):
    member = await db.get(
        ProjectMember,
        (uuid.UUID(project_id), uuid.UUID(employee_id)),
    )
    if not member:
        raise HTTPException(404, "Member not found")
    await db.delete(member)

    # Sync: remove scope membership for the project
    scope_stmt = (
        select(ScopeMembership)
        .where(
            ScopeMembership.employee_id == uuid.UUID(employee_id),
            ScopeMembership.scope_type == ScopeType.PROJECT.value,
            ScopeMembership.scope_id == uuid.UUID(project_id),
        )
    )
    scope_membership = (await db.execute(scope_stmt)).scalar_one_or_none()
    if scope_membership:
        await db.delete(scope_membership)

    return {"removed": True}


# ---------------------------------------------------------------------------
# Project Sources
# ---------------------------------------------------------------------------

class AddSourceBody(BaseModel):
    source_id: str


@router.get("/projects/{project_id}/sources", response_model=list[ProjectSourceOut])
async def list_project_sources(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    await _get_project_or_404(db, project_id)
    pid = uuid.UUID(project_id)

    # 1. Linked sources (project_sources table)
    linked_result = await db.execute(
        select(ProjectSource)
        .options(
            selectinload(ProjectSource.source).selectinload(Source.knowledge_type)
        )
        .where(ProjectSource.project_id == pid)
    )
    linked_rows = linked_result.scalars().all()
    linked_ids = {r.source_id for r in linked_rows}

    # 2. Owned sources (scope_type=project, scope_id=project_id)
    owned_result = await db.execute(
        select(Source)
        .options(selectinload(Source.knowledge_type))
        .where(Source.scope_type == "project", Source.scope_id == pid)
    )
    owned_sources = owned_result.scalars().all()

    out: list[ProjectSourceOut] = []
    # Linked sources
    for r in linked_rows:
        out.append(ProjectSourceOut(
            source_id=str(r.source_id),
            title=r.source.title,
            source_type=r.source.source_type,
            file_name=r.source.file_name,
            status=r.source.status,
            progress=r.source.progress,
            progress_message=r.source.progress_message,
            knowledge_type_name=r.source.knowledge_type.name if r.source.knowledge_type else None,
            added_at=r.added_at.isoformat(),
        ))
    # Owned sources not already linked
    for s in owned_sources:
        if s.id not in linked_ids:
            out.append(ProjectSourceOut(
                source_id=str(s.id),
                title=s.title,
                source_type=s.source_type,
                file_name=s.file_name,
                status=s.status,
                progress=s.progress,
                progress_message=s.progress_message,
                knowledge_type_name=s.knowledge_type.name if s.knowledge_type else None,
                added_at=s.created_at.isoformat(),
            ))
    return out


@router.post("/projects/{project_id}/sources", status_code=201)
async def add_project_source(
    project_id: str,
    body: AddSourceBody,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("workspaces.edit"),
):
    await _get_project_or_404(db, project_id)

    source = await db.get(Source, uuid.UUID(body.source_id))
    if not source:
        raise HTTPException(404, "Source not found")

    existing = await db.get(
        ProjectSource,
        (uuid.UUID(project_id), uuid.UUID(body.source_id)),
    )
    if existing:
        raise HTTPException(409, "Source already in project")

    ps = ProjectSource(
        project_id=uuid.UUID(project_id),
        source_id=uuid.UUID(body.source_id),
    )
    db.add(ps)
    await db.flush()
    return {"added": True}


@router.delete("/projects/{project_id}/sources/{source_id}")
async def remove_project_source(
    project_id: str,
    source_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("workspaces.edit"),
):
    ps = await db.get(
        ProjectSource,
        (uuid.UUID(project_id), uuid.UUID(source_id)),
    )
    if not ps:
        raise HTTPException(404, "Source not in project")
    await db.delete(ps)
    return {"removed": True}


# ---------------------------------------------------------------------------
# Workspace-scoped upload (owned sources)
# ---------------------------------------------------------------------------

from fastapi import File, Form, UploadFile
from arq.connections import ArqRedis, create_pool

_arq_pool_ws: ArqRedis | None = None

async def _get_arq_pool() -> ArqRedis:
    global _arq_pool_ws
    if _arq_pool_ws is None:
        from app.worker import _get_redis_settings
        _arq_pool_ws = await create_pool(_get_redis_settings())
    return _arq_pool_ws


@router.post("/projects/{project_id}/sources/upload", status_code=201)
async def upload_workspace_source(
    project_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    knowledge_type_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("workspaces.edit"),
):
    """Upload a file directly into a workspace. Sets scope to project."""
    project = await _get_project_or_404(db, project_id)
    pid = uuid.UUID(project_id)

    file_data = await file.read()
    file_name = file.filename or "unknown"

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
        scope_type="project",
        scope_id=pid,
    )
    db.add(source)
    await db.flush()

    # Upload to MinIO
    from app.services.storage_service import storage_service
    from app.services.kb_service import _guess_content_type
    minio_key = f"sources/{source.id}/original/{file_name}"
    storage_service.upload_file(
        object_name=minio_key,
        data=file_data,
        content_type=_guess_content_type(file_name),
    )
    source.minio_key = minio_key
    source.file_name = file_name
    await db.flush()

    # Enqueue ingestion
    pool = await _get_arq_pool()
    job = await pool.enqueue_job("ingest_file_task", str(source.id))
    source.job_id = job.job_id
    await db.commit()

    return {
        "id": str(source.id),
        "title": source.title,
        "status": source.status,
        "scope_type": source.scope_type,
        "scope_id": str(source.scope_id),
    }


class WorkspaceURLBody(BaseModel):
    url: str
    title: str | None = None
    knowledge_type_id: str | None = None


@router.post("/projects/{project_id}/sources/url", status_code=201)
async def add_workspace_url_source(
    project_id: str,
    body: WorkspaceURLBody,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("workspaces.edit"),
):
    """Add a URL source directly into a workspace."""
    project = await _get_project_or_404(db, project_id)
    pid = uuid.UUID(project_id)

    source = Source(
        title=body.title or body.url,
        source_type="url",
        url=body.url,
        status="pending",
        progress=0,
        progress_message="Queued for ingestion...",
        knowledge_type_id=uuid.UUID(body.knowledge_type_id) if body.knowledge_type_id else None,
        contributed_by_employee_id=user.id,
        scope_type="project",
        scope_id=pid,
    )
    db.add(source)
    await db.flush()

    pool = await _get_arq_pool()
    job = await pool.enqueue_job("ingest_url_task", str(source.id))
    source.job_id = job.job_id
    await db.commit()

    return {
        "id": str(source.id),
        "title": source.title,
        "status": source.status,
        "scope_type": source.scope_type,
        "scope_id": str(source.scope_id),
    }


# ---------------------------------------------------------------------------
# Workspace Wiki
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/wiki")
async def list_workspace_wiki(
    project_id: str,
    page_type: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """List wiki pages scoped to this workspace."""
    await _get_project_or_404(db, project_id)
    pid = uuid.UUID(project_id)

    from app.services import wiki_service
    pages = await wiki_service.list_pages(
        db,
        page_type=page_type,
        limit=limit,
        scope_type="project",
        scope_id=pid,
    )
    return [
        {
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "summary": p.summary,
            "knowledge_type_slugs": p.knowledge_type_slugs or [],
            "source_ids": [str(s) for s in (p.source_ids or [])],
            "scope_type": p.scope_type,
            "scope_id": str(p.scope_id) if p.scope_id else None,
            "version": p.version or 1,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in pages
    ]


@router.get("/projects/{project_id}/wiki/graph")
async def get_workspace_wiki_graph(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Return nodes/edges for workspace-scoped wiki graph visualization."""
    await _get_project_or_404(db, project_id)
    pid = uuid.UUID(project_id)

    from sqlalchemy import select
    from app.database.models import WikiLink, WikiPage
    from app.services import wiki_service

    pages = (await db.execute(
        select(WikiPage.slug, WikiPage.title, WikiPage.page_type)
        .where(
            WikiPage.scope_type == "project",
            WikiPage.scope_id == pid,
            WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG]),
        )
    )).all()

    slug_set = {r.slug for r in pages}

    edges = (await db.execute(select(WikiLink.from_slug, WikiLink.to_slug))).all()

    return {
        "nodes": [{"slug": r.slug, "title": r.title, "page_type": r.page_type} for r in pages],
        "edges": [
            {"from": r.from_slug, "to": r.to_slug}
            for r in edges
            if r.from_slug in slug_set and r.to_slug in slug_set
        ],
    }

