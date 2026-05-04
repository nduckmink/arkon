"""
Notes router — CRUD.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Note, Employee
from app.database.repository import Repository
from app.services.auth_service import get_current_user, require_permission

router = APIRouter()


class NoteCreate(BaseModel):
    title: Optional[str] = None
    content: str
    note_type: Optional[str] = "human"


class NoteResponse(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    content: Optional[str]
    note_type: Optional[str]
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


@router.get("/notes", response_model=list[NoteResponse])
async def list_notes(db: AsyncSession = Depends(get_db), _user: Employee = Depends(get_current_user)):
    repo = Repository(db)
    notes = await repo.get_all(Note, order_by=Note.created_at.desc())
    return [
        NoteResponse(
            id=n.id, title=n.title, content=n.content,
            note_type=n.note_type,
            created_at=n.created_at.isoformat(),
            updated_at=n.updated_at.isoformat(),
        )
        for n in notes
    ]


@router.post("/notes", response_model=NoteResponse)
async def create_note(req: NoteCreate, db: AsyncSession = Depends(get_db), _user: Employee = require_permission("kb.create")):
    repo = Repository(db)
    note = Note(**req.model_dump())
    note = await repo.create(note)
    return NoteResponse(
        id=note.id, title=note.title, content=note.content,
        note_type=note.note_type,
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat(),
    )


@router.delete("/notes/{note_id}")
async def delete_note(note_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user: Employee = require_permission("kb.delete")):
    repo = Repository(db)
    deleted = await repo.delete_by_id(Note, note_id)
    if not deleted:
        raise HTTPException(404, "Note not found")
    return {"deleted": True}
