"""Dependency boundaries consumed by Module C, implemented by owning modules later."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WidgetAllowance:
    """The minimal inventory/catalog projection safe to expose to AppDev."""

    widget_id: str
    quantity: int


class InventoryReader(Protocol):
    """Module F's eventual read contract; Module C never owns inventory tables."""

    def get_team_inventory(self, team_id: str) -> list[WidgetAllowance]: ...


class UnconfiguredInventoryReader:
    def get_team_inventory(self, team_id: str) -> list[WidgetAllowance]:
        from app.core.errors import AppError

        raise AppError(
            "DEPENDENCY_UNAVAILABLE", "Required dependencies are unavailable.", 503
        )
