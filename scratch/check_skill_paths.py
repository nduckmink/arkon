
import asyncio
import uuid
from sqlalchemy import select
from app.database import async_session_factory
from app.database.models import Skill, SkillVersion

async def check_paths(skill_slug):
    async with async_session_factory() as session:
        # Find skill
        stmt = select(Skill).where(Skill.slug == skill_slug)
        res = await session.execute(stmt)
        skill = res.scalars().first()
        if not skill:
            print(f"Skill {skill_slug} not found")
            return

        print(f"Skill: {skill.name} ({skill.id})")
        print(f"Latest storage_path: {skill.storage_path}")
        
        # Find versions
        stmt = select(SkillVersion).where(SkillVersion.skill_id == skill.id).order_by(SkillVersion.version_number)
        res = await session.execute(stmt)
        versions = res.scalars().all()
        
        for v in versions:
            print(f"Version {v.version_number}: storage_path='{v.storage_path}'")

if __name__ == "__main__":
    import sys
    slug = sys.argv[1] if len(sys.argv) > 1 else "brand-guidelines"
    asyncio.run(check_paths(slug))
