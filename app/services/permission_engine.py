"""
Permission Engine — resolves access decisions for Global and Workspace realms.

Global Realm:
  - Permissions are scoped: resource:action:own_dept or resource:action:all
  - own_dept = user's department_id matches source's departments (via source_departments)
  - all = no scope restriction
  - No departments on resource = Global (visible to everyone with the action permission)

Workspace Realm:
  - Pure membership check. Global role does NOT grant access.
  - Admin (role='admin') can view all workspaces.
  - Workspace role (viewer/contributor/editor/admin) determines actions within workspace.
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Employee,
    ProjectMember,
    Source,
    SourceDepartment,
    Skill,
    SkillDepartment,
    WorkspaceRole,
    WORKSPACE_ROLE_HIERARCHY,
)


# ---------------------------------------------------------------------------
# Permission string parsing
# ---------------------------------------------------------------------------

def parse_permission(perm: str) -> tuple[str, str, str]:
    """Parse 'resource:action:scope' → (resource, action, scope).
    For org permissions like 'org:departments:read' → ('org', 'departments', 'read').
    """
    parts = perm.split(":")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return perm, "", ""


def has_permission(permissions: list[str], resource: str, action: str, scope: str = "any") -> bool:
    """Check if a permission list contains the required permission.
    
    scope = "any" → matches either own_dept or all
    scope = "all" → only matches :all
    scope = "own_dept" → matches :own_dept or :all
    """
    perm_all = f"{resource}:{action}:all"
    perm_own = f"{resource}:{action}:own_dept"

    if scope == "all":
        return perm_all in permissions
    elif scope == "own_dept":
        return perm_all in permissions or perm_own in permissions
    else:  # "any"
        return perm_all in permissions or perm_own in permissions


def has_any_permission(permissions: list[str], resource: str, action: str) -> bool:
    """Check if user has any variant (own_dept or all) of a resource:action."""
    return has_permission(permissions, resource, action, "any")


def get_scope_level(permissions: list[str], resource: str, action: str) -> Optional[str]:
    """Get the effective scope level for a resource:action.
    Returns 'all', 'own_dept', or None.
    """
    perm_all = f"{resource}:{action}:all"
    perm_own = f"{resource}:{action}:own_dept"
    if perm_all in permissions:
        return "all"
    if perm_own in permissions:
        return "own_dept"
    return None


# ---------------------------------------------------------------------------
# Global Realm: Document access
# ---------------------------------------------------------------------------

async def can_access_document(
    db: AsyncSession,
    user: Employee,
    source: Source,
    action: str = "read",
) -> bool:
    """Check if user can perform action on a source document.
    
    Logic:
    1. Admin → always True
    2. User has doc:{action}:all → True
    3. User has doc:{action}:own_dept →
       a. Source has no departments (Global doc) → True
       b. Source has a department matching user.department_id → True
       c. Otherwise → False
    4. Otherwise → False
    """
    if user.role == "admin":
        return True

    permissions = _get_user_permissions(user)

    # Has :all scope
    if f"doc:{action}:all" in permissions:
        return True

    # Has :own_dept scope
    if f"doc:{action}:own_dept" not in permissions:
        return False

    # Check if source is global (no departments) or belongs to user's department
    dept_result = await db.execute(
        select(SourceDepartment.department_id)
        .where(SourceDepartment.source_id == source.id)
    )
    source_dept_ids = {row[0] for row in dept_result.all()}

    if not source_dept_ids:
        # No departments = Global doc
        return True

    return user.department_id in source_dept_ids


def build_document_filter(user: Employee, action: str = "read"):
    """Build SQLAlchemy filter clauses for listing documents based on user permissions.
    
    Returns: (needs_filter: bool, filter_clauses: list)
    If needs_filter is False, show all documents.
    """
    if user.role == "admin":
        return False, []

    permissions = _get_user_permissions(user)

    if f"doc:{action}:all" in permissions:
        return False, []

    if f"doc:{action}:own_dept" in permissions:
        # Filter: source has no departments (global) OR has user's department
        return True, [user.department_id]

    # No permission at all — empty result
    return True, None


# ---------------------------------------------------------------------------
# Global Realm: AI Skill access
# ---------------------------------------------------------------------------

async def can_access_skill(
    db: AsyncSession,
    user: Employee,
    skill: Skill,
    action: str = "read",
) -> bool:
    """Check if user can perform action on an AI skill.
    
    Logic:
    1. Admin → True
    2. User has skill:{action}:all → True
    3. User has skill:{action}:own_dept →
       a. Skill has no department (Global) → True
       b. Skill's department matches user.department_id → True
       c. Otherwise → False
    4. Otherwise → False
    """
    if user.role == "admin":
        return True

    permissions = _get_user_permissions(user)

    if f"skill:{action}:all" in permissions:
        return True

    # Skill visible if it's Global (no depts) OR user's dept is in skill's depts
    skill_dept_ids = {sd.department_id for sd in skill.departments}
    if not skill_dept_ids:
        return True

    return user.department_id in skill_dept_ids


def build_skill_filter(user: Employee, action: str = "read"):
    """Build SQLAlchemy filter clauses for listing skills.
    Returns: (needs_filter: bool, filter_clauses: list)
    """
    if user.role == "admin":
        return False, []

    permissions = _get_user_permissions(user)

    if f"skill:{action}:all" in permissions:
        return False, []

    if f"skill:{action}:own_dept" in permissions:
        # Filter: skill has no department (global) OR matches user's department
        # This will be handled in SkillService.list_skills via allowed_department_ids
        return True, [user.department_id]

    return True, None


# ---------------------------------------------------------------------------
# Workspace Realm: Membership check
# ---------------------------------------------------------------------------

async def can_access_workspace(
    db: AsyncSession,
    user: Employee,
    workspace_id: uuid.UUID,
) -> bool:
    """Check if user can access a workspace.
    Admin (role='admin') can always access all workspaces.
    Otherwise, user must be a member.
    """
    if user.role == "admin":
        return True

    result = await db.execute(
        select(ProjectMember.role)
        .where(
            ProjectMember.project_id == workspace_id,
            ProjectMember.employee_id == user.id,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_workspace_role(
    db: AsyncSession,
    user: Employee,
    workspace_id: uuid.UUID,
) -> Optional[str]:
    """Get user's role in a workspace.
    Admin gets 'admin' role in all workspaces.
    Returns None if user is not a member.
    """
    if user.role == "admin":
        return WorkspaceRole.ADMIN.value

    result = await db.execute(
        select(ProjectMember.role)
        .where(
            ProjectMember.project_id == workspace_id,
            ProjectMember.employee_id == user.id,
        )
    )
    return result.scalar_one_or_none()


def workspace_role_can(member_role: str, required_role: str) -> bool:
    """Check if a workspace role meets the minimum required level."""
    try:
        member_level = WORKSPACE_ROLE_HIERARCHY[WorkspaceRole(member_role)]
        required_level = WORKSPACE_ROLE_HIERARCHY[WorkspaceRole(required_role)]
        return member_level >= required_level
    except (ValueError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_permissions(user: Employee) -> set[str]:
    """Extract effective permissions from user's custom role."""
    if user.role == "admin":
        from app.services.permissions import ALL_PERMISSIONS
        return set(ALL_PERMISSIONS)

    if not user.custom_role:
        # Fallback — should rarely hit since auth_service auto-attaches Employee role
        from app.services.permissions import EMPLOYEE_DEFAULT_PERMISSIONS
        return set(EMPLOYEE_DEFAULT_PERMISSIONS)

    stored = user.custom_role.permissions or []

    # Auto-migrate legacy permission names
    from app.services.permissions import LEGACY_PERMISSION_MAP
    effective: set[str] = set()
    for p in stored:
        if p in LEGACY_PERMISSION_MAP:
            effective.update(LEGACY_PERMISSION_MAP[p])
        else:
            effective.add(p)

    return effective


def get_effective_permissions(user: Employee) -> list[str]:
    """Public version — returns sorted list for API responses."""
    from app.services.permissions import ALL_PERMISSIONS
    perms = _get_user_permissions(user)
    return sorted(p for p in perms if p in ALL_PERMISSIONS)
