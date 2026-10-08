"""TEMP: minimal copy of the `team` table owned by Module B (Team 6), so our foreign keys resolve.

Delete this file and import Module B's team model when it is merged.
(The `widget` table is now real: Module D, app/modules/catalog/models.py.)
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, SQLModel


class Team(SQLModel, table=True):
    __tablename__ = "team"

    id: UUID = Field(sa_column=Column(PG_UUID(as_uuid=True), primary_key=True))
    name: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    status: str = Field(default="ACTIVE", sa_column=Column(String(16), nullable=False, server_default="ACTIVE"))
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
