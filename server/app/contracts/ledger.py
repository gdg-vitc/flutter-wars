"""Module E (credit ledger and wallets) contract, for Modules I, J and K.

Everything runs in the caller's transaction. Lock order across modules:
operational_control -> market_listing -> team_wallet -> team_widget_inventory.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

# Largest single credit movement (ledger CHECK constraints use the same bound).
MAX_AMOUNT = 1_000_000


@dataclass(frozen=True)
class WalletView:
    team_id: UUID
    balance: int
    held: int
    available: int


class LedgerGateway(Protocol):
    OWNER: ClassVar[str] = "Module E ledger"

    # --- reads and organizer actions (Module K)
    def initial_funding(self, team_id: UUID) -> int: ...

    def get_wallet(self, team_id: UUID) -> WalletView: ...

    def get_wallets(self, team_ids: list[UUID]) -> dict[UUID, WalletView]:
        """Many wallets in one query; teams without a wallet show zeros."""
        ...

    def grant_initial(self, team_id: UUID, amount: int, *, actor: str) -> None:
        """Starting credits, at most once per team."""
        ...

    # --- trading and auctions (Modules I, J); errors are the marketplace errors
    def lock_accounts(self, *, team_ids: Sequence[UUID]) -> None:
        """Lock wallets in sorted UUID order; every spend/reservation uses them."""
        ...

    def debit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None: ...
    def credit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None: ...
    def reserve(self, *, team_id: UUID, reservation_id: UUID, additional_amount: int) -> None: ...
    def release(self, *, team_id: UUID, reservation_id: UUID) -> None: ...
    def settle(self, *, team_id: UUID, reservation_id: UUID, amount: int, reference: UUID) -> None: ...
