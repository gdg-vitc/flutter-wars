from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends

from app.core import auth as core_auth


@dataclass(frozen=True)
class Principal:
    """Foundation/Auth supplies this AFTER JWT and current eligibility verification."""

    team_id: UUID | None
    organizer: bool = False


def principal_from_core(
    principal: core_auth.Principal = Depends(core_auth.get_principal),
) -> Principal:
    """I/J view of the one shared principal, so Auth (B) replaces a single dependency."""
    return Principal(team_id=principal.team_id, organizer=principal.organizer)
