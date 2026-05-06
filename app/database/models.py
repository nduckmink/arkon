"""
SQLAlchemy ORM models for all database tables.

Permission Architecture v2 — Dual-Realm:
  - Global Realm: scoped permissions (doc:read:own_dept, doc:read:all, etc.)
  - Workspace Realm: membership-gated (viewer / contributor / editor / admin)
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    Integer,
    Boolean,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID, ENUM as PgEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ScopeType(str, PyEnum):
    """Scope for sources/wiki: global or project (workspace).
    Department visibility is handled via source_departments M2M.
    """
    GLOBAL = "global"
    PROJECT = "project"


class WorkspaceRole(str, PyEnum):
    """Role within a workspace (ordered by privilege level)."""
    VIEWER = "viewer"
    CONTRIBUTOR = "contributor"
    EDITOR = "editor"
    ADMIN = "admin"


WORKSPACE_ROLE_HIERARCHY: dict[WorkspaceRole, int] = {
    WorkspaceRole.VIEWER: 0,
    WorkspaceRole.CONTRIBUTOR: 1,
    WorkspaceRole.EDITOR: 2,
    WorkspaceRole.ADMIN: 3,
}


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


# ---------------------------------------------------------------------------
# Sources — raw documents (file/URL)
# ---------------------------------------------------------------------------

class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[Optional[str]] = mapped_column(String(500))
    full_text: Mapped[Optional[str]] = mapped_column(Text)
    source_type: Mapped[Optional[str]] = mapped_column(String(50))  # "file", "url"
    # --- Scope: global or project (workspace) ---
    scope_type: Mapped[str] = mapped_column(
        String(20), default=ScopeType.GLOBAL.value,
        comment="Scope type: global or project",
    )
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="Project/workspace ID when scope_type=project. Null for global.",
    )
    knowledge_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_types.id", ondelete="SET NULL"),
        nullable=True,
    )
    contributed_by_employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    file_path: Mapped[Optional[str]] = mapped_column(String(1000))
    url: Mapped[Optional[str]] = mapped_column(String(2000))
    minio_key: Mapped[Optional[str]] = mapped_column(String(500))
    file_name: Mapped[Optional[str]] = mapped_column(String(500))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[Optional[str]] = mapped_column(String(500))
    job_id: Mapped[Optional[str]] = mapped_column(String(200))
    # Heading-based TOC tree (PageIndex-style) built at ingest time from extracted markdown.
    # Schema: [{"title": str, "level": int, "page": int, "char_start": int, "char_end": int, "children": [...]}]
    outline_json: Mapped[Optional[list]] = mapped_column(JSONB)
    # Char offset (in full_text) where each extracted page begins.
    # Used by MCP `get_source_pages` to slice raw text by page range.
    page_offsets: Mapped[Optional[list[int]]] = mapped_column(JSONB)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    departments: Mapped[list["SourceDepartment"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    knowledge_type: Mapped[Optional["KnowledgeType"]] = relationship()
    contributor: Mapped[Optional["Employee"]] = relationship(
        foreign_keys=[contributed_by_employee_id]
    )


class SourceDepartment(Base):
    """Many-to-many: Source ↔ Department.
    A source with NO rows here is considered Global (visible to all).
    """
    __tablename__ = "source_departments"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Relationships
    source: Mapped["Source"] = relationship(back_populates="departments")
    department: Mapped["Department"] = relationship(back_populates="source_departments")


# ---------------------------------------------------------------------------
# Wiki — LLM-compiled persistent knowledge layer
# ---------------------------------------------------------------------------

class WikiPage(Base):
    """
    A markdown wiki page maintained by the LLM Wiki Compiler.
    Reserved slugs: '_index' (catalog), '_log' (chronological activity log).
    """
    __tablename__ = "wiki_pages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    page_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # --- Scope: global or project (workspace) ---
    scope_type: Mapped[str] = mapped_column(
        String(20), default=ScopeType.GLOBAL.value,
        comment="Scope type: global or project",
    )
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="Project/workspace ID. Null for global scope.",
    )
    knowledge_type_slugs: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list,
    )
    source_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    embedding = mapped_column(Vector(768), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_wiki_pages_page_type", "page_type"),
    )


class WikiLink(Base):
    """
    Derived edge between two wiki pages, parsed from `[[slug]]` patterns in content_md.
    Refreshed after every page upsert by wiki_service.refresh_links().
    """
    __tablename__ = "wiki_links"

    from_slug: Mapped[str] = mapped_column(String(300), nullable=False)
    to_slug: Mapped[str] = mapped_column(String(300), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("from_slug", "to_slug"),
        Index("ix_wiki_links_from_slug", "from_slug"),
        Index("ix_wiki_links_to_slug", "to_slug"),
    )


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

class Note(Base):
    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[Optional[str]] = mapped_column(String(500))
    content: Mapped[Optional[str]] = mapped_column(Text)
    note_type: Mapped[Optional[str]] = mapped_column(String(50))  # "human", "ai"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# App Config (key-value store for settings)
# ---------------------------------------------------------------------------

class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# Knowledge Types (admin-defined, dynamic)
# ---------------------------------------------------------------------------

class KnowledgeType(Base):
    """
    Admin-defined knowledge type — replaces hardcoded types.
    Examples: SOP, Product, HR Policy, Technical Spec, etc.
    """
    __tablename__ = "knowledge_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True,
        comment="URL-safe identifier, e.g. 'sop', 'product', 'hr-policy'",
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Display name, e.g. 'Standard Operating Procedure'",
    )
    color: Mapped[Optional[str]] = mapped_column(
        String(20), default="#6366f1",
        comment="Hex color for UI badge",
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# RBAC: Roles, Departments, Employees
# ---------------------------------------------------------------------------

class Role(Base):
    """Custom permission role assignable to employees.
    Permissions use scoped format: resource:action:scope
    e.g. 'doc:read:own_dept', 'doc:read:all', 'org:settings:manage'
    """
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    employees: Mapped[list["Employee"]] = relationship(back_populates="custom_role")


class Department(Base):
    """Organizational department — groups employees and scopes knowledge access."""
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    employees: Mapped[list["Employee"]] = relationship(
        back_populates="department", cascade="all, delete-orphan"
    )
    source_departments: Mapped[list["SourceDepartment"]] = relationship(
        back_populates="department", cascade="all, delete-orphan"
    )
    skill_departments: Mapped[list["SkillDepartment"]] = relationship(
        back_populates="department", cascade="all, delete-orphan"
    )


class Employee(Base):
    """
    Employee — authenticates via login (JWT) or MCP token.
    Role 'admin' has full access (bypasses all permission checks).
    Role 'employee' access is governed by custom_role permissions.
    """
    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(500),
        comment="bcrypt hash of password",
    )
    role: Mapped[str] = mapped_column(
        String(20), default="employee",
        comment="admin or employee — system-level role",
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE")
    )
    custom_role_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    mcp_token: Mapped[Optional[str]] = mapped_column(
        String(500), unique=True,
        comment="Bearer token for MCP authentication",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_connected: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    department: Mapped["Department"] = relationship(back_populates="employees")
    custom_role: Mapped[Optional["Role"]] = relationship(back_populates="employees")

    __table_args__ = (
        Index("ix_employees_mcp_token", "mcp_token"),
        Index("ix_employees_department_id", "department_id"),
        Index("ix_employees_email", "email"),
    )


# ---------------------------------------------------------------------------
# Workspaces (Projects) — membership-gated realm
# ---------------------------------------------------------------------------

class Project(Base):
    """
    A named workspace grouping employees and sources across departments.
    Can represent a project, customer engagement, or any cross-functional context.
    Access is purely membership-based — global role does NOT grant access.
    Admin (role='admin') can view all workspaces via workspace:view:all permission.
    """
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    workspace_type: Mapped[str] = mapped_column(
        String(20), default="project",
        comment="project or customer",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active",
        comment="active or archived",
    )
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    project_sources: Mapped[list["ProjectSource"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    created_by: Mapped[Optional["Employee"]] = relationship(foreign_keys=[created_by_id])


class ProjectMember(Base):
    """Associates an employee with a project/workspace.
    Role determines what the member can do within this workspace.
    """
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(
        String(20), default=WorkspaceRole.VIEWER.value,
        comment="viewer, contributor, editor, or admin",
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="members")
    employee: Mapped["Employee"] = relationship()

    __table_args__ = (
        Index("ix_project_members_employee_id", "employee_id"),
    )


class ProjectSource(Base):
    """Associates a source document with a project."""
    __tablename__ = "project_sources"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="project_sources")
    source: Mapped["Source"] = relationship()

    __table_args__ = (
        Index("ix_project_sources_source_id", "source_id"),
    )


# ---------------------------------------------------------------------------
# AI Skills — Versioned prompt packages and tools
# ---------------------------------------------------------------------------

class Skill(Base):
    """
    An AI Skill package (e.g. 'document-generator').
    Can be scoped to a department or global (NULL department).
    """
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(
        String(20), default="global",
        comment="Scope type: global, project, department, team",
    )
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="Scope entity ID. Null for global scope.",
    )
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    version_hash: Mapped[Optional[str]] = mapped_column(String(64))
    storage_path: Mapped[Optional[str]] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(
        PgEnum("active", "processing", "deleting", "deprecated", "archived", name="skill_status"),
        server_default="active",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    departments: Mapped[list["SkillDepartment"]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )
    versions: Mapped[list["SkillVersion"]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )


class SkillDepartment(Base):
    """Many-to-many: Skill ↔ Department.
    A skill with NO rows here is considered Global (visible to all).
    """
    __tablename__ = "skill_departments"

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Relationships
    skill: Mapped["Skill"] = relationship(back_populates="departments")
    department: Mapped["Department"] = relationship(back_populates="skill_departments")


class SkillVersion(Base):
    """Specific version of a skill."""
    __tablename__ = "skill_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE")
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_hash: Mapped[Optional[str]] = mapped_column(String(64))
    storage_path: Mapped[Optional[str]] = mapped_column(String(1000))
    changelog: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    skill: Mapped["Skill"] = relationship(back_populates="versions")
    author: Mapped[Optional["Employee"]] = relationship()

    __table_args__ = (
        Index("ix_skill_versions_skill_id", "skill_id"),
    )


# ---------------------------------------------------------------------------
# Scope-based RBAC: Membership & Audit
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

class AuditLog(Base):
    """
    Append-only access decision log.
    Records actions for compliance and debugging.
    """
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
        comment="Employee or agent ID",
    )
    principal_type: Mapped[str] = mapped_column(
        String(20), default="human",
        comment="human or agent",
    )
    action: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Action attempted (read, list, delete...)",
    )
    resource_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Type of resource: source, wiki_page, etc.",
    )
    resource_id: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="UUID or identifier of the resource",
    )
    decision: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="allow or deny",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Human-readable reason for the decision",
    )
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB,
        comment="Extra context (IP, user agent, request ID...)",
    )

    __table_args__ = (
        Index("ix_audit_log_timestamp", "timestamp"),
        Index("ix_audit_log_principal", "principal_id"),
        Index("ix_audit_log_resource", "resource_type", "resource_id"),
    )
