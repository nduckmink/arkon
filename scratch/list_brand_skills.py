
import asyncio
from sqlalchemy import select
from app.database import async_session_factory
from app.database.models import Skill

async def check():
    async with async_session_factory() as session:
        stmt = select(Skill).where(Skill.slug == 'brand-guidelines')
        res = await session.execute(stmt)
        skills = res.scalars().all()
        for sk in skills:
            print(f"ID: {sk.id}, Name: {sk.name}, Status: {sk.status}, Storage: {sk.storage_path}")

if __name__ == "__main__":
    asyncio.run(check())
