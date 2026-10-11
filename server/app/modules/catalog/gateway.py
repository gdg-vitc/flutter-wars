"""Module D's gateway (app/contracts/catalog.py). Thin: the rules live in service.py."""

from typing import Literal

from sqlmodel import Session

from app.contracts.auction import AuctionGateway
from app.contracts.catalog import WidgetInfo
from app.contracts.marketplace_errors import InvalidListing, ResaleNotAllowed, port_errors
from app.core.services import optional_gateway
from app.modules.catalog import service
from app.modules.catalog.models import Widget

# Trading/auction see an unknown or archived widget as an unavailable listing.
_PORT_ERRORS = {"WIDGET_NOT_FOUND": InvalidListing, "WIDGET_ARCHIVED": InvalidListing}


def _info(w: Widget) -> WidgetInfo:
    return WidgetInfo(
        id=w.id, appdev_key=w.appdev_key, display_name=w.display_name, archived=w.archived, description=w.description
    )


class CatalogGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_widget(self, widget_id: str) -> WidgetInfo:
        return _info(service.get_widget(self.session, widget_id))

    def require_active_widget(self, widget_id: str) -> WidgetInfo:
        return _info(service.require_active_widget(self.session, widget_id))

    def get_widgets(self, widget_ids: list[str]) -> dict[str, WidgetInfo]:
        return {wid: _info(w) for wid, w in service.get_widgets(self.session, widget_ids).items()}

    def widget_exists(self, widget_id: str) -> bool:
        return service.widget_exists(self.session, widget_id)

    def validate_widget(self, *, widget_id: str, operation: Literal["buy", "sell", "award"]) -> None:
        """Buying and awarding need an active widget; resale accepts an archived one."""
        with port_errors(_PORT_ERRORS):
            if operation == "sell":
                auctions = optional_gateway(AuctionGateway, self.session)
                if auctions is not None and auctions.widget_is_exclusive(widget_id):
                    raise ResaleNotAllowed("Auction-exclusive widgets cannot be resold.")
                service.get_widget(self.session, widget_id)
            else:
                service.require_active_widget(self.session, widget_id)
