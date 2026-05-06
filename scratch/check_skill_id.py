
import asyncio
import uuid
from sqlalchemy import select
from app.database import async_session_factory
from app.database.models import Skill

async def check_id(id_str):
    async with async_session_factory() as session:
        uid = uuid.UUID(id_str)
        skill = await session.get(Skill, uid)
        if skill:
            print(f"Found Skill: {skill.name} (slug: {skill.slug})")
        else:
            print(f"Skill with ID {id_str} NOT FOUND in DB")

if __name__ == "__main__":
    import sys
    id_str = sys.argv[1]
    asyncio.run(check_id(id_str))
