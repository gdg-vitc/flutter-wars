"""Module B identity/team lookup and current-principal validation."""

from sqlmodel import Session, select

from app.contracts.principal import Principal
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.authentication.jwt import decode_access_token
from app.modules.authentication.model import Team, TeamMembership, UserIdentity


class AuthenticationService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def principal_for_google_identity(self, google_subject: str, email: str) -> Principal | None:
        user = self.session.exec(
            select(UserIdentity).where(UserIdentity.google_subject == google_subject)
        ).first()
        if user is None:
            user = self.session.exec(
                select(UserIdentity).where(UserIdentity.email == email.lower())
            ).first()
            if user is None or user.google_subject is not None:
                return None
            user.google_subject = google_subject
            self.session.add(user)
        if user is None or user.id is None:
            return None
        membership = self.session.exec(
            select(TeamMembership).where(TeamMembership.user_identity_id == user.id)
        ).first()
        if membership is None:
            return None
        team = self.session.get(Team, membership.team_id)
        if team is None or team.status != "active" or team.id is None:
            return None
        return Principal(
            user_id=str(user.id), team_id=str(team.id), role=membership.role, email=user.email
        )

    def principal_for_token(self, token: str) -> Principal:
        claims = decode_access_token(token, self.settings)
        user_id = claims.get("sub")
        team_id = claims.get("team_id")
        if not isinstance(user_id, str) or not isinstance(team_id, str):
            raise AppError("INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401)
        try:
            numeric_user_id = int(user_id)
            numeric_team_id = int(team_id)
        except ValueError as exc:
            raise AppError(
                "INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401
            ) from exc
        user = self.session.get(UserIdentity, numeric_user_id)
        if user is None:
            raise AppError(
                "IDENTITY_NOT_FOUND", "Authenticated identity is no longer available.", 401
            )
        membership = self.session.exec(
            select(TeamMembership).where(
                TeamMembership.user_identity_id == numeric_user_id,
                TeamMembership.team_id == numeric_team_id,
            )
        ).first()
        if membership is None:
            raise AppError(
                "TEAM_ACCESS_DENIED", "Authenticated identity cannot access this team.", 403
            )
        team = self.session.get(Team, numeric_team_id)
        if team is None or team.status != "active":
            raise AppError(
                "TEAM_ACCESS_DENIED", "Authenticated identity cannot access this team.", 403
            )
        return Principal(
            user_id=str(user.id), team_id=str(team.id), role=membership.role, email=user.email
        )
