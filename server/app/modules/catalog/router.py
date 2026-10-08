"""Module D HTTP routes (participant side). Organizer routes (/admin/widgets...) live in Module K
(app/modules/admin/catalog_router.py), so every /admin route shares one permission check + audit log."""

from fastapi import APIRouter, Depends, Path
from sqlmodel import Session

from app.core.auth import Principal, get_principal
from app.core.db import get_db
from app.modules.catalog import service
from app.modules.catalog.models import WIDGET_ID_PATTERN
from app.modules.catalog.schemas import WidgetPublicOut

router = APIRouter(tags=["catalog"])


@router.get("/widgets", response_model=list[WidgetPublicOut])
def list_widgets(_: Principal = Depends(get_principal), s: Session = Depends(get_db)) -> list[WidgetPublicOut]:
    """Active widgets only. Any logged-in user (participant or organizer)."""
    return [WidgetPublicOut.model_validate(w) for w in service.list_widgets(s, include_archived=False)]


@router.get("/widgets/{widget_id}", response_model=WidgetPublicOut)
def read_widget(
    widget_id: str = Path(pattern=WIDGET_ID_PATTERN),
    _: Principal = Depends(get_principal),
    s: Session = Depends(get_db),
) -> WidgetPublicOut:
    """Resolves archived widgets too (archived=true), so a team that owns one can still see what it is."""
    return WidgetPublicOut.model_validate(service.get_widget(s, widget_id))
