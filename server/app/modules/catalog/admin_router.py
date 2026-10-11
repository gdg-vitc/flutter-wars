"""Module D: organizer routes for the widget catalog.

Same paths as spec §6 (POST /admin/widgets, PATCH /admin/widgets/{id}, POST .../archive), plus
GET (organizer view incl. archived + notes) and POST .../restore (undo an accidental archive).
Every change: permission check (Module K) -> Module D's rules -> audit row (through
Module K's gateway) -> one commit.
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlmodel import Session

from app.contracts.admin import AuditAction, OrganizerPrincipal, Permission
from app.core.audit import audit
from app.core.auth import require_permission
from app.core.db import get_db
from app.modules.catalog import service as catalog
from app.modules.catalog.errors import ConfirmationRequired
from app.modules.catalog.models import WIDGET_ID_PATTERN
from app.modules.catalog.schemas import (
    WidgetAdminOut,
    WidgetArchiveIn,
    WidgetCreateIn,
    WidgetRestoreIn,
    WidgetUpdateIn,
)

router = APIRouter(prefix="/admin/widgets", tags=["admin: catalog"])

_view = require_permission(Permission.VIEW)
_manage = require_permission(Permission.CATALOG_MANAGE)
WidgetIdPath = Path(pattern=WIDGET_ID_PATTERN)


@router.get("", response_model=list[WidgetAdminOut])
def list_widgets(
    include_archived: bool = Query(default=True),
    _: OrganizerPrincipal = Depends(_view),
    s: Session = Depends(get_db),
) -> list[WidgetAdminOut]:
    return [WidgetAdminOut.model_validate(w) for w in catalog.list_widgets(s, include_archived=include_archived)]


@router.get("/{widget_id}", response_model=WidgetAdminOut)
def read_widget(
    widget_id: str = WidgetIdPath, _: OrganizerPrincipal = Depends(_view), s: Session = Depends(get_db)
) -> WidgetAdminOut:
    return WidgetAdminOut.model_validate(catalog.get_widget(s, widget_id))


@router.post("", response_model=WidgetAdminOut, status_code=201)
def create_widget(
    body: WidgetCreateIn, org: OrganizerPrincipal = Depends(_manage), s: Session = Depends(get_db)
) -> WidgetAdminOut:
    w = catalog.create_widget(
        s,
        widget_id=body.id,
        appdev_key=body.appdev_key,
        display_name=body.display_name,
        category=body.category,
        description=body.description,
        flutter_classes=body.flutter_classes,
        internal_notes=body.internal_notes,
    )
    audit(
        s,
        org,
        AuditAction.WIDGET_CREATE,
        target_type="widget",
        target_id=w.id,
        reason=body.reason,
        details={"appdev_key": w.appdev_key, "display_name": w.display_name, "category": w.category},
    )
    out = WidgetAdminOut.model_validate(w)
    s.commit()
    return out


@router.patch("/{widget_id}", response_model=WidgetAdminOut)
def update_widget(
    body: WidgetUpdateIn,
    widget_id: str = WidgetIdPath,
    org: OrganizerPrincipal = Depends(_manage),
    s: Session = Depends(get_db),
) -> WidgetAdminOut:
    w, diff = catalog.update_widget(s, widget_id, body.changes(), expected_version=body.expected_version)
    if diff:
        audit(
            s,
            org,
            AuditAction.WIDGET_UPDATE,
            target_type="widget",
            target_id=widget_id,
            reason=body.reason,
            details={"changes": diff, "version": w.version},
        )
    out = WidgetAdminOut.model_validate(w)
    s.commit()
    return out


@router.post("/{widget_id}/archive", response_model=WidgetAdminOut)
def archive_widget(
    body: WidgetArchiveIn,
    widget_id: str = WidgetIdPath,
    org: OrganizerPrincipal = Depends(_manage),
    s: Session = Depends(get_db),
) -> WidgetAdminOut:
    if body.confirm != widget_id:
        raise ConfirmationRequired(widget_id)  # type the widget id to archive it
    w = catalog.archive_widget(s, widget_id, expected_version=body.expected_version)
    audit(s, org, AuditAction.WIDGET_ARCHIVE, target_type="widget", target_id=widget_id, reason=body.reason)
    out = WidgetAdminOut.model_validate(w)
    s.commit()
    return out


@router.post("/{widget_id}/restore", response_model=WidgetAdminOut)
def restore_widget(
    body: WidgetRestoreIn,
    widget_id: str = WidgetIdPath,
    org: OrganizerPrincipal = Depends(_manage),
    s: Session = Depends(get_db),
) -> WidgetAdminOut:
    w = catalog.restore_widget(s, widget_id, expected_version=body.expected_version)
    audit(s, org, AuditAction.WIDGET_RESTORE, target_type="widget", target_id=widget_id, reason=body.reason)
    out = WidgetAdminOut.model_validate(w)
    s.commit()
    return out
