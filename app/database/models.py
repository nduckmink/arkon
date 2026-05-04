"""
SQLAlchemy ORM models for all database tables.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from pgvector.sqlalchemy import Vector
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Scope-based RBAC Enums & Constants
# ---------------------------------------------------------------------------

class ScopeType(str, PyEnum):
    """Types of knowledge scopes."""
    GLOBAL = "global"
    PROJECT = "project"
    DEPARTMENT = "department"
    TEAM = "team"


class ScopeRole(str, PyEnum):
    """Role within a scope (ordered by privilege level)."""
    READER = "reader"
    CONTRIBUTOR = "contributor"
    OWNER = "owner"
    ADMIN = "admin"


ROLE_HIERARCHY: dict["ScopeRole", int] = {
    ScopeRole.READER: 0,
    ScopeRole.CONTRIBUTOR: 1,
    ScopeRole.OWNER: 2,
    ScopeRole.ADMIN: 3,
}


class Action(str, PyEnum):
    """Atomic actions on resources (SRS §4.2)."""
    READ = "read"
    LIST = "list"
    COMMENT = "comment"
    PROPOSE_EDIT = "propose_edit"
    APPROVE_EDIT = "approve_edit"
    WRITE_DIRECT = "write_direct"
    DELETE = "delete"
    CREATE_LINK = "create_link"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_SETTINGS = "manage_settings"


# Role × Action permission matrix (SRS §4.3)
ROLE_PERMISSIONS: dict["ScopeRole", set["Action"]] = {
    ScopeRole.READER: {Action.READ, Action.LIST},
    ScopeRole.CONTRIBUTOR: {
        Action.READ, Action.LIST, Action.COMMENT,
        Action.PROPOSE_EDIT, Action.CREATE_LINK,
    },
    ScopeRole.OWNER: {
        Action.READ, Action.LIST, Action.COMMENT,
        Action.PROPOSE_EDIT, Action.APPROVE_EDIT,
        Action.CREATE_LINK, Action.DELETE, Action.MANAGE_MEMBERS,
    },
    ScopeRole.ADMIN: set(Action),
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
    # --- Scope-based access control ---
    scope_type: Mapped[str] = mapped_column(
        String(20), default=ScopeType.GLOBAL.value,
        comment="Scope type: global, project, department, team",
    )
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="Scope entity ID. Null for global scope.",
    )
    knowledge_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_types.id", ondelete="SET NULL"),
        nullable=True,
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"),
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
    department: Mapped[Optional["Department"]] = relationship(back_populates="sources")
    knowledge_type: Mapped[Optional["KnowledgeType"]] = relationship()
    contributor: Mapped[Optional["Employee"]] = relationship(
        foreign_keys=[contributed_by_employee_id]
    )


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
    # --- Scope-based access control ---
    scope_type: Mapped[str] = mapped_column(
        String(20), default=ScopeType.GLOBAL.value,
        comment="Scope type: global, project, department, team",
    )
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="Scope entity ID. Null for global scope.",
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
    Replaces a dedicated graph DB for 1-2 hop queries (backlinks, neighborhood).
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
# RBAC: Roles, Departments, Employees, Knowledge Scopes
# ---------------------------------------------------------------------------


class Role(Base):
    """Custom permission role assignable to employees."""
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
    knowledge_scopes: Mapped[list["KnowledgeScope"]] = relationship(
        back_populates="department", cascade="all, delete-orphan"
    )
    sources: Mapped[list["Source"]] = relationship(back_populates="department")


class Employee(Base):
    """
    Employee — authenticates via login (JWT) or MCP token.
    Role 'admin' has full access to admin portal.
    Role 'employee' can view their scoped knowledge and get MCP tokens.
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
        comment="admin or employee",
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
    personal_scopes: Mapped[list["KnowledgeScope"]] = relationship(
        back_populates="employee",
        foreign_keys="KnowledgeScope.employee_id",
    )

    __table_args__ = (
        Index("ix_employees_mcp_token", "mcp_token"),
        Index("ix_employees_department_id", "department_id"),
        Index("ix_employees_email", "email"),
    )


class KnowledgeScope(Base):
    """
    Defines what knowledge a department or individual employee can access.

    Scoping rules:
      - department_id set, employee_id null → applies to entire department
      - employee_id set → personal override (grant or restrict)
      - knowledge_type filter → only specific types (sop, product...)
      - source_ids filter → only specific documents
    """
    __tablename__ = "knowledge_scopes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=True,
    )
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=True,
    )
    scope_type: Mapped[str] = mapped_column(
        String(20), default="grant",
        comment="grant = allow access, deny = restrict access",
    )
    knowledge_type_slugs: Mapped[Optional[list[str]]] = mapped_column(
        "knowledge_types", ARRAY(String),
        comment="Filter by KnowledgeType slugs (admin-defined). Null = all types.",
    )
    source_ids: Mapped[Optional[list]] = mapped_column(
        JSONB,
        comment="Specific source UUIDs. Null = all sources matching knowledge_types.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    department: Mapped[Optional["Department"]] = relationship(back_populates="knowledge_scopes")
    employee: Mapped[Optional["Employee"]] = relationship(
        back_populates="personal_scopes",
        foreign_keys=[employee_id],
    )


# ---------------------------------------------------------------------------
# Projects — cross-functional, temporary knowledge contexts
# ---------------------------------------------------------------------------

class Project(Base):
    """
    A named workspace grouping employees and sources across departments.
    Can represent a project, customer engagement, or any cross-functional context.
    workspace_type distinguishes between 'project' and 'customer'.
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
    """Associates an employee with a project."""
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
        String(20), default="member",
        comment="owner or member",
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
# Scope-based RBAC: Membership & Audit
# ---------------------------------------------------------------------------

class ScopeMembership(Base):
    """
    Maps a principal (employee) to a scope with a specific role.
    Replaces KnowledgeScope with a cleaner, per-scope-role model.

    Scoping rules:
      - scope_type='global', scope_id=None → org-wide access
      - scope_type='project', scope_id=<project.id> → project-level access
      - scope_type='department', scope_id=<department.id> → department-level access
      - scope_type='team', scope_id=<team.id> → team-level access

    Implements FR-10, FR-12, FR-13 from AccessControl.md.
    """
    __tablename__ = "scope_memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"),
    )
    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="Scope type: global, project, department, team",
    )
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="Scope entity ID. Null for global scope.",
    )
    role: Mapped[str] = mapped_column(
        String(20), default=ScopeRole.READER.value,
        comment="Role within this scope: reader, contributor, owner, admin",
    )
    granted_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        comment="Who granted this membership (FR-11)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        foreign_keys=[employee_id],
    )
    granted_by: Mapped[Optional["Employee"]] = relationship(
        foreign_keys=[granted_by_id],
    )

    __table_args__ = (
        UniqueConstraint("employee_id", "scope_type", "scope_id",
                         name="uq_scope_membership_employee_scope"),
        Index("ix_scope_memberships_employee_id", "employee_id"),
        Index("ix_scope_memberships_scope", "scope_type", "scope_id"),
    )


class AuditLog(Base):
    """
    Append-only access decision log.
    Records every allow/deny decision for compliance and debugging.

    Implements NFR-01, NFR-02 from AccessControl.md.
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
        comment="Type of resource: source, wiki_page, scope_membership...",
    )
    resource_id: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="UUID or identifier of the resource",
    )
    scope_type: Mapped[Optional[str]] = mapped_column(String(20))
    scope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    decision: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="allow or deny",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Human-readable reason for the decision (FR-31)",
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
