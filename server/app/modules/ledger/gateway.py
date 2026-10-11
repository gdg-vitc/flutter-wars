"""Module E's gateway (app/contracts/ledger.py). Thin: the rules live in service.py.

Trading and auction references written to the ledger:
  trade/<trade_id> (DEBIT purchase, CREDIT resale),
  auction_bid/<bid_id> (hold), auction/<auction_id> (CAPTURE)
"""

from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session, func, select

from app.contracts.ledger import WalletView
from app.contracts.marketplace_errors import ConfigurationRequired, InsufficientCredits, port_errors
from app.modules.ledger import service as ledger
from app.modules.ledger.models import CreditLedgerEntry, CreditReservation

ACTOR = "system:marketplace"
_PORT_ERRORS = {"INSUFFICIENT_CREDITS": InsufficientCredits, "WALLET_NOT_FOUND": InsufficientCredits}


class LedgerGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- reads and organizer actions

    def initial_funding(self, team_id: UUID) -> int:
        return int(
            self.session.exec(
                select(func.coalesce(func.sum(CreditLedgerEntry.amount), 0)).where(
                    CreditLedgerEntry.team_id == team_id, CreditLedgerEntry.kind == "GRANT"
                )
            ).one()
        )

    def get_wallet(self, team_id: UUID) -> WalletView:
        return ledger.get_wallet(self.session, team_id)

    def get_wallets(self, team_ids: list[UUID]) -> dict[UUID, WalletView]:
        return ledger.get_wallets(self.session, team_ids)

    def grant_initial(self, team_id: UUID, amount: int, *, actor: str) -> None:
        ledger.grant_initial(self.session, team_id, amount, actor=actor)

    # --- trading and auctions

    def lock_accounts(self, *, team_ids: Sequence[UUID]) -> None:
        with port_errors(_PORT_ERRORS):
            ledger.lock_wallets(self.session, list(team_ids))

    def debit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None:
        if amount == 0:
            return
        with port_errors(_PORT_ERRORS):
            ledger.debit(
                self.session,
                team_id,
                amount,
                ref_type="trade",
                ref_id=str(trade_id),
                reason="Market purchase",
                actor=ACTOR,
            )

    def credit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None:
        if amount == 0:
            return
        with port_errors(_PORT_ERRORS):
            ledger.credit(
                self.session,
                team_id,
                amount,
                ref_type="trade",
                ref_id=str(trade_id),
                reason="Market resale",
                actor=ACTOR,
            )

    def _hold(self, team_id: UUID, reservation_id: UUID) -> CreditReservation | None:
        return ledger.get_active_reservation(self.session, team_id, ref_type="auction_bid", ref_id=str(reservation_id))

    def reserve(self, *, team_id: UUID, reservation_id: UUID, additional_amount: int) -> None:
        """Raise the bid's hold by `additional_amount` (a hold is replaced, never edited)."""
        if additional_amount == 0:
            return  # a zero first bid holds nothing
        with port_errors(_PORT_ERRORS):
            current = self._hold(team_id, reservation_id)
            total = additional_amount
            if current is not None:
                total += current.amount
                ledger.release(self.session, current.id)
            ledger.reserve(self.session, team_id, total, ref_type="auction_bid", ref_id=str(reservation_id))

    def release(self, *, team_id: UUID, reservation_id: UUID) -> None:
        with port_errors(_PORT_ERRORS):
            current = self._hold(team_id, reservation_id)
            if current is not None:
                ledger.release(self.session, current.id)

    def settle(self, *, team_id: UUID, reservation_id: UUID, amount: int, reference: UUID) -> None:
        with port_errors(_PORT_ERRORS):
            current = self._hold(team_id, reservation_id)
            held = current.amount if current is not None else 0
            if held != amount:
                raise ConfigurationRequired("Auction hold does not match the winning bid.")
            if current is None:
                return  # a zero winning bid has nothing to capture
            ledger.capture(
                self.session,
                current.id,
                ref_type="auction",
                ref_id=str(reference),
                reason="Auction won",
                actor=ACTOR,
            )
