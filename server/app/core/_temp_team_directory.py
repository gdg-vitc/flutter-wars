"""TEMP implementation of Module K's TeamDirectory port, over the temp `team` table.

When Module B merges, Team 6 registers THEIR implementation instead
(ports.set_team_directory(...)) and this file is deleted. Module K never
touches the team table directly — it only calls the port.
"""

from uuid import UUID, uuid4

from sqlmodel import Session, col, select

from app.core._temp_models import Team
from app.modules.admin.ports import TeamSummary


class TempTeamDirectory:
    def list_teams(self, s: Session) -> list[TeamSummary]:
        rows = s.exec(select(Team).order_by(col(Team.name))).all()
        return [TeamSummary(id=t.id, name=t.name, status=t.status) for t in rows]

    def get_team(self, s: Session, team_id: UUID) -> TeamSummary | None:
        t = s.get(Team, team_id)
        return TeamSummary(id=t.id, name=t.name, status=t.status) if t else None

    def find_by_name(self, s: Session, name: str) -> TeamSummary | None:
        t = s.exec(select(Team).where(Team.name == name)).first()
        return TeamSummary(id=t.id, name=t.name, status=t.status) if t else None

    def create_team(self, s: Session, name: str, member_emails: list[str]) -> TeamSummary:
        # member_emails: Module B maps Google identities to the team. The temp version ignores them.
        t = Team(id=uuid4(), name=name, status="ACTIVE")
        s.add(t)
        s.flush()
        return TeamSummary(id=t.id, name=t.name, status=t.status)

    def set_status(self, s: Session, team_id: UUID, status: str) -> TeamSummary:
        t = s.exec(select(Team).where(Team.id == team_id).with_for_update()).one()
        t.status = status
        s.add(t)
        s.flush()
        return TeamSummary(id=t.id, name=t.name, status=t.status)
