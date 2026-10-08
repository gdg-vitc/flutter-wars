"""TEMP local app until Module A provides the real entrypoint and module registration.
When A merges: delete this file and register our routers + the port wiring using A's mechanism."""

from fastapi import FastAPI

from app.core._temp_team_directory import TempTeamDirectory
from app.core.errors import register_error_handlers
from app.modules.admin import ports as admin_ports
from app.modules.admin.catalog_router import router as admin_catalog_router
from app.modules.admin.router import public_router as admin_public_router
from app.modules.admin.router import router as admin_router
from app.modules.admin.team_assets_router import router as admin_team_assets_router
from app.modules.catalog.router import router as catalog_router
from app.modules.inventory.router import router as inventory_router
from app.modules.ledger.router import router as ledger_router

app = FastAPI(title="Flutter Wars backend — Team 3 local (Modules D, E, F, K)")
register_error_handlers(app)

app.include_router(catalog_router)  # Module D: GET /widgets, /widgets/{id}
app.include_router(ledger_router)  # Module E: GET /wallet, /wallet/ledger
app.include_router(inventory_router)  # Module F: GET /inventory
app.include_router(admin_public_router)  # Module K: GET /controls
app.include_router(admin_router)  # Module K: /admin/...
app.include_router(admin_team_assets_router)  # Module K: /admin/teams/{id}/wallet|credits|inventory
app.include_router(admin_catalog_router)  # Module K: /admin/widgets (Module D's organizer routes)

# TEMP: Module B (Team 6) replaces this with their real team directory.
admin_ports.set_team_directory(TempTeamDirectory())
