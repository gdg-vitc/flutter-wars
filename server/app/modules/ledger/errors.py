"""Module E error codes. Any of these raised inside a caller's transaction means: roll back."""

from app.core.errors import AppError


class InvalidAmount(AppError):
    def __init__(self, amount: object) -> None:
        super().__init__(
            "INVALID_AMOUNT",
            "Amount must be a non-zero whole number within the allowed range",
            422,
            {"amount": str(amount)},
        )


class InvalidReference(AppError):
    def __init__(self, field: str) -> None:
        super().__init__("INVALID_REFERENCE", f"'{field}' is missing or too long", 422, {"field": field})


class TeamNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("TEAM_NOT_FOUND", "Team not found", 404)


class WalletNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("WALLET_NOT_FOUND", "This team has no wallet yet", 404)


class InsufficientCredits(AppError):
    def __init__(self, available: int, requested: int) -> None:
        super().__init__(
            "INSUFFICIENT_CREDITS",
            "Not enough available credits",
            409,
            {"available": available, "requested": requested},
        )


class DuplicateReference(AppError):
    def __init__(self, ref_type: str, ref_id: str, kind: str) -> None:
        super().__init__(
            "DUPLICATE_REFERENCE",
            "This operation was already applied",
            409,
            {"ref_type": ref_type, "ref_id": ref_id, "kind": kind},
        )


class ReservationNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("RESERVATION_NOT_FOUND", "Reservation not found", 404)


class ReservationNotActive(AppError):
    def __init__(self, status: str) -> None:
        super().__init__("RESERVATION_NOT_ACTIVE", "Reservation is no longer active", 409, {"status": status})


class LedgerInvariantError(AppError):
    """Should never happen. If it does, the integrity check / verify endpoint will show it."""

    def __init__(self, what: str) -> None:
        super().__init__("LEDGER_INVARIANT_BROKEN", "Internal ledger error", 500, {"check": what})
