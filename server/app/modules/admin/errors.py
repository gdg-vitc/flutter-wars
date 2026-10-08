"""Module K error codes."""

from app.core.errors import AppError


class NotOrganizer(AppError):
    # Same message for "not logged in as organizer" and "deactivated": never reveal which.
    def __init__(self) -> None:
        super().__init__("FORBIDDEN", "Organizer access required", 403)


class MissingPermission(AppError):
    def __init__(self, permission: str) -> None:
        super().__init__("MISSING_PERMISSION", "Your organizer role cannot do this", 403, {"permission": permission})


class OrganizerExists(AppError):
    def __init__(self) -> None:
        super().__init__("ORGANIZER_EXISTS", "An organizer with this email already exists", 409)


class OrganizerNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("ORGANIZER_NOT_FOUND", "Organizer not found", 404)


class LastOwner(AppError):
    def __init__(self) -> None:
        super().__init__("LAST_OWNER", "At least one active OWNER must remain", 409)


class SelfLockout(AppError):
    def __init__(self) -> None:
        super().__init__("SELF_LOCKOUT", "You cannot deactivate or demote yourself", 409)


class VersionConflict(AppError):
    def __init__(self, current: int) -> None:
        super().__init__(
            "VERSION_CONFLICT", "Someone else changed this first; reload and retry", 409, {"current_version": current}
        )


class ConfirmationRequired(AppError):
    def __init__(self, expected: str) -> None:
        super().__init__(
            "CONFIRMATION_REQUIRED", "Type the confirmation text exactly to continue", 422, {"expected": expected}
        )


class OperationFrozen(AppError):
    """Raised by ensure_not_frozen(). HTTP 423 Locked."""

    def __init__(self, scope: str, reason: str | None) -> None:
        super().__init__(
            "OPERATION_FROZEN",
            "This action is temporarily paused by the organizers",
            423,
            {"scope": scope, "reason": reason},
        )


class TeamNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("TEAM_NOT_FOUND", "Team not found", 404)


class TeamNameTaken(AppError):
    def __init__(self, names: list[str]) -> None:
        super().__init__("TEAM_NAME_TAKEN", "Team name already exists", 409, {"names": names})


class TeamImportInvalid(AppError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "TEAM_IMPORT_INVALID", "Nothing was imported; fix these rows first", 422, {"problems": problems}
        )


class DependencyNotAvailable(AppError):
    def __init__(self, module: str) -> None:
        super().__init__("DEPENDENCY_NOT_AVAILABLE", f"{module} is not connected yet", 503, {"module": module})
