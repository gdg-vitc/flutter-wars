"""PLACEHOLDER for Module B (Authentication) and Module K (organizer role).

get_principal always rejects until Module B replaces it; tests override it
with app.dependency_overrides.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from fastapi import Depends

from app.core.errors import AppError


class Role(StrEnum):
    PARTICIPANT = "participant"
    ORGANIZER = "organizer"


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    team_id: UUID | None = None

    @property
    def organizer(self) -> bool:
        return self.role == Role.ORGANIZER


def get_principal() -> Principal:
    raise AppError("UNAUTHENTICATED", "Authentication is not configured.", status_code=401)


def require_organizer(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != Role.ORGANIZER:
        raise AppError("FORBIDDEN", "Organizer access required.", status_code=403)
    return principal
