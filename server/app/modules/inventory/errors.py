"""Module F error codes. Any of these raised inside a caller's transaction means: roll back."""

from app.core.errors import AppError


class InvalidQuantity(AppError):
    def __init__(self, qty: object) -> None:
        super().__init__(
            "INVALID_QUANTITY", "Quantity must be a non-zero whole number within range", 422, {"quantity": str(qty)}
        )


class InvalidReference(AppError):
    def __init__(self, field: str) -> None:
        super().__init__("INVALID_REFERENCE", f"'{field}' is missing or too long", 422, {"field": field})


class TeamNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("TEAM_NOT_FOUND", "Team not found", 404)


class WidgetNotFound(AppError):
    def __init__(self, widget_id: str) -> None:
        super().__init__("WIDGET_NOT_FOUND", "Widget not found", 404, {"widget_id": widget_id})


class InsufficientQuantity(AppError):
    def __init__(self, owned: int, requested: int) -> None:
        super().__init__(
            "INSUFFICIENT_QUANTITY",
            "Team does not own enough of this widget",
            409,
            {"owned": owned, "requested": requested},
        )


class DuplicateReference(AppError):
    def __init__(self, ref_type: str, ref_id: str, kind: str) -> None:
        super().__init__(
            "DUPLICATE_REFERENCE",
            "This inventory change was already applied",
            409,
            {"ref_type": ref_type, "ref_id": ref_id, "kind": kind},
        )
