import io
import os
import uuid
import zipfile
import hashlib
from typing import List, Optional, Tuple, Dict, Any

import sqlalchemy as sa
from fastapi import HTTPException
from loguru import logger
from sqlalchemy import select, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Skill, SkillVersion, Department, SkillDepartment
from app.utils.text import slugify
from app.worker import get_arq_pool
from app.services.storage_service import storage_service



class SkillService:
    @staticmethod
    async def validate_zip_content(file_data: bytes, zip_name: str) -> str:
        """Validates ZIP and extracts README. Returns error message if invalid, None if valid."""
        try:
            with zipfile.ZipFile(io.BytesIO(file_data)) as zf:
                file_list = [f.filename.lower() for f in zf.infolist()]
                target_readme = f"{zip_name}/SKILL.md".lower()
                has_readme = any(f == "skill.md" or f == target_readme or f.endswith("/skill.md") for f in file_list)
                if not has_readme:
                    return "Missing SKILL.md file in package."
        except zipfile.BadZipFile:
            return "Invalid ZIP file."
        return None

    @staticmethod
    async def upload_skills(
        db: AsyncSession, 
        files: List[Any], 
        department_ids: Optional[List[uuid.UUID]], 
        scope_type: str,
        scope_id: Optional[uuid.UUID],
        force: bool, 
        current_user_id: uuid.UUID
    ) -> List[Any]:
        pool = await get_arq_pool()
        
        results = []
        duplicates = []
        jobs_to_enqueue = []

        try:
            for file in files:
                file_data = await file.read()
                file_hash = hashlib.sha256(file_data).hexdigest()
                name = file.filename.rsplit(".", 1)[0]
                
                # Validate ZIP
                err = await SkillService.validate_zip_content(file_data, name)
                if err:
                    results.append({"name": name, "status": "error" if "Invalid" in err else "rejected", "message": err})
                    continue

                # Check existing
                stmt = select(Skill).where(Skill.name == name)
                res = await db.execute(stmt)
                existing_skill = res.scalars().first()

                if existing_skill:
                    if not force:
                        duplicates.append(name)
                        continue
                    # Always prioritize department_ids for Skills
                    if scope_type == "department" or department_ids:
                        existing_skill.scope_type = "department"
                        existing_skill.scope_id = scope_id or (department_ids[0] if department_ids else None)
                        
                        # Update M2M departments
                        await db.execute(sa.delete(SkillDepartment).where(SkillDepartment.skill_id == existing_skill.id))
                        if department_ids:
                            for d_id in department_ids:
                                db.add(SkillDepartment(skill_id=existing_skill.id, department_id=d_id))
                    else:
                        existing_skill.scope_type = "global"
                        existing_skill.scope_id = None
                        await db.execute(sa.delete(SkillDepartment).where(SkillDepartment.skill_id == existing_skill.id))

                    
                    if existing_skill.version_hash == file_hash:
                        results.append({"name": name, "status": "updated_metadata", "message": "Metadata updated, content unchanged."})
                        continue
                    
                    new_version_num = existing_skill.current_version + 1
                    skill_id = existing_skill.id
                    existing_skill.status = "processing"
                    existing_skill.version_hash = file_hash
                    returned_obj = existing_skill
                else:
                    # For skills, we only care about departments vs global
                    effective_dept_ids = [scope_id] if (scope_type == "department" and scope_id) else (department_ids or [])
                    new_skill = Skill(
                        name=name, slug=slugify(name), status="processing", current_version=1,
                        version_hash=file_hash, 
                        scope_type="department" if effective_dept_ids else "global",
                        scope_id=effective_dept_ids[0] if effective_dept_ids else None
                    )
                    db.add(new_skill)
                    await db.flush()
                    skill_id = new_skill.id
                    
                    if effective_dept_ids:
                        for d_id in effective_dept_ids:
                            db.add(SkillDepartment(skill_id=skill_id, department_id=d_id))
                    
                    new_version_num = 1
                    returned_obj = new_skill

                new_version = SkillVersion(
                    skill_id=skill_id, version_number=new_version_num, version_hash=file_hash, created_by=current_user_id
                )
                db.add(new_version)
                await db.flush()

                temp_dir = "temp_uploads"
                os.makedirs(temp_dir, exist_ok=True)
                temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}.zip")
                with open(temp_path, "wb") as f:
                    f.write(file_data)
                jobs_to_enqueue.append((str(skill_id), str(new_version.id), temp_path, file.filename))
                
                # Append ORM object for frontend Response model validation if it's new or updated content
                results.append(returned_obj)

            if duplicates and not force:
                await db.rollback()
                raise HTTPException(status_code=409, detail={"message": "Duplicate skill names detected", "conflicts": duplicates})
                
            await db.commit()
            
            for job_args in jobs_to_enqueue:
                await pool.enqueue_job("ingest_skill_task", *job_args, _queue_name="skills_queue")
                
        except Exception as e:
            for job_args in jobs_to_enqueue:
                temp_path = job_args[2]
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass
            raise e
            
        return results

    @staticmethod
    async def reupload_skill(db: AsyncSession, slug: str, file: Any, current_user_id: uuid.UUID) -> Dict:
        pool = await get_arq_pool()
        try:
            skill_uuid = uuid.UUID(slug)
            stmt = select(Skill).where(Skill.id == skill_uuid)
        except ValueError:
            stmt = select(Skill).where(Skill.slug == slug)

        res = await db.execute(stmt)
        skill = res.scalars().first()
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")

        file_data = await file.read()
        file_hash = hashlib.sha256(file_data).hexdigest()
        zip_name = file.filename.rsplit(".", 1)[0]
        
        if zip_name != skill.name:
            raise HTTPException(status_code=400, detail=f"Filename mismatch. Expected '{skill.name}.zip', got '{file.filename}'.")

        if skill.version_hash == file_hash:
            return {"status": "skipped", "message": "Content unchanged. No new version created.", "skill_id": str(skill.id), "version": skill.current_version}

        err = await SkillService.validate_zip_content(file_data, zip_name)
        if err:
            raise HTTPException(status_code=400, detail=err)

        new_version_num = skill.current_version + 1
        skill.status = "processing"
        skill.version_hash = file_hash
        
        new_version = SkillVersion(skill_id=skill.id, version_number=new_version_num, version_hash=file_hash, created_by=current_user_id)
        db.add(new_version)
        
        temp_dir = "temp_uploads"
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}.zip")
        
        try:
            with open(temp_path, "wb") as f:
                f.write(file_data)
            await db.commit()
            await pool.enqueue_job("ingest_skill_task", str(skill.id), str(new_version.id), temp_path, file.filename, _queue_name="skills_queue")
        except Exception as e:
            if os.path.exists(temp_path):
                try: os.remove(temp_path)
                except: pass
            raise e
            
        return {"status": "processing", "skill_id": str(skill.id), "version": new_version_num}

    @staticmethod
    async def inspect_zip(file: Any) -> Dict:
        file_data = await file.read()
        name = file.filename.rsplit(".", 1)[0]
        readme_content = ""
        try:
            with zipfile.ZipFile(io.BytesIO(file_data)) as zf:
                target_readme = f"{name}/SKILL.md".lower()
                for member in zf.infolist():
                    curr = member.filename.lower()
                    if curr == "skill.md" or curr == target_readme or curr.endswith("/skill.md"):
                        with zf.open(member) as f:
                            readme_content = f.read().decode("utf-8", errors="ignore")
                        break
            return {"name": name, "description": readme_content}
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid ZIP file.")
        except Exception as e:
            logger.error(f"Error inspecting zip: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @staticmethod
    async def list_skills(
        db: AsyncSession, 
        q: Optional[str], 
        department_id: Optional[uuid.UUID], 
        scope_type: Optional[str],
        scope_id: Optional[uuid.UUID],
        ids: Optional[List[uuid.UUID]], 
        cursor: Optional[str], 
        limit: int,
        allowed_department_ids: Optional[List[uuid.UUID]] = None
    ) -> Tuple[List[Skill], int]:
        stmt = select(Skill).options(selectinload(Skill.departments).selectinload(SkillDepartment.department)).order_by(Skill.updated_at.desc(), Skill.id.desc())

        if cursor:
            ref_skill_res = await db.execute(select(Skill).where(Skill.slug == cursor))
            ref_skill = ref_skill_res.scalars().first()
            if ref_skill:
                stmt = stmt.where(or_(Skill.updated_at < ref_skill.updated_at, and_(Skill.updated_at == ref_skill.updated_at, Skill.id < ref_skill.id)))

        # --- RBAC Filtering ---
        if allowed_department_ids is not None:
            # Skill is visible if it's Global OR user's dept is in skill's depts
            # This is complex for M2M, usually we use EXISTS
            rbac_filter = or_(
                ~Skill.departments.any(), # Global
                Skill.departments.any(SkillDepartment.department_id.in_(allowed_department_ids))
            )
            stmt = stmt.where(rbac_filter)

        if q:
            filter_expr = or_(Skill.name.ilike(f"%{q}%"), Skill.description.ilike(f"%{q}%"))
            stmt = stmt.where(filter_expr)

        if ids:
            stmt = stmt.where(Skill.id.in_(ids))


        if department_id:
            stmt = stmt.where(Skill.departments.any(SkillDepartment.department_id == department_id))
            
        if scope_type:
            stmt = stmt.where(Skill.scope_type == scope_type)
            if scope_id:
                stmt = stmt.where(Skill.scope_id == scope_id)

        count_stmt = select(func.count(func.distinct(Skill.id))).select_from(Skill)
        
        # Apply filters to count_stmt as well
        if allowed_department_ids is not None:
            count_stmt = count_stmt.where(or_(
                ~Skill.departments.any(),
                Skill.departments.any(SkillDepartment.department_id.in_(allowed_department_ids))
            ))
            
        if ids:
            count_stmt = count_stmt.where(Skill.id.in_(ids))
        else:
            if q:
                count_stmt = count_stmt.where(or_(Skill.name.ilike(f"%{q}%"), Skill.description.ilike(f"%{q}%")))
            if department_id:
                count_stmt = count_stmt.where(Skill.departments.any(SkillDepartment.department_id == department_id))
            if scope_type:
                count_stmt = count_stmt.where(Skill.scope_type == scope_type)
                if scope_id:
                    count_stmt = count_stmt.where(Skill.scope_id == scope_id)

        total_res = await db.execute(count_stmt)
        total = total_res.scalar() or 0

        stmt = stmt.limit(limit)
        res = await db.execute(stmt)
        return res.scalars().unique().all(), total

    @staticmethod
    async def bulk_delete_skills(db: AsyncSession, ids: List[uuid.UUID]) -> int:
        if not ids: return 0
        pool = await get_arq_pool()
        stmt = sa.update(Skill).where(Skill.id.in_(ids)).values(status="deleting")
        await db.execute(stmt)
        await db.commit()
        for skill_id in ids:
            await pool.enqueue_job("delete_skill_task", str(skill_id), _queue_name="skills_queue")
        return len(ids)

    @staticmethod
    async def get_skill(db: AsyncSession, slug: str, version_number: Optional[int] = None) -> Skill:
        try:
            skill_uuid = uuid.UUID(slug)
            stmt = select(Skill).where(Skill.id == skill_uuid)
        except ValueError:
            stmt = select(Skill).where(Skill.slug == slug)
        stmt = stmt.options(selectinload(Skill.departments).selectinload(SkillDepartment.department))
        res = await db.execute(stmt)
        skill = res.scalars().first()
        if not skill or skill.status == "deleting":
            raise HTTPException(status_code=404, detail="Skill not found")
            
        # If a specific version is requested, load its description/content
        if version_number and version_number != skill.current_version:
            v_stmt = select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version_number == version_number)
            v_res = await db.execute(v_stmt)
            version = v_res.scalars().first()
            if not version:
                raise HTTPException(status_code=404, detail=f"Version {version_number} not found")
            
            # Try to fetch SKILL.md from this version's storage
            if version.storage_path:
                try:
                    # Try both direct path and skill-named subfolder path
                    possible_paths = [
                        f"{version.storage_path.rstrip('/')}/SKILL.md",
                        f"{version.storage_path.rstrip('/')}/{skill.name}/SKILL.md"
                    ]
                    
                    content_bytes = None
                    for path in possible_paths:
                        try:
                            content_bytes = storage_service.download_file(path)
                            if content_bytes: 
                                logger.debug(f"Found SKILL.md at: {path}")
                                break
                        except:
                            continue

                    if content_bytes:
                        skill.description = content_bytes.decode("utf-8", errors="ignore")
                except Exception as e:
                    logger.warning(f"Failed to fetch description for version {version_number}: {e}")
                    # Keep existing description if fetch fails

        return skill

    @staticmethod
    async def list_versions(db: AsyncSession, slug: str) -> List[SkillVersion]:
        skill = await SkillService.get_skill(db, slug)
        stmt = select(SkillVersion).where(SkillVersion.skill_id == skill.id).order_by(SkillVersion.version_number.desc())
        res = await db.execute(stmt)
        return res.scalars().all()

    @staticmethod
    async def set_latest_version(db: AsyncSession, slug: str, version_number: int) -> Skill:
        skill = await SkillService.get_skill(db, slug)
        if version_number == skill.current_version:
            return skill
            
        stmt = select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version_number == version_number)
        res = await db.execute(stmt)
        version = res.scalars().first()
        if not version:
            raise HTTPException(status_code=404, detail=f"Version {version_number} not found")
            
        # Update skill record
        skill.current_version = version_number
        skill.version_hash = version.version_hash
        skill.storage_path = version.storage_path
        
        # Sync description from SKILL.md of this version
        if version.storage_path:
            try:
                # Try both direct path and skill-named subfolder path
                possible_paths = [
                    f"{version.storage_path.rstrip('/')}/SKILL.md",
                    f"{version.storage_path.rstrip('/')}/{skill.name}/SKILL.md"
                ]
                
                content_bytes = None
                for path in possible_paths:
                    try:
                        content_bytes = storage_service.download_file(path)
                        if content_bytes: break
                    except:
                        continue

                if content_bytes:
                    skill.description = content_bytes.decode("utf-8", errors="ignore")
            except Exception as e:
                logger.error(f"Failed to sync description while setting latest version: {e}")

        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def delete_skill(db: AsyncSession, slug: str):
        skill = await SkillService.get_skill(db, slug)
        pool = await get_arq_pool()
        skill.status = "deleting"
        await db.commit()
        await pool.enqueue_job("delete_skill_task", str(skill.id), _queue_name="skills_queue")

    @staticmethod
    async def update_skill(db: AsyncSession, slug: str, req_data: dict) -> Skill:
        skill = await SkillService.get_skill(db, slug)
        
        name = req_data.get("name")
        description = req_data.get("description")
        department_ids = req_data.get("department_ids")
        increment_version = req_data.get("increment_version", False)
        is_department_explicit = "department_ids" in req_data.get("_explicit_fields", [])
        scope_type = req_data.get("scope_type")
        scope_id = req_data.get("scope_id")
        is_scope_explicit = "scope_type" in req_data.get("_explicit_fields", [])

        if name is not None and name != skill.name:
            stmt = select(Skill).where(Skill.name == name, Skill.id != skill.id)
            res = await db.execute(stmt)
            if res.scalars().first():
                raise HTTPException(status_code=409, detail=f"Skill with name '{name}' already exists.")
            skill.name = name
            skill.slug = slugify(name)

        if description is not None and description != skill.description:
            if increment_version:
                new_version_num = skill.current_version + 1
                content_hash = hashlib.sha256(description.encode()).hexdigest()
                skill.version_hash = content_hash
                new_v = SkillVersion(skill_id=skill.id, version_number=new_version_num, changelog="Manual update via UI", storage_path=f"skills/{skill.id}/versions/{new_version_num}/content/")
                db.add(new_v)
                skill.current_version = new_version_num
                skill.storage_path = new_v.storage_path
            
            skill.description = description
            if skill.storage_path:
                base_path = skill.storage_path.rstrip("/")
                object_name = f"{base_path}/SKILL.md"
                storage_service.upload_file(object_name=object_name, data=description.encode("utf-8"), content_type="text/markdown")

        # Skill visibility: only Department or Global
        if scope_type is not None or is_scope_explicit:
            if scope_type == "department" and scope_id:
                skill.scope_type = "department"
                skill.scope_id = scope_id
                # Update M2M
                await db.execute(sa.delete(SkillDepartment).where(SkillDepartment.skill_id == skill.id))
                db.add(SkillDepartment(skill_id=skill.id, department_id=scope_id))
            else:
                skill.scope_type = "global"
                skill.scope_id = None
                await db.execute(sa.delete(SkillDepartment).where(SkillDepartment.skill_id == skill.id))
        elif department_ids is not None or is_department_explicit:
            if department_ids:
                skill.scope_type = "department"
                skill.scope_id = department_ids[0] # Primary dept for legacy compatibility
                # Update M2M
                await db.execute(sa.delete(SkillDepartment).where(SkillDepartment.skill_id == skill.id))
                for d_id in department_ids:
                    db.add(SkillDepartment(skill_id=skill.id, department_id=d_id))
            else:
                skill.scope_type = "global"
                skill.scope_id = None
                await db.execute(sa.delete(SkillDepartment).where(SkillDepartment.skill_id == skill.id))


        await db.commit()
        await db.refresh(skill)
        return skill


    @staticmethod
    async def bulk_change_scope(
        db: AsyncSession, 
        skill_ids: List[uuid.UUID], 
        scope_type: str,
        scope_id: Optional[uuid.UUID]
    ) -> int:
        if not skill_ids: return 0
        
        # Sync department_ids for compatibility
        dept_ids = [scope_id] if (scope_type == "department" and scope_id) else []
        
        stmt = sa.update(Skill).where(Skill.id.in_(skill_ids)).values(
            scope_type=scope_type,
            scope_id=scope_id
        )
        await db.execute(stmt)

        # Update M2M for all skills
        await db.execute(sa.delete(SkillDepartment).where(SkillDepartment.skill_id.in_(skill_ids)))
        if dept_ids:
            for skill_id in skill_ids:
                for d_id in dept_ids:
                    db.add(SkillDepartment(skill_id=skill_id, department_id=d_id))
        await db.commit()
        return len(skill_ids)
