"""
Department & Employee router — RBAC management for admin portal.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import Department, Employee, KnowledgeScope, Role, ScopeMembership, ScopeRole, ScopeType
from app.services.mcp_auth_service import MCPAuthService
from app.services.auth_service import get_current_user, require_admin, require_permission, hash_password

router = APIRouter()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class DepartmentCreate(BaseModel):
    name: str
    description: Optional[str] = None


class DepartmentOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    employee_count: int = 0

    class Config:
        from_attributes = True


class EmployeeCreate(BaseModel):
    name: str
    email: str
    password: Optional[str] = None  # Optional on update
    role: str = "employee"  # "admin" or "employee"
    department_id: str
    custom_role_id: Optional[str] = None


class EmployeeOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    department_id: str
    department_name: str = ""
    is_active: bool
    has_token: bool
    last_connected: Optional[str] = None
    custom_role_id: Optional[str] = None
    custom_role_name: Optional[str] = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    token: str
    employee_name: str
    instructions: str


class ScopeCreate(BaseModel):
    department_id: Optional[str] = None
    employee_id: Optional[str] = None
    scope_type: str = "grant"
    knowledge_type_slugs: Optional[list[str]] = None  # e.g. ["sop", "product"]
    source_ids: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Department CRUD
# ---------------------------------------------------------------------------

@router.get("/departments")
async def list_departments(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("departments.read"),
):
    """List all departments with employee counts."""
    stmt = select(Department).options(selectinload(Department.employees))
    result = await db.execute(stmt)
    departments = result.scalars().all()

    return [
        DepartmentOut(
            id=str(d.id),
            name=d.name,
            description=d.description,
            employee_count=len(d.employees),
        )
        for d in departments
    ]


@router.post("/departments", status_code=201)
async def create_department(
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("departments.create"),
):
    """Create a new department."""
    dept = Department(name=body.name, description=body.description)
    db.add(dept)
    await db.flush()
    return {"id": str(dept.id), "name": dept.name}


@router.put("/departments/{dept_id}")
async def update_department(
    dept_id: str,
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("departments.edit"),
):
    dept = await db.get(Department, uuid.UUID(dept_id))
    if not dept:
        raise HTTPException(404, "Department not found")
    dept.name = body.name
    dept.description = body.description
    await db.flush()
    return {"id": str(dept.id), "name": dept.name}


@router.delete("/departments/{dept_id}")
async def delete_department(
    dept_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("departments.delete"),
):
    dept = await db.get(Department, uuid.UUID(dept_id))
    if not dept:
        raise HTTPException(404, "Department not found")
    await db.delete(dept)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Employee CRUD
# ---------------------------------------------------------------------------

@router.get("/employees")
async def list_employees(
    department_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("employees.read"),
):
    """List employees, optionally filtered by department."""
    stmt = (
        select(Employee)
        .options(selectinload(Employee.department), selectinload(Employee.custom_role))
    )
    if department_id:
        stmt = stmt.where(Employee.department_id == uuid.UUID(department_id))
    stmt = stmt.order_by(Employee.name)
    result = await db.execute(stmt)
    employees = result.scalars().all()

    return [
        EmployeeOut(
            id=str(e.id),
            name=e.name,
            email=e.email,
            role=e.role,
            department_id=str(e.department_id),
            department_name=e.department.name if e.department else "",
            is_active=e.is_active,
            has_token=bool(e.mcp_token),
            last_connected=e.last_connected.isoformat() if e.last_connected else None,
            custom_role_id=str(e.custom_role_id) if e.custom_role_id else None,
            custom_role_name=e.custom_role.name if e.custom_role else None,
        )
        for e in employees
    ]


@router.post("/employees", status_code=201)
async def create_employee(
    body: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("employees.create"),
):
    """Create a new employee (admin only)."""
    dept = await db.get(Department, uuid.UUID(body.department_id))
    if not dept:
        raise HTTPException(400, "Department not found")

    if not body.password:
        raise HTTPException(400, "Password is required")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if body.role not in ("admin", "employee"):
        raise HTTPException(400, "Role must be 'admin' or 'employee'")

    emp = Employee(
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        department_id=uuid.UUID(body.department_id),
        custom_role_id=uuid.UUID(body.custom_role_id) if body.custom_role_id else None,
    )
    db.add(emp)
    await db.flush()

    # Auto-create scope memberships
    # Global scope: admin gets admin role, employee gets reader
    global_role = ScopeRole.ADMIN.value if body.role == "admin" else ScopeRole.READER.value
    db.add(ScopeMembership(
        employee_id=emp.id,
        scope_type=ScopeType.GLOBAL.value,
        scope_id=None,
        role=global_role,
        granted_by_id=_user.id,
    ))
    # Department scope: contributor by default
    db.add(ScopeMembership(
        employee_id=emp.id,
        scope_type=ScopeType.DEPARTMENT.value,
        scope_id=uuid.UUID(body.department_id),
        role=ScopeRole.CONTRIBUTOR.value,
        granted_by_id=_user.id,
    ))
    await db.flush()

    return {"id": str(emp.id), "name": emp.name, "email": emp.email}


@router.put("/employees/{emp_id}")
async def update_employee(
    emp_id: str,
    body: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("employees.edit"),
):
    emp = await db.get(Employee, uuid.UUID(emp_id))
    if not emp:
        raise HTTPException(404, "Employee not found")
    emp.name = body.name
    emp.email = body.email
    emp.role = body.role
    emp.department_id = uuid.UUID(body.department_id)
    emp.custom_role_id = uuid.UUID(body.custom_role_id) if body.custom_role_id else None
    if body.password:
        emp.password_hash = hash_password(body.password)
    await db.flush()
    return {"id": str(emp.id), "name": emp.name}


@router.delete("/employees/{emp_id}")
async def delete_employee(
    emp_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("employees.delete"),
):
    emp = await db.get(Employee, uuid.UUID(emp_id))
    if not emp:
        raise HTTPException(404, "Employee not found")
    await db.delete(emp)
    return {"deleted": True}


@router.patch("/employees/{emp_id}/toggle")
async def toggle_employee(
    emp_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("employees.edit"),
):
    """Activate or deactivate an employee."""
    emp = await db.get(Employee, uuid.UUID(emp_id))
    if not emp:
        raise HTTPException(404, "Employee not found")
    emp.is_active = not emp.is_active
    await db.flush()
    return {"id": str(emp.id), "is_active": emp.is_active}


# ---------------------------------------------------------------------------
# MCP Token Management
# ---------------------------------------------------------------------------

@router.post("/employees/{emp_id}/token", response_model=TokenResponse)
async def generate_mcp_token(
    emp_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("employees.edit"),
):
    """Generate (or regenerate) an MCP token for an employee."""
    emp = await db.get(Employee, uuid.UUID(emp_id))
    if not emp:
        raise HTTPException(404, "Employee not found")

    auth_svc = MCPAuthService(db)
    token = await auth_svc.generate_token(emp.id)

    return TokenResponse(
        token=token,
        employee_name=emp.name,
        instructions=(
            f"Add this to Claude Desktop config:\n"
            f'{{"mcpServers": {{"arkon": {{"url": "https://your-server/mcp", '
            f'"headers": {{"Authorization": "Bearer {token}"}}}}}}}}'
        ),
    )


@router.delete("/employees/{emp_id}/token")
async def revoke_mcp_token(
    emp_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("employees.edit"),
):
    """Revoke an employee's MCP token."""
    auth_svc = MCPAuthService(db)
    revoked = await auth_svc.revoke_token(uuid.UUID(emp_id))
    if not revoked:
        raise HTTPException(404, "Employee not found or has no token")
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Knowledge Scope Management
# ---------------------------------------------------------------------------

@router.get("/departments/{dept_id}/scopes")
async def get_department_scopes(
    dept_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("scopes.read"),
):
    """Get knowledge scopes for a department."""
    stmt = select(KnowledgeScope).where(
        KnowledgeScope.department_id == uuid.UUID(dept_id),
        KnowledgeScope.employee_id == None,
    )
    result = await db.execute(stmt)
    scopes = result.scalars().all()

    return [
        {
            "id": str(s.id),
            "scope_type": s.scope_type,
            "knowledge_type_slugs": s.knowledge_type_slugs,
            "source_ids": s.source_ids,
        }
        for s in scopes
    ]


@router.get("/employees/{emp_id}/scopes")
async def get_employee_scopes(
    emp_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("scopes.read"),
):
    """Get personal knowledge scopes for an employee."""
    stmt = select(KnowledgeScope).where(
        KnowledgeScope.employee_id == uuid.UUID(emp_id),
    )
    result = await db.execute(stmt)
    scopes = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "scope_type": s.scope_type,
            "knowledge_type_slugs": s.knowledge_type_slugs,
            "source_ids": s.source_ids,
        }
        for s in scopes
    ]


@router.post("/scopes", status_code=201)
async def create_scope(
    body: ScopeCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("scopes.manage"),
):
    """Create a new knowledge scope."""
    scope = KnowledgeScope(
        department_id=uuid.UUID(body.department_id) if body.department_id else None,
        employee_id=uuid.UUID(body.employee_id) if body.employee_id else None,
        scope_type=body.scope_type,
        knowledge_type_slugs=body.knowledge_type_slugs,
        source_ids=body.source_ids,
    )
    db.add(scope)
    await db.flush()
    return {"id": str(scope.id)}


@router.delete("/scopes/{scope_id}")
async def delete_scope(
    scope_id: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("scopes.manage"),
):
    """Delete a knowledge scope."""
    scope = await db.get(KnowledgeScope, uuid.UUID(scope_id))
    if not scope:
        raise HTTPException(404, "Scope not found")
    await db.delete(scope)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Self-Service: Employee gets their own MCP token
# ---------------------------------------------------------------------------

@router.post("/my/mcp-token", response_model=TokenResponse)
async def get_my_mcp_token(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Generate (or show) the current employee's own MCP token.
    This is the self-service endpoint employees use from their portal.
    """
    auth_svc = MCPAuthService(db)

    if not current_user.mcp_token:
        token = await auth_svc.generate_token(current_user.id)
    else:
        token = current_user.mcp_token

    return TokenResponse(
        token=token,
        employee_name=current_user.name,
        instructions=(
            f"Add this to Claude Desktop config:\n"
            f'{{"mcpServers": {{"arkon": {{"url": "https://your-server/mcp", '
            f'"headers": {{"Authorization": "Bearer {token}"}}}}}}}}'
        ),
    )


@router.delete("/my/mcp-token")
async def revoke_my_mcp_token(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Revoke the current employee's own MCP token."""
    auth_svc = MCPAuthService(db)
    await auth_svc.revoke_token(current_user.id)
    return {"revoked": True}

