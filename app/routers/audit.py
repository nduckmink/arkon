"""
Audit log router — admin-only view of access decisions.
Implements audit_read capability from AccessControl.md.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import AuditLog, Employee
from app.services.auth_service import require_permission

router = APIRouter(prefix="/audit", tags=["audit"])


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class AuditEntryOut(BaseModel):
    id: str
    timestamp: str
    principal_id: str
    principal_type: str
    action: str
    resource_type: str
    resource_id: str
    scope_type: Optional[str] = None
    scope_id: Optional[str] = None
    decision: str
    reason: Optional[str] = None


class AuditListResponse(BaseModel):
    items: list[AuditEntryOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/log", response_model=AuditListResponse)
async def get_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    principal_id: Optional[str] = None,
    action: Optional[str] = None,
    decision: Optional[str] = None,
    resource_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("audit.read"),
):
    """
    Query audit log with pagination and filters.
    Admin-only endpoint.
    """
    stmt = select(AuditLog)

    if principal_id:
        stmt = stmt.where(AuditLog.principal_id == uuid.UUID(principal_id))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if decision:
        stmt = stmt.where(AuditLog.decision == decision)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate
    stmt = (
        stmt
        .order_by(desc(AuditLog.timestamp))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    return AuditListResponse(
        items=[
            AuditEntryOut(
                id=str(e.id),
                timestamp=e.timestamp.isoformat(),
                principal_id=str(e.principal_id),
                principal_type=e.principal_type,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                scope_type=e.scope_type,
                scope_id=str(e.scope_id) if e.scope_id else None,
                decision=e.decision,
                reason=e.reason,
            )
            for e in entries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
