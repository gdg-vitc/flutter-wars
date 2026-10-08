"""Module D — Widget Catalog & Component Registry. Public internal contract for Modules C, F, G, H, I, J, K."""

from app.modules.catalog.models import Widget, WidgetStatus
from app.modules.catalog.service import (
    archive_widget,
    create_widget,
    get_widget,
    get_widgets,
    list_widgets,
    require_active_widget,
    restore_widget,
    update_widget,
    widget_exists,
)

__all__ = [
    "Widget",
    "WidgetStatus",
    "archive_widget",
    "create_widget",
    "get_widget",
    "get_widgets",
    "list_widgets",
    "require_active_widget",
    "restore_widget",
    "update_widget",
    "widget_exists",
]
