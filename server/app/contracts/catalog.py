"""Module D (widget catalog) contract, for Modules F, G, I and J."""

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol


@dataclass(frozen=True)
class WidgetInfo:
    id: str
    appdev_key: str
    display_name: str
    archived: bool
    description: str | None = None


class CatalogGateway(Protocol):
    OWNER: ClassVar[str] = "Module D catalog"

    def get_widget(self, widget_id: str) -> WidgetInfo:
        """Any status (resolving history). 404 WIDGET_NOT_FOUND."""
        ...

    def require_active_widget(self, widget_id: str) -> WidgetInfo:
        """Before listing or selling. 404 WIDGET_NOT_FOUND, 409 WIDGET_ARCHIVED."""
        ...

    def get_widgets(self, widget_ids: list[str]) -> dict[str, WidgetInfo]:
        """Batch resolve in one query, archived included; missing ids are absent."""
        ...

    def widget_exists(self, widget_id: str) -> bool: ...

    def validate_widget(self, *, widget_id: str, operation: Literal["buy", "sell", "award"]) -> None:
        """Trading/auction check: buying and awarding need an active widget, resale
        accepts an archived one. Raises the marketplace errors (INVALID_LISTING)."""
        ...
