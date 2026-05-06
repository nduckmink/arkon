import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Form, Query
from loguru import logger
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Employee, Skill
from app.services.auth_service import get_current_user, require_admin, require_permission
from app.services.skill_service import SkillService
from app.services.permission_engine import (
    _get_user_permissions,
    build_skill_filter,
    can_access_skill,
)

router = APIRouter()

# --- Pydantic Models ---

class SkillResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    department_ids: List[uuid.UUID] = []
    department_names: List[str] = []
    current_version: int
    version_hash: Optional[str]
    status: str
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillVersionResponse(BaseModel):
    version_number: int
    version_hash: Optional[str]
    storage_path: Optional[str]
    changelog: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillListResponse(BaseModel):
    items: List[SkillResponse]
    total: int


class SkillDeleteRequest(BaseModel):
    ids: List[uuid.UUID]




class SkillBulkVisibilityRequest(BaseModel):
    skill_ids: List[uuid.UUID]
    scope_type: str
    scope_id: Optional[uuid.UUID] = None
    department_ids: Optional[List[uuid.UUID]] = None  # Legacy support


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    department_ids: Optional[List[uuid.UUID]] = None
    scope_type: Optional[str] = None
    scope_id: Optional[uuid.UUID] = None
    increment_version: bool = False




# --- Skill Routes ---

@router.post("/skills/upload")
async def upload_skills(
    files: List[UploadFile] = File(...),
    department_ids: Optional[List[uuid.UUID]] = Form(None),
    scope_type: str = Form("global"),
    scope_id: Optional[uuid.UUID] = Form(None),
    force: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Upload one or more ZIP packages containing AI skills."""
    # Scope validation
    perms = _get_user_permissions(user)
    if user.role != "admin" and "skill:create:all" not in perms:
        if "skill:create:own_dept" not in perms:
            raise HTTPException(403, "Permission required: skill:create")
        
        # User only has own_dept scope
        if department_ids and any(d_id != user.department_id for d_id in department_ids):
            raise HTTPException(403, "You can only assign skills to your own department")
        if scope_type == "department" and scope_id != user.department_id:
            raise HTTPException(403, "You can only assign skills to your own department")
        if scope_type == "global":
            # Auto-force to own department if trying to create global without :all permission?
            # Or just deny. Let's deny for now to be safe.
            raise HTTPException(403, "You do not have permission to create global skills")

    results = await SkillService.upload_skills(
        db, files, department_ids, scope_type, scope_id, force, user.id
    )
    return {"results": results}


@router.post("/skills/{slug}/reupload")
async def reupload_skill(
    slug: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Re-upload content for a specific skill to create a new version."""
    skill = await SkillService.get_skill(db, slug)
    if not await can_access_skill(db, user, skill, "create"):
        raise HTTPException(status_code=403, detail="Access denied")

    result = await SkillService.reupload_skill(db, slug, file, user.id)
    return result


@router.post("/skills/inspect-zip")
async def inspect_skill_zip(
    file: UploadFile = File(...),
    _user: Employee = require_permission("skill:create"),
):
    """Peek into a ZIP package to extract metadata without saving anything to the database."""
    result = await SkillService.inspect_zip(file)
    return result


@router.get("/skills", response_model=SkillListResponse)
async def list_skills(
    q: Optional[str] = Query(None),
    department_id: Optional[uuid.UUID] = Query(None),
    scope_type: Optional[str] = Query(None),
    scope_id: Optional[uuid.UUID] = Query(None),
    ids: Optional[List[uuid.UUID]] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List and filter skills available in the system."""
    # --- Scope filtering ---
    needs_filter, allowed_depts = build_skill_filter(user, "read")
    
    # If allowed_depts is None and needs_filter is True, it means NO permission
    if needs_filter and allowed_depts is None:
        return {"items": [], "total": 0}

    # Pass allowed_depts to service for filtering
    # Note: If allowed_depts is [], it means user can see Global (dept=None)
    # The service needs to handle this logic: (department_id IN allowed_depts) OR (department_id IS NULL)
    
    skills, total = await SkillService.list_skills(
        db, q, department_id, scope_type, scope_id, ids, cursor, limit,
        allowed_department_ids=allowed_depts if needs_filter else None
    )
    items = []
    for s in skills:
        resp = SkillResponse.model_validate(s)
        resp.department_ids = [sd.department_id for sd in s.departments]
        resp.department_names = [sd.department.name for sd in s.departments]
        items.append(resp)
    return {"items": items, "total": total}


@router.delete("/skills/bulk")
async def bulk_delete_skills(
    req: SkillDeleteRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Delete multiple skills at once."""
    if not req.ids:
        return {"message": "No skills selected"}
        
    # Check each skill
    for skill_id in req.ids:
        skill = await db.get(Skill, skill_id)
        if not skill: continue
        if not await can_access_skill(db, user, skill, "delete"):
            raise HTTPException(403, f"Access denied for skill {skill.name}")

    count = await SkillService.bulk_delete_skills(db, req.ids)
    return {"message": f"Queued {count} skills for deletion"}




@router.post("/skills/bulk/department")
async def bulk_change_visibility(
    req: SkillBulkVisibilityRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Change visibility/scope for multiple skills at once."""
    if not req.skill_ids:
        return {"message": "No skills provided"}
    
    # Check access to all skills
    for skill_id in req.skill_ids:
        skill = await db.get(Skill, skill_id)
        if not skill: continue
        if not await can_access_skill(db, user, skill, "edit"):
            raise HTTPException(403, f"Access denied for skill {skill.name}")

    # Handle legacy department_id if scope_type is not provided
    effective_scope_type = req.scope_type
    effective_scope_id = req.scope_id
    
    if not effective_scope_type and req.department_id:
        effective_scope_type = "department"
        effective_scope_id = req.department_id

    # Scope validation for new scope
    perms = _get_user_permissions(user)
    if user.role != "admin" and "skill:edit:all" not in perms:
        if effective_scope_type == "department" and effective_scope_id != user.department_id:
            raise HTTPException(403, "You can only assign skills to your own department")
        if effective_scope_type == "global":
            raise HTTPException(403, "You do not have permission to make skills global")

    count = await SkillService.bulk_change_scope(
        db, req.skill_ids, effective_scope_type, effective_scope_id
    )
    return {"updated": count, "message": f"Updated visibility for {count} skills"}




# --- Skill File Exploration ---

TEXT_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".sh", ".js", ".ts", ".tsx", 
    ".html", ".css", ".sql", ".env", ".cfg", ".ini", ".xml", ".csv", ".bat", ".ps1"
}

def is_text_file(filename: str) -> bool:
    import os
    _, ext = os.path.splitext(filename.lower())
    return ext in TEXT_EXTENSIONS

@router.get("/skills/{skill_id}/files")
async def list_skill_files(
    skill_id: uuid.UUID,
    version: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List all files within a skill's storage prefix in MinIO."""
    from app.services.storage_service import storage_service
    from app.database.models import SkillVersion
    
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    if not await can_access_skill(db, user, skill, "read"):
        raise HTTPException(status_code=403, detail="Access denied")
    
    logger.info(f"Listing files for skill {skill_id} (slug: {skill.slug}), version: {version}")

    # Determine prefix
    if version:
        stmt = select(SkillVersion).where(SkillVersion.skill_id == skill_id, SkillVersion.version_number == version)
        v_res = await db.execute(stmt)
        v_obj = v_res.scalars().first()
        if not v_obj:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")
        prefix = v_obj.storage_path
    else:
        prefix = skill.storage_path

    if not prefix:
        logger.warning(f"No storage_path found for skill {skill_id} version {version}")
        return []

    # List objects in MinIO
    from app.config import settings
    logger.info(f"Listing MinIO objects with bucket={settings.minio_bucket} prefix={prefix}")
    # Ensure prefix ends with / for clean relative path replacement
    prefix_for_list = prefix if prefix.endswith("/") else f"{prefix}/"
    logger.info(f"[Debug] Listing files: skill_id={skill_id}, version={version}, prefix_for_list={prefix_for_list}")
    objects = storage_service.client.list_objects(settings.minio_bucket, prefix=prefix_for_list, recursive=True)
    
    files = []
    for obj in objects:
        # Extract relative path from full object name
        rel_path = obj.object_name.replace(prefix_for_list, "", 1)
        if not rel_path: continue
        
        files.append({
            "path": rel_path,
            "size": obj.size,
            "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
            "is_text": is_text_file(rel_path)
        })
        
    return sorted(files, key=lambda x: x["path"])


@router.get("/skills/{skill_id}/files/content")
async def get_skill_file_content(
    skill_id: uuid.UUID,
    path: str,
    version: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Fetch text content for a specific file within a skill."""
    from app.services.storage_service import storage_service
    from app.database.models import SkillVersion
    
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    if not await can_access_skill(db, user, skill, "read"):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Determine prefix
    if version:
        stmt = select(SkillVersion).where(SkillVersion.skill_id == skill_id, SkillVersion.version_number == version)
        v_res = await db.execute(stmt)
        v_obj = v_res.scalars().first()
        if not v_obj:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")
        prefix = v_obj.storage_path
    else:
        prefix = skill.storage_path

    if not prefix:
        raise HTTPException(status_code=404, detail="Skill storage path not found")

    if not is_text_file(path):
        raise HTTPException(status_code=400, detail="Only text-based files can be viewed.")

    # Ensure exactly one slash between prefix and path
    p = prefix.rstrip("/")
    f = path.lstrip("/")
    full_path = f"{p}/{f}"
    
    logger.info(f"[Debug] Fetching content: skill_id={skill_id}, version={version}, prefix={prefix}, path={path}, full_path={full_path}")
    
    try:
        content_bytes = storage_service.download_file(full_path)
        return {"content": content_bytes.decode("utf-8", errors="ignore")}
    except Exception as e:
        logger.error(f"[Debug] Failed to read skill file {full_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read file content: {str(e)}")


# --- Individual Skill Routes (MUST BE LAST) ---

@router.get("/skills/{slug}", response_model=SkillResponse)
async def get_skill(
    slug: str,
    version: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Get detailed information for a single skill."""
    skill = await SkillService.get_skill(db, slug, version_number=version)
    
    # Check access using permission engine
    if not await can_access_skill(db, user, skill, "read"):
        raise HTTPException(status_code=403, detail="Access denied")

    resp = SkillResponse.model_validate(skill)
    resp.department_ids = [sd.department_id for sd in skill.departments]
    resp.department_names = [sd.department.name for sd in skill.departments]
    return resp


@router.get("/skills/{slug}/versions", response_model=List[SkillVersionResponse])
async def list_skill_versions(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("skill:read"),
):
    """List all versions for a specific skill."""
    return await SkillService.list_versions(db, slug)


@router.post("/skills/{slug}/set-latest")
async def set_latest_version(
    slug: str,
    version: int = Query(..., alias="version"),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Set a specific version as the latest/current version."""
    skill = await SkillService.get_skill(db, slug)
    if not await can_access_skill(db, user, skill, "edit"):
        raise HTTPException(status_code=403, detail="Access denied")

    skill = await SkillService.set_latest_version(db, slug, version)
    return {"message": f"Version {version} set as latest", "current_version": skill.current_version}


@router.delete("/skills/{slug}")
async def delete_skill(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Delete a single skill by its identifier."""
    skill = await SkillService.get_skill(db, slug)
    if not await can_access_skill(db, user, skill, "delete"):
        raise HTTPException(status_code=403, detail="Access denied")

    await SkillService.delete_skill(db, slug)
    return {"message": "Skill marked for deletion"}


@router.patch("/skills/{slug}", response_model=SkillResponse)
async def update_skill(
    slug: str,
    req: SkillUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Update a skill's metadata or documentation content."""
    skill = await SkillService.get_skill(db, slug)
    if not await can_access_skill(db, user, skill, "edit"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Scope validation for new department/scope
    perms = _get_user_permissions(user)
    if user.role != "admin" and "skill:edit:all" not in perms:
        # User only has own_dept scope
        if req.department_ids and any(d_id != user.department_id for d_id in req.department_ids):
            raise HTTPException(403, "You can only assign skills to your own department")
        if req.scope_type == "department" and req.scope_id != user.department_id:
            raise HTTPException(403, "You can only assign skills to your own department")
        if req.scope_type == "global":
            raise HTTPException(403, "You do not have permission to make skills global")

    # Pass explicit fields so service knows if department_id was explicitly set to None
    req_data = req.model_dump()
    req_data["_explicit_fields"] = req.model_fields_set
    
    updated_skill = await SkillService.update_skill(db, slug, req_data)
    
    resp = SkillResponse.model_validate(updated_skill)
    resp.department_ids = [sd.department_id for sd in updated_skill.departments]
    resp.department_names = [sd.department.name for sd in updated_skill.departments]
    return resp
