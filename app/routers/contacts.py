"""Contacts router — CRUD for the internal contact directory."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Contact, Employee
from app.database.repository import Repository
from app.services.auth_service import require_permission

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


@router.get("/contacts", response_model=list[ContactResponse])
async def list_contacts(db: AsyncSession = Depends(get_db)):
    repo = Repository(db)
    contacts = await repo.get_all(Contact, order_by=Contact.name)
    return contacts


@router.post("/contacts", response_model=ContactResponse)
async def create_contact(
    req: ContactCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("contacts.manage"),
):
    repo = Repository(db)
    contact = Contact(**req.model_dump())
    return await repo.create(contact)


@router.put("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: uuid.UUID,
    req: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("contacts.manage"),
):
    repo = Repository(db)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "No fields to update")
    updated = await repo.update_fields(Contact, contact_id, **fields)
    if not updated:
        raise HTTPException(404, "Contact not found")
    return updated


@router.delete("/contacts/{contact_id}")
async def delete_contact(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("contacts.manage"),
):
    repo = Repository(db)
    deleted = await repo.delete_by_id(Contact, contact_id)
    if not deleted:
        raise HTTPException(404, "Contact not found")
    return {"deleted": True}
