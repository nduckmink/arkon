"""
Admin settings router — provider config, connection testing, dashboard stats.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Source, Department, Employee
from app.database.repository import Repository
from app.services.auth_service import get_current_user, require_permission

router = APIRouter()


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

class DashboardStats(BaseModel):
    total_sources: int
    total_departments: int
    total_employees: int


@router.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    repo = Repository(db)
    return DashboardStats(
        total_sources=await repo.count(Source),
        total_departments=await repo.count(Department),
        total_employees=await repo.count(Employee),
    )


# ---------------------------------------------------------------------------
# Settings CRUD
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    """Batch update config values."""
    settings: dict[str, str]


class TestConnectionResult(BaseModel):
    success: bool
    message: str
    details: Optional[dict] = None


@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """Get current app settings (masked sensitive values for UI)."""
    from app.services.config_service import ConfigService

    svc = ConfigService(db)
    ui_config = await svc.get_all_for_ui()
    return ui_config


@router.put("/settings")
async def update_settings(
    body: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("settings.edit"),
):
    """Update config values in database."""
    from app.services.config_service import ConfigService

    svc = ConfigService(db)
    results = await svc.set_batch(body.settings)
    return {"updated": results}


# ---------------------------------------------------------------------------
# Provider connection testing
# ---------------------------------------------------------------------------

@router.post("/settings/test-providers", response_model=dict[str, TestConnectionResult])
async def test_all_providers(db: AsyncSession = Depends(get_db)):
    """Test all configured AI providers (embedding, LLM, vision)."""
    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(db)
    results = await registry.test_all()

    return {
        capability: TestConnectionResult(success=ok, message=msg)
        for capability, (ok, msg) in results.items()
    }


@router.post("/settings/test-embedding", response_model=TestConnectionResult)
async def test_embedding(db: AsyncSession = Depends(get_db)):
    """Test the configured embedding provider."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_embedding()
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


@router.post("/settings/test-llm", response_model=TestConnectionResult)
async def test_llm(db: AsyncSession = Depends(get_db)):
    """Test the configured LLM provider."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_llm()
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


@router.post("/settings/test-vision", response_model=TestConnectionResult)
async def test_vision(db: AsyncSession = Depends(get_db)):
    """Test the configured vision provider."""
    from app.ai.registry import ProviderRegistry

    try:
        registry = ProviderRegistry(db)
        provider = await registry.get_vision()
        if not provider:
            return TestConnectionResult(success=False, message="No vision provider configured")
        ok, msg = await provider.test_connection()
        return TestConnectionResult(success=ok, message=msg)
    except Exception as e:
        return TestConnectionResult(success=False, message=str(e))


# ---------------------------------------------------------------------------
# Supported providers list (for admin UI dropdowns)
# ---------------------------------------------------------------------------

@router.get("/settings/providers")
async def list_providers():
    """Get supported providers and models for each capability."""
    from app.ai.registry import SUPPORTED_PROVIDERS
    return SUPPORTED_PROVIDERS
