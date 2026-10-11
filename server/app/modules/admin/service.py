"""Module K business rules — the Organizer/Admin Control Plane.

RULES (same as Modules E and F):
  1. Functions take the caller's Session, flush only, NEVER commit. The route commits once.
  2. Every organizer mutation writes an admin_action_log row in the SAME transaction,
     so the change and its audit row are saved together or not at all.
  3. Module K never reads another module's tables. It calls their gateways
     (app/contracts/: identity, ledger, inventory, market, trading).
  4. Lock order: operational_control -> market_listing -> team_wallet -> team_widget_inventory.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.contracts.identity import IdentityGateway, TeamSummary
from app.contracts.inventory import InventoryGateway, InventoryItem
from app.contracts.ledger import LedgerGateway, WalletView
from app.contracts.market import MarketGateway
from app.contracts.trading import TradingGateway, TransactionQuery
from app.core.errors import AppError
from app.core.services import gateway
from app.modules.admin import repository as repo
from app.modules.admin.authz import OrganizerPrincipal
from app.modules.admin.errors import (
    ConfirmationRequired,
    LastOwner,
    MissingPermission,
    OperationFrozen,
    OrganizerExists,
    OrganizerNotFound,
    SelfLockout,
    TeamImportInvalid,
    TeamNameTaken,
    TeamNotFound,
    VersionConflict,
)
from app.modules.admin.models import CONTROL_SCOPES, AdminActionLog, Organizer
from app.modules.admin.permissions import ROLE_PERMISSIONS, AuditAction, Permission, Role

FREEZE_ALL_CONFIRMATION = "FREEZE ALL"
MAX_IMPORT_TEAMS = 200

# ---------------------------------------------------------------- audit

_REDACT_PARTS = ("password", "secret", "token", "authorization", "cookie", "api_key", "apikey", "private", "pepper")
_MAX_STR = 500
_MAX_ITEMS = 50
_MAX_DEPTH = 4


def _redact(value: Any, depth: int = 0) -> Any:
    """Make `details` safe to store: no secrets, bounded size, JSON-only types."""
    if depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[:_MAX_ITEMS]:
            key = str(k)[:60]
            out[key] = "[REDACTED]" if any(p in key.lower() for p in _REDACT_PARTS) else _redact(v, depth + 1)
        return out
    if isinstance(value, list | tuple | set):
        return [_redact(v, depth + 1) for v in list(value)[:_MAX_ITEMS]]
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, UUID | datetime):
        return str(value)
    return str(value)[:_MAX_STR]


_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def audit(
    s: Session,
    actor: OrganizerPrincipal,
    action: AuditAction | str,
    *,
    target_type: str,
    target_id: str | UUID,
    reason: str,
    details: dict[str, Any] | None = None,
) -> AdminActionLog:
    """Record one organizer mutation. Call it in the same transaction as the change.

    Other modules may pass their own action name, e.g. "market.round_open" (module.verb, lowercase)."""
    name = action.value if isinstance(action, AuditAction) else action
    if not _ACTION_RE.fullmatch(name) or len(name) > 60:
        raise ValueError(f"audit action must look like 'module.verb', got {name!r}")
    if not reason or not reason.strip():
        raise ValueError("audit reason is required")
    row = AdminActionLog(
        actor_id=str(actor.id),
        actor_email=actor.email,
        actor_role=actor.role.value,
        action=name,
        target_type=target_type[:40],
        target_id=str(target_id)[:100],
        reason=reason.strip()[:_MAX_STR],
        details=_redact(details or {}),
    )
    return repo.insert_audit(s, row)


def list_audit(
    s: Session,
    *,
    cursor: int | None = None,
    limit: int = 50,
    action: str | None = None,
    actor_email: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> tuple[list[AdminActionLog], int | None]:
    limit = max(1, min(limit, 100))
    rows = repo.list_audit(
        s,
        before_id=cursor,
        limit=limit,
        action=action,
        actor_email=actor_email,
        target_type=target_type,
        target_id=target_id,
    )
    return rows, (rows[-1].id if len(rows) == limit else None)


# ---------------------------------------------------------------- organizers


def list_organizers(s: Session) -> list[Organizer]:
    return repo.list_organizers(s)


def create_organizer(
    s: Session, actor: OrganizerPrincipal, *, email: str, display_name: str, role: Role, reason: str
) -> Organizer:
    email = email.strip().lower()
    if repo.email_exists(s, email):
        raise OrganizerExists()
    try:
        with s.begin_nested():  # SAVEPOINT: a unique-email race becomes a clean 409
            org = repo.add_organizer(
                s, Organizer(email=email, display_name=display_name.strip(), role=role.value, created_by=actor.email)
            )
    except IntegrityError as e:
        raise OrganizerExists() from e
    audit(
        s,
        actor,
        AuditAction.ORGANIZER_CREATE,
        target_type="organizer",
        target_id=org.id,
        reason=reason,
        details={"email": email, "role": role.value},
    )
    return org


def update_organizer(
    s: Session,
    actor: OrganizerPrincipal,
    organizer_id: UUID,
    *,
    expected_version: int,
    reason: str,
    role: Role | None = None,
    active: bool | None = None,
    display_name: str | None = None,
) -> Organizer:
    # Lock owners FIRST (always the same order), so two owners can't demote each other at once.
    owners = repo.lock_active_owners(s)

    # Re-check the ACTOR now that we hold the locks: another owner may have demoted them
    # a moment ago (after require_permission ran). Their old permission must not still count.
    me = next((o for o in owners if o.id == actor.id), None) or repo.get_organizer_for_update(s, actor.id)
    if me is None or not me.active or Permission.ORGANIZERS_MANAGE not in ROLE_PERMISSIONS[Role(me.role)]:
        raise MissingPermission(Permission.ORGANIZERS_MANAGE.value)

    target = repo.get_organizer_for_update(s, organizer_id)
    if target is None:
        raise OrganizerNotFound()
    if target.version != expected_version:
        raise VersionConflict(target.version)

    changes_role = role is not None and role.value != target.role
    changes_active = active is not None and active != target.active
    if target.id == actor.id and (changes_role or active is False):
        raise SelfLockout()

    stops_being_owner = (
        target.active and target.role == Role.OWNER.value and ((changes_role) or (changes_active and active is False))
    )
    if stops_being_owner and len(owners) <= 1:
        raise LastOwner()

    before = {"role": target.role, "active": target.active, "display_name": target.display_name}
    if changes_role:
        assert role is not None
        target.role = role.value
    if changes_active:
        assert active is not None
        target.active = active
    if display_name is not None and display_name.strip() != target.display_name:
        target.display_name = display_name.strip()
    after = {"role": target.role, "active": target.active, "display_name": target.display_name}
    if before == after:
        return target  # nothing changed: no version bump, no audit row

    repo.save_organizer(s, target)
    audit(
        s,
        actor,
        AuditAction.ORGANIZER_UPDATE,
        target_type="organizer",
        target_id=target.id,
        reason=reason,
        details={"before": before, "after": after},
    )
    return target


def bootstrap_owner(s: Session, *, email: str, display_name: str) -> Organizer:
    """Used only by the CLI to create the first OWNER (nobody can log in to /admin before that)."""
    email = email.strip().lower()
    if repo.email_exists(s, email):
        raise OrganizerExists()
    org = repo.add_organizer(
        s, Organizer(email=email, display_name=display_name.strip(), role=Role.OWNER.value, created_by="cli")
    )
    repo.insert_audit(
        s,
        AdminActionLog(
            actor_id="cli",
            actor_email="cli",
            actor_role="SYSTEM",
            action=AuditAction.ORGANIZER_CREATE.value,
            target_type="organizer",
            target_id=str(org.id),
            reason="Bootstrap first owner from server CLI",
            details={"email": email, "role": Role.OWNER.value},
        ),
    )
    return org


# ---------------------------------------------------------------- emergency controls


@dataclass(frozen=True)
class ControlView:
    scope: str
    frozen: bool
    reason: str | None
    changed_by: str | None
    changed_at: datetime | None
    version: int


def get_controls(s: Session) -> list[ControlView]:
    return [
        ControlView(c.scope, c.frozen, c.reason, c.changed_by, c.changed_at, c.version) for c in repo.list_controls(s)
    ]


def set_frozen(
    s: Session,
    actor: OrganizerPrincipal,
    scope: str,
    *,
    frozen: bool,
    reason: str,
    confirm: str | None = None,
    expected_version: int | None = None,
) -> ControlView:
    if scope not in CONTROL_SCOPES:
        raise AppError("INVALID_SCOPE", "Unknown control scope", 422, {"allowed": list(CONTROL_SCOPES)})
    if frozen and scope == "ALL" and confirm != FREEZE_ALL_CONFIRMATION:
        raise ConfirmationRequired(FREEZE_ALL_CONFIRMATION)

    # FOR UPDATE waits for every in-flight purchase/bid holding FOR SHARE to finish.
    # After we commit, every new purchase/bid sees frozen=true. Clean boundary, nothing half-done.
    ctl = repo.get_control_for_update(s, scope)
    if ctl is None:  # seeded by migration 0004; missing means the DB was tampered with
        raise AppError("CONTROL_MISSING", "Control row missing; re-run migrations", 500)
    if expected_version is not None and ctl.version != expected_version:
        raise VersionConflict(ctl.version)

    if ctl.frozen != frozen:
        ctl.frozen = frozen
        ctl.reason = reason.strip()
        ctl.changed_by = actor.email
        repo.save_control(s, ctl)
        audit(
            s,
            actor,
            AuditAction.CONTROL_FREEZE if frozen else AuditAction.CONTROL_UNFREEZE,
            target_type="control",
            target_id=scope,
            reason=reason,
            details={"scope": scope, "frozen": frozen},
        )
    return ControlView(ctl.scope, ctl.frozen, ctl.reason, ctl.changed_by, ctl.changed_at, ctl.version)


def ensure_not_frozen(s: Session, scope: str) -> None:
    """Call FIRST inside your transaction, before locking listings/wallets (Module I: "TRADING",
    Module J: "BIDDING"). Raises 423 OPERATION_FROZEN if `scope` or ALL is frozen.

    It takes a FOR SHARE lock on the control rows: many purchases run in parallel, but a
    freeze waits until they finish, so a freeze never cuts a purchase in half."""
    if scope not in ("TRADING", "BIDDING"):
        raise ValueError(f"ensure_not_frozen scope must be TRADING or BIDDING, got {scope!r}")
    frozen = repo.frozen_controls_for_share(s, ["ALL", scope])
    if frozen:
        c = next((x for x in frozen if x.scope == "ALL"), frozen[0])
        raise OperationFrozen(c.scope, c.reason)


def public_controls(s: Session) -> dict[str, Any]:
    """What a participant's app may know: is trading/bidding open, and the organizers' message."""
    by_scope = {c.scope: c for c in repo.list_controls(s)}
    all_c, tr, bd = by_scope.get("ALL"), by_scope.get("TRADING"), by_scope.get("BIDDING")
    all_frozen = bool(all_c and all_c.frozen)
    message = next((c.reason for c in (all_c, tr, bd) if c and c.frozen), None)
    return {
        "trading_open": not (all_frozen or (tr is not None and tr.frozen)),
        "bidding_open": not (all_frozen or (bd is not None and bd.frozen)),
        "message": message,
    }


# ---------------------------------------------------------------- teams


@dataclass(frozen=True)
class TeamOverview:
    id: UUID
    name: str
    status: str
    balance: int
    held: int
    available: int
    units_owned: int


@dataclass(frozen=True)
class TeamDetail:
    team: TeamSummary
    wallet: WalletView
    inventory: list[InventoryItem]


def _directory(s: Session) -> IdentityGateway:
    return gateway(IdentityGateway, s)


def _overview(teams: list[TeamSummary], s: Session) -> list[TeamOverview]:
    ids = [t.id for t in teams]
    wallets = gateway(LedgerGateway, s).get_wallets(ids)  # 1 query
    units = gateway(InventoryGateway, s).unit_counts(ids)  # 1 query
    return [
        TeamOverview(
            id=t.id,
            name=t.name,
            status=t.status,
            balance=wallets[t.id].balance,
            held=wallets[t.id].held,
            available=wallets[t.id].available,
            units_owned=units[t.id],
        )
        for t in teams
    ]


def list_team_overview(s: Session) -> list[TeamOverview]:
    """Dashboard: every team with credits and units, in 3 queries total however many teams exist."""
    return _overview(_directory(s).list_teams(), s)


def get_team_detail(s: Session, team_id: UUID) -> TeamDetail:
    team = _directory(s).get_team(team_id)
    if team is None:
        raise TeamNotFound()
    return TeamDetail(
        team=team,
        wallet=gateway(LedgerGateway, s).get_wallet(team_id),
        inventory=gateway(InventoryGateway, s).get_team_inventory(team_id, include_zero=False),
    )


def _create_one(
    s: Session,
    actor: OrganizerPrincipal,
    d: IdentityGateway,
    *,
    name: str,
    member_emails: list[str],
    initial_credits: int,
    reason: str,
) -> TeamSummary:
    try:
        with s.begin_nested():  # SAVEPOINT: a duplicate-name race becomes a clean 409
            team = d.create_team(name, member_emails)
    except IntegrityError as e:
        raise TeamNameTaken([name]) from e
    if initial_credits > 0:
        gateway(LedgerGateway, s).grant_initial(team.id, initial_credits, actor=actor.actor)
    audit(
        s,
        actor,
        AuditAction.TEAM_CREATE,
        target_type="team",
        target_id=team.id,
        reason=reason,
        # member emails are personal data: store how many, not who
        details={"name": name, "member_count": len(member_emails), "initial_credits": initial_credits},
    )
    return team


def create_team(
    s: Session,
    actor: OrganizerPrincipal,
    *,
    name: str,
    member_emails: list[str],
    initial_credits: int,
    reason: str,
) -> TeamOverview:
    d = _directory(s)
    if d.find_by_name(name) is not None:
        raise TeamNameTaken([name])
    team = _create_one(
        s, actor, d, name=name, member_emails=member_emails, initial_credits=initial_credits, reason=reason
    )
    return _overview([team], s)[0]


def import_teams(
    s: Session,
    actor: OrganizerPrincipal,
    *,
    teams: list[tuple[str, list[str]]],
    initial_credits: int,
    reason: str,
) -> list[TeamOverview]:
    """All-or-nothing: every row is checked before anything is created. Any failure -> nothing saved."""
    d = _directory(s)
    problems: list[str] = []
    if not teams:
        problems.append("No teams given")
    if len(teams) > MAX_IMPORT_TEAMS:
        problems.append(f"At most {MAX_IMPORT_TEAMS} teams per import")
    seen_names: dict[str, int] = {}
    seen_emails: dict[str, int] = {}
    for i, (name, emails) in enumerate(teams, start=1):
        key = name.casefold()
        if key in seen_names:
            problems.append(f"Row {i}: team name '{name}' repeats row {seen_names[key]}")
        seen_names.setdefault(key, i)
        for e in emails:
            if e in seen_emails and seen_emails[e] != i:
                problems.append(f"Row {i}: an email is already in row {seen_emails[e]}")
            seen_emails.setdefault(e, i)
    if problems:
        raise TeamImportInvalid(problems)

    taken = [name for name, _ in teams if d.find_by_name(name) is not None]
    if taken:
        raise TeamNameTaken(taken)

    created = [
        _create_one(s, actor, d, name=name, member_emails=emails, initial_credits=initial_credits, reason=reason)
        for name, emails in teams
    ]
    return _overview(created, s)


def set_team_status(
    s: Session, actor: OrganizerPrincipal, team_id: UUID, *, status: str, reason: str, confirm: str | None
) -> TeamSummary:
    d = _directory(s)
    team = d.get_team(team_id)
    if team is None:
        raise TeamNotFound()
    if status == "DISABLED" and confirm != team.name:
        raise ConfirmationRequired(team.name)  # type the team's name to disable it
    if team.status == status:
        return team
    updated = d.set_status(team_id, status)
    audit(
        s,
        actor,
        AuditAction.TEAM_STATUS,
        target_type="team",
        target_id=team_id,
        reason=reason,
        details={"from": team.status, "to": status},
    )
    return updated


# ---------------------------------------------------------------- read-only views owned by other modules


def market_status(s: Session) -> dict[str, Any]:
    return gateway(MarketGateway, s).status_summary()


def transactions(s: Session, *, team_id: UUID | None, limit: int, cursor: str | None) -> dict[str, Any]:
    return gateway(TradingGateway, s).transaction_feed(
        TransactionQuery(team_id=team_id, limit=max(1, min(limit, 100)), cursor=cursor)
    )
