"""Module F — Team Widget Inventory. Public internal contract for Modules C, I, J, K."""

from app.modules.inventory.service import (
    InventoryItem,
    admin_adjust,
    decrement,
    get_quantity,
    get_team_inventory,
    increment,
    unit_counts,
    verify_inventory,
)

__all__ = [
    "InventoryItem",
    "admin_adjust",
    "decrement",
    "get_quantity",
    "get_team_inventory",
    "increment",
    "unit_counts",
    "verify_inventory",
]
