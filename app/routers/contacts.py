"""
Contacts router — CRUD for contact directory.
Syncs contact data to Neo4j Knowledge Graph when available.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Contact, Employee
from app.database.repository import Repository
from app.services.auth_service import require_admin

router = APIRouter()


class ContactCreate(BaseModel):
    name: str
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    topics: Optional[list[str]] = None
    note: Optional[str] = None


class ContactResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    topics: Optional[list[str]]
    note: Optional[str]

    model_config = {"from_attributes": True}


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    topics: Optional[list[str]] = None
    note: Optional[str] = None


async def _sync_contact_to_neo4j(contact_id: str, name: str, role: Optional[str] = None) -> None:
    """Best-effort sync contact node to Neo4j."""
    try:
        from app.services.neo4j_service import neo4j_service
        if neo4j_service.available:
            await neo4j_service.ensure_contact(contact_id, name, role)
    except Exception as e:
        logger.debug(f"Neo4j contact sync skipped: {e}")


async def _delete_contact_from_neo4j(contact_id: str) -> None:
    """Best-effort delete contact node from Neo4j."""
    try:
        from app.services.neo4j_service import neo4j_service
        if neo4j_service.available:
            await neo4j_service.delete_contact(contact_id)
    except Exception as e:
        logger.debug(f"Neo4j contact delete skipped: {e}")


@router.get("/contacts", response_model=list[ContactResponse])
async def list_contacts(db: AsyncSession = Depends(get_db)):
    repo = Repository(db)
    contacts = await repo.get_all(Contact, order_by=Contact.name)
    return contacts


@router.post("/contacts", response_model=ContactResponse)
async def create_contact(req: ContactCreate, db: AsyncSession = Depends(get_db), _admin: Employee = Depends(require_admin)):
    repo = Repository(db)
    contact = Contact(**req.model_dump())
    contact = await repo.create(contact)
    await _sync_contact_to_neo4j(str(contact.id), contact.name, contact.role)
    return contact


@router.put("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: uuid.UUID,
    req: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: Employee = Depends(require_admin),
):
    repo = Repository(db)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")
    updated = await repo.update_fields(Contact, contact_id, **fields)
    if not updated:
        raise HTTPException(404, "Contact not found")
    await _sync_contact_to_neo4j(str(contact_id), updated.name, updated.role)
    return updated


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: uuid.UUID, db: AsyncSession = Depends(get_db), _admin: Employee = Depends(require_admin)):
    repo = Repository(db)
    deleted = await repo.delete_by_id(Contact, contact_id)
    if not deleted:
        raise HTTPException(404, "Contact not found")
    await _delete_contact_from_neo4j(str(contact_id))
    return {"deleted": True}
