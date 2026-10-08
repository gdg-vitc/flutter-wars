"""Module D error codes. All are AppError, so every response uses the shared
{"error": {"code", "message", "details"}} shape (spec §2.2)."""

from app.core.errors import AppError


class WidgetNotFound(AppError):
    def __init__(self, widget_id: str) -> None:
        super().__init__("WIDGET_NOT_FOUND", "Widget not found", 404, {"widget_id": widget_id})


class WidgetArchived(AppError):
    """The widget exists but is archived: it can't be listed, bought or edited (it can be restored)."""

    def __init__(self, widget_id: str) -> None:
        super().__init__("WIDGET_ARCHIVED", "Widget is archived", 409, {"widget_id": widget_id})


class WidgetNotArchived(AppError):
    def __init__(self, widget_id: str) -> None:
        super().__init__("WIDGET_NOT_ARCHIVED", "Widget is not archived", 409, {"widget_id": widget_id})


class WidgetDuplicate(AppError):
    def __init__(self, field: str) -> None:
        super().__init__("WIDGET_DUPLICATE", f"A widget with this {field} already exists", 409, {"field": field})


class FieldImmutable(AppError):
    def __init__(self, field: str) -> None:
        super().__init__("FIELD_IMMUTABLE", f"'{field}' can never change once a widget exists", 422, {"field": field})


class VersionConflict(AppError):
    def __init__(self, current: int) -> None:
        super().__init__(
            "VERSION_CONFLICT",
            "Someone else changed this widget first; reload and retry",
            409,
            {"current_version": current},
        )


class InvalidWidgetData(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_WIDGET_DATA", message, 422)
