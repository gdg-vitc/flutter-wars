"""Module K HTTP routes. Thin: permission check + validation + one service call + commit."""

from dataclasses import asdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.auth import Principal, require_participant
from app.core.db import get_db
from app.modules.admin import service
from app.modules.admin.authz import OrganizerPrincipal, require_organizer, require_permission
from app.modules.admin.permissions import Permission
from app.modules.admin.schemas import (
    AuditOut,
    AuditPage,
    ControlOut,
    ControlSetIn,
    MeOut,
    OrganizerCreateIn,
    OrganizerOut,
    OrganizerUpdateIn,
    PublicControlsOut,
    Scope,
    TeamCreateIn,
    TeamDetailOut,
    TeamImportIn,
    TeamInventoryItemOut,
    TeamOverviewOut,
    TeamStatusIn,
    TeamSummaryOut,
    TeamWalletOut,
)

router = APIRouter(prefix="/admin", tags=["admin: control plane"])
public_router = APIRouter(tags=["event status"])


# ---------------------------------------------------------------- participant-visible


@public_router.get("/controls", response_model=PublicControlsOut)
def read_public_controls(_: Principal = Depends(require_participant), s: Session = Depends(get_db)) -> Any:
    return service.public_controls(s)


# ---------------------------------------------------------------- me / organizers


@router.get("/me", response_model=MeOut)
def me(org: OrganizerPrincipal = Depends(require_organizer)) -> MeOut:
    return MeOut(
        id=org.id,
        email=org.email,
        display_name=org.display_name,
        role=org.role,
        permissions=sorted(p.value for p in org.permissions),
    )


@router.get("/organizers", response_model=list[OrganizerOut])
def list_organizers(
    _: OrganizerPrincipal = Depends(require_permission(Permission.ORGANIZERS_MANAGE)), s: Session = Depends(get_db)
) -> list[OrganizerOut]:
    return [OrganizerOut.model_validate(o) for o in service.list_organizers(s)]


@router.post("/organizers", response_model=OrganizerOut, status_code=201)
def create_organizer(
    body: OrganizerCreateIn,
    org: OrganizerPrincipal = Depends(require_permission(Permission.ORGANIZERS_MANAGE)),
    s: Session = Depends(get_db),
) -> OrganizerOut:
    created = service.create_organizer(
        s, org, email=body.email, display_name=body.display_name, role=body.role, reason=body.reason
    )
    out = OrganizerOut.model_validate(created)
    s.commit()
    return out


@router.patch("/organizers/{organizer_id}", response_model=OrganizerOut)
def update_organizer(
    organizer_id: UUID,
    body: OrganizerUpdateIn,
    org: OrganizerPrincipal = Depends(require_permission(Permission.ORGANIZERS_MANAGE)),
    s: Session = Depends(get_db),
) -> OrganizerOut:
    updated = service.update_organizer(
        s,
        org,
        organizer_id,
        expected_version=body.expected_version,
        reason=body.reason,
        role=body.role,
        active=body.active,
        display_name=body.display_name,
    )
    s.flush()
    s.refresh(updated)
    out = OrganizerOut.model_validate(updated)
    s.commit()
    return out


# ---------------------------------------------------------------- audit


@router.get("/audit", response_model=AuditPage)
def read_audit(
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    action: str | None = Query(default=None, max_length=60),
    actor_email: str | None = Query(default=None, max_length=254),
    target_type: str | None = Query(default=None, max_length=40),
    target_id: str | None = Query(default=None, max_length=100),
    _: OrganizerPrincipal = Depends(require_permission(Permission.AUDIT_READ)),
    s: Session = Depends(get_db),
) -> AuditPage:
    rows, nxt = service.list_audit(
        s,
        cursor=cursor,
        limit=limit,
        action=action,
        actor_email=actor_email,
        target_type=target_type,
        target_id=target_id,
    )
    return AuditPage(items=[AuditOut.model_validate(r) for r in rows], next_cursor=nxt)


# ---------------------------------------------------------------- emergency controls


@router.get("/controls", response_model=list[ControlOut])
def read_controls(
    _: OrganizerPrincipal = Depends(require_permission(Permission.VIEW)), s: Session = Depends(get_db)
) -> list[ControlOut]:
    return [ControlOut(**asdict(c)) for c in service.get_controls(s)]


@router.put("/controls/{scope}", response_model=ControlOut)
def set_control(
    scope: Scope,
    body: ControlSetIn,
    org: OrganizerPrincipal = Depends(require_permission(Permission.CONTROLS_FREEZE)),
    s: Session = Depends(get_db),
) -> ControlOut:
    view = service.set_frozen(
        s,
        org,
        scope,
        frozen=body.frozen,
        reason=body.reason,
        confirm=body.confirm,
        expected_version=body.expected_version,
    )
    s.commit()
    return ControlOut(**asdict(view))


# ---------------------------------------------------------------- teams


@router.get("/teams", response_model=list[TeamOverviewOut])
def list_teams(
    _: OrganizerPrincipal = Depends(require_permission(Permission.VIEW)), s: Session = Depends(get_db)
) -> list[TeamOverviewOut]:
    return [TeamOverviewOut(**asdict(t)) for t in service.list_team_overview(s)]


@router.post("/teams", response_model=TeamOverviewOut, status_code=201)
def create_team(
    body: TeamCreateIn,
    org: OrganizerPrincipal = Depends(require_permission(Permission.TEAMS_MANAGE)),
    s: Session = Depends(get_db),
) -> TeamOverviewOut:
    t = service.create_team(
        s,
        org,
        name=body.name,
        member_emails=body.member_emails,
        initial_credits=body.initial_credits,
        reason=body.reason,
    )
    s.commit()
    return TeamOverviewOut(**asdict(t))


@router.post("/teams/import", response_model=list[TeamOverviewOut], status_code=201)
def import_teams(
    body: TeamImportIn,
    org: OrganizerPrincipal = Depends(require_permission(Permission.TEAMS_MANAGE)),
    s: Session = Depends(get_db),
) -> list[TeamOverviewOut]:
    created = service.import_teams(
        s,
        org,
        teams=[(row.name, row.member_emails) for row in body.teams],
        initial_credits=body.initial_credits,
        reason=body.reason,
    )
    s.commit()
    return [TeamOverviewOut(**asdict(t)) for t in created]


@router.get("/teams/{team_id}", response_model=TeamDetailOut)
def read_team(
    team_id: UUID,
    _: OrganizerPrincipal = Depends(require_permission(Permission.VIEW)),
    s: Session = Depends(get_db),
) -> TeamDetailOut:
    d = service.get_team_detail(s, team_id)
    return TeamDetailOut(
        team=TeamSummaryOut(**asdict(d.team)),
        wallet=TeamWalletOut(balance=d.wallet.balance, held=d.wallet.held, available=d.wallet.available),
        inventory=[TeamInventoryItemOut(**asdict(i)) for i in d.inventory],
    )


@router.post("/teams/{team_id}/status", response_model=TeamSummaryOut)
def set_team_status(
    team_id: UUID,
    body: TeamStatusIn,
    org: OrganizerPrincipal = Depends(require_permission(Permission.TEAMS_MANAGE)),
    s: Session = Depends(get_db),
) -> TeamSummaryOut:
    t = service.set_team_status(s, org, team_id, status=body.status, reason=body.reason, confirm=body.confirm)
    s.commit()
    return TeamSummaryOut(**asdict(t))


# ---------------------------------------------------------------- market & transactions (read-only, via their gateways)


@router.get("/market/status")
def read_market_status(
    _: OrganizerPrincipal = Depends(require_permission(Permission.VIEW)), s: Session = Depends(get_db)
) -> dict[str, Any]:
    return service.market_status(s)


@router.get("/transactions")
def read_transactions(
    team_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=200),
    _: OrganizerPrincipal = Depends(require_permission(Permission.VIEW)),
    s: Session = Depends(get_db),
) -> dict[str, Any]:
    return service.transactions(s, team_id=team_id, limit=limit, cursor=cursor)
