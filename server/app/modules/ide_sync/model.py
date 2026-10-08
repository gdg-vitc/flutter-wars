"""Persistent entities owned exclusively by Module C."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field, SQLModel


class TeamApiKey(SQLModel, table=True):
    """A non-recoverable credential tied to exactly one external team ID."""

    __tablename__ = "team_api_key"

    key_id: str = Field(primary_key=True, max_length=32)
    team_id: str = Field(index=True, max_length=128)
    secret_hash: str = Field(max_length=128)
    status: str = Field(
        default="active", sa_column=Column(String(16), nullable=False, index=True)
    )
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_used_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    revoked_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
