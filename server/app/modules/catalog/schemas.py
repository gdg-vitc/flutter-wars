"""Module D request/response shapes.

Public shapes never contain organizer-only data (internal_notes, version, timestamps) — spec §6 DoD.
Requests are strict: unknown fields rejected, ints/bools must be real ints/bools, text is bounded.
"""

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.catalog.models import WIDGET_ID_PATTERN

_CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9_]{0,59}$")  # a Dart class name: Row, IconButton, ListView


def _safe_text(v: str) -> str:
    v = " ".join(v.split())
    if any(c in v for c in "<>") or any(ord(c) < 32 for c in v):
        raise ValueError("must not contain < > or control characters")
    return v


def _classes(v: list[str]) -> list[str]:
    for c in v:
        if not _CLASS_RE.fullmatch(c):
            raise ValueError(f"'{c}' is not a valid Flutter class name")
    return list(dict.fromkeys(v))  # de-duplicate, keep order


WidgetId = Annotated[str, Field(pattern=WIDGET_ID_PATTERN)]
AppdevKey = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:\-]{0,79}$")]
DisplayName = Annotated[str, StringConstraints(min_length=1, max_length=60), AfterValidator(_safe_text)]
Description = Annotated[str, StringConstraints(max_length=500), AfterValidator(_safe_text)]
Category = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,29}$")]
FlutterClasses = Annotated[list[str], Field(max_length=20), AfterValidator(_classes)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Version = Annotated[int, Field(strict=True, ge=1)]


# ---------------------------------------------------------------- responses


class WidgetPublicOut(BaseModel):
    """What participants (and the app) see. No organizer notes, no version, no status internals."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    appdev_key: str
    display_name: str
    description: str | None
    category: str
    flutter_classes: list[str]
    archived: bool


class WidgetAdminOut(WidgetPublicOut):
    status: str
    internal_notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


# ---------------------------------------------------------------- requests


class WidgetCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: WidgetId
    appdev_key: AppdevKey
    display_name: DisplayName
    category: Category
    description: Description | None = None
    flutter_classes: FlutterClasses = Field(default_factory=list)
    internal_notes: Notes | None = None
    reason: Reason = "Catalog setup"


class WidgetUpdateIn(BaseModel):
    """Send only the fields you change, plus the version you loaded.

    `id` / `appdev_key` are accepted here ONLY so we can answer a clear 422 FIELD_IMMUTABLE."""

    model_config = ConfigDict(extra="forbid")
    expected_version: Version
    reason: Reason = "Widget metadata edited"
    display_name: DisplayName | None = None
    description: Description | None = None
    category: Category | None = None
    flutter_classes: FlutterClasses | None = None
    internal_notes: Notes | None = None
    id: str | None = Field(default=None, max_length=80)
    appdev_key: str | None = Field(default=None, max_length=80)

    def changes(self) -> dict[str, object]:
        return {k: getattr(self, k) for k in self.model_fields_set - {"expected_version", "reason"}}


class WidgetArchiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: Version
    reason: Reason
    confirm: str = Field(max_length=40, description="Type the widget id exactly")


class WidgetRestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: Version
    reason: Reason
