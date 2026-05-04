"""
Auth Service — JWT-based authentication for Admin Portal and Employee Portal.

Handles:
  - Password hashing (bcrypt)
  - JWT token generation and verification
  - Login / logout (stateless JWT)
  - Role-based access (admin vs employee)
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.database.models import Employee, Role


# JWT config
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

def create_access_token(employee_id: str, role: str, name: str) -> str:
    """Create a signed JWT token."""
    payload = {
        "sub": employee_id,
        "role": role,
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token. Returns payload or None."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ---------------------------------------------------------------------------
# Login / authenticate
# ---------------------------------------------------------------------------

async def authenticate_employee(
    db: AsyncSession, email: str, password: str
) -> Optional[Employee]:
    """
    Verify email + password. Returns Employee or None.
    """
    stmt = (
        select(Employee)
        .where(Employee.email == email, Employee.is_active == True)
        .options(selectinload(Employee.department), selectinload(Employee.custom_role))
    )
    result = await db.execute(stmt)
    employee = result.scalar_one_or_none()

    if not employee or not employee.password_hash:
        return None
    if not verify_password(password, employee.password_hash):
        return None
    return employee


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Employee:
    """
    FastAPI dependency — extracts and validates JWT from Authorization header.
    Returns the authenticated Employee.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    result = await db.execute(
        select(Employee)
        .options(selectinload(Employee.department), selectinload(Employee.custom_role))
        .where(Employee.id == uuid.UUID(payload["sub"]))
    )
    employee = result.scalar_one_or_none()
    if not employee or not employee.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found or deactivated",
        )

    return employee


async def require_admin(
    current_user: Employee = Depends(get_current_user),
) -> Employee:
    """
    FastAPI dependency — requires admin role.
    Use on admin-only endpoints.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def require_permission(permission: str):
    """
    FastAPI dependency factory — checks a specific permission on the employee's custom role.
    Admins bypass all permission checks.
    Legacy permission names are auto-migrated during the check.

    Usage: Depends(require_permission("kb.read"))
    """
    async def _check(current_user: Employee = Depends(get_current_user)) -> Employee:
        if current_user.role == "admin":
            return current_user
        if current_user.custom_role:
            # Auto-migrate legacy permission names stored in the DB
            stored = current_user.custom_role.permissions or []
            from app.routers.roles import _LEGACY_MAP
            effective: set[str] = set()
            for p in stored:
                if p in _LEGACY_MAP:
                    effective.update(_LEGACY_MAP[p])
                else:
                    effective.add(p)
            if permission in effective:
                return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission required: {permission}",
        )
    return Depends(_check)
