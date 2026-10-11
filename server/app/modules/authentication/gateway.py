"""Module B's gateway (app/contracts/identity.py): token checks and the team directory.

Module K manages teams through this gateway and never touches the `team` or
`team_membership` tables itself. Team status is stored lower-case by Module B
("active"/"disabled") and exposed upper-case ("ACTIVE"/"DISABLED").
"""

from uuid import UUID

from sqlmodel import Session, col, select

from app.contracts.admin import AdminGateway
from app.contracts.identity import TeamSummary
from app.contracts.principal import Principal
from app.core.config import Settings
from app.core.errors import AppError
from app.core.services import gateway
from app.modules.authentication.jwt import decode_access_token
from app.modules.authentication.model import Team, TeamMembership, UserIdentity
from app.modules.authentication.service import AuthenticationService


def _summary(team: Team) -> TeamSummary:
    return TeamSummary(id=team.id, name=team.name, status=team.status.upper())


class IdentityGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    def principal_for_token(self, token: str, settings: Settings) -> Principal:
        return AuthenticationService(self.session, settings).principal_for_token(token)

    def authorized_stream_tokens(self, tokens: list[str], settings: Settings) -> set[str]:
        claims = {}
        for token in set(tokens):
            try:
                data = decode_access_token(token, settings)
                user_id, team_id = data.get("sub"), data.get("team_id")
                if not isinstance(user_id, str) or not isinstance(team_id, (str, type(None))):
                    continue
                if "sid" in data:
                    from app.modules.authentication.sessions import verify_session

                    verify_session(self.session, data["sid"], int(user_id))
                claims[token] = (int(user_id), UUID(team_id) if team_id is not None else None)
            except (AppError, ValueError):
                continue
        if not claims:
            return set()
        rows = self.session.exec(
            select(UserIdentity.id, UserIdentity.email, TeamMembership.team_id, Team.status)
            .outerjoin(TeamMembership, TeamMembership.user_identity_id == UserIdentity.id)
            .outerjoin(Team, Team.id == TeamMembership.team_id)
            .where(col(UserIdentity.id).in_({user_id for user_id, _ in claims.values()}))
        ).all()
        members = {(user_id, team_id) for user_id, _, team_id, status in rows if status == "active"}
        emails = {user_id: email for user_id, email, _, _ in rows}
        organizers = {}
        allowed = set()
        for token, (user_id, team_id) in claims.items():
            if team_id is not None:
                if (user_id, team_id) in members:
                    allowed.add(token)
            elif user_id in emails:
                email = emails[user_id]
                if email not in organizers:
                    organizers[email] = gateway(AdminGateway, self.session).is_organizer(email)
                if organizers[email]:
                    allowed.add(token)
        return allowed

    def team_exists(self, team_id: UUID) -> bool:
        return self.session.exec(select(Team.id).where(Team.id == team_id)).first() is not None

    def list_teams(self) -> list[TeamSummary]:
        return [_summary(t) for t in self.session.exec(select(Team).order_by(col(Team.name))).all()]

    def get_team(self, team_id: UUID) -> TeamSummary | None:
        team = self.session.get(Team, team_id)
        return _summary(team) if team else None

    def find_by_name(self, name: str) -> TeamSummary | None:
        team = self.session.exec(select(Team).where(Team.name == name)).first()
        return _summary(team) if team else None

    def create_team(self, name: str, member_emails: list[str]) -> TeamSummary:
        """Create the team and pre-register its members by email.

        Each member binds their Google account on first login (Module B matches the
        verified email). An email already registered to another team is rejected.
        """
        s = self.session
        team = Team(name=name)
        s.add(team)
        s.flush()
        for email in dict.fromkeys(e.strip().lower() for e in member_emails if e.strip()):
            user = s.exec(select(UserIdentity).where(UserIdentity.email == email)).first()
            if user is None:
                user = UserIdentity(email=email)
                s.add(user)
                s.flush()
            elif s.exec(select(TeamMembership).where(TeamMembership.user_identity_id == user.id)).first():
                raise AppError(
                    "MEMBER_ALREADY_IN_TEAM",
                    "A member already belongs to another team.",
                    409,
                    email=email,
                )
            s.add(TeamMembership(user_identity_id=user.id, team_id=team.id))
        s.flush()
        return _summary(team)

    def set_status(self, team_id: UUID, status: str) -> TeamSummary:
        s = self.session
        team = s.exec(select(Team).where(Team.id == team_id).with_for_update()).one()
        team.status = status.lower()
        s.add(team)
        s.flush()
        return _summary(team)
