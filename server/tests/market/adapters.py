"""TEST ONLY: stand-in owner tables, never imported by production modules."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from app.contracts.marketplace import PurchaseListing
from app.contracts.marketplace_errors import (
    ConfigurationRequired,
    InsufficientCredits,
    InsufficientInventory,
    InsufficientStock,
    InvalidListing,
    MarketNotOpen,
    ResaleNotAllowed,
)

metadata = MetaData()
rounds = Table(
    "test_round",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("state", String, nullable=False),
)
catalog = Table("test_widget", metadata, Column("id", String(40), primary_key=True))
listings = Table(
    "test_listing",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("round_id", Uuid, nullable=False),
    Column("widget_id", String(40), nullable=False),
    Column("mode", String, nullable=False),
    Column("finite", Boolean, nullable=False),
    Column("stock", Integer),
    Column("price", Integer, nullable=False),
    CheckConstraint("stock IS NULL OR stock >= 0"),
)
wallets = Table(
    "test_wallet",
    metadata,
    Column("team_id", Uuid, primary_key=True),
    Column("balance", BigInteger, nullable=False),
    Column("reserved", BigInteger, nullable=False),
    CheckConstraint("balance >= 0 AND reserved >= 0 AND reserved <= balance"),
)
inventory = Table(
    "test_inventory",
    metadata,
    Column("team_id", Uuid, primary_key=True),
    Column("widget_id", String(40), primary_key=True),
    Column("quantity", Integer, nullable=False),
    CheckConstraint("quantity >= 0"),
)
reservations = Table(
    "test_reservation",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("team_id", Uuid, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("state", String, nullable=False),
    CheckConstraint("amount >= 0"),
)
entries = Table(
    "test_ledger_entry",
    metadata,
    Column("reference", Uuid, primary_key=True),
    Column("kind", String, primary_key=True),
    Column("team_id", Uuid, nullable=False),
    Column("amount", Integer, nullable=False),
)
allocations = Table(
    "test_auction_allocation",
    metadata,
    Column("auction_id", Uuid, primary_key=True),
    Column("listing_id", Uuid, nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("consumed", Boolean, nullable=False),
)


class MarketAdapter:
    def __init__(self, session):
        self.session = session

    def _listing(self, listing_id):
        row = self.session.execute(select(listings).where(listings.c.id == listing_id)).mappings().one_or_none()
        if row is None:
            raise InvalidListing()
        state = self.session.execute(
            select(rounds.c.state).where(rounds.c.id == row["round_id"]).with_for_update(read=True)
        ).scalar_one()
        row = (
            self.session.execute(select(listings).where(listings.c.id == listing_id).with_for_update()).mappings().one()
        )
        return row, state

    def _trade(self, listing_id, quantity, sell):
        row, state = self._listing(listing_id)
        if state != "OPEN":
            raise MarketNotOpen()
        if row["mode"] != "NORMAL":
            raise InvalidListing()
        if sell and not row["finite"]:
            raise ResaleNotAllowed()
        if not sell and row["finite"] and row["stock"] < quantity:
            raise InsufficientStock()
        return PurchaseListing(row["id"], row["round_id"], row["widget_id"])

    def get_for_purchase(self, *, listing_id, quantity):
        return self._trade(listing_id, quantity, False)

    def get_for_resale(self, *, listing_id, quantity):
        return self._trade(listing_id, quantity, True)

    def consume_stock(self, *, listing_id, quantity, trade_id):
        row = self.session.execute(select(listings).where(listings.c.id == listing_id)).mappings().one()
        if row["finite"]:
            changed = self.session.execute(
                update(listings)
                .where(listings.c.id == listing_id, listings.c.stock >= quantity)
                .values(stock=listings.c.stock - quantity)
            )
            if changed.rowcount != 1:
                raise InsufficientStock()

    def restore_stock(self, *, listing_id, quantity, trade_id):
        self.session.execute(
            update(listings).where(listings.c.id == listing_id).values(stock=listings.c.stock + quantity)
        )

    def guard_auction(self, *, auction_id, round_id, listing_id, widget_id, quantity, operation):
        row, state = self._listing(listing_id)
        if row["round_id"] != round_id or row["widget_id"] != widget_id or row["mode"] != "AUCTION":
            raise InvalidListing()
        if operation == "bid" and state != "OPEN":
            raise MarketNotOpen()
        allocation = (
            self.session.execute(select(allocations).where(allocations.c.auction_id == auction_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if allocation is None or allocation["listing_id"] != listing_id or allocation["quantity"] != quantity:
            raise ConfigurationRequired()
        if operation == "bid" and allocation["consumed"]:
            raise MarketNotOpen()

    def consume_auction_allocation(self, *, auction_id):
        result = self.session.execute(
            update(allocations)
            .where(allocations.c.auction_id == auction_id, allocations.c.consumed.is_(False))
            .values(consumed=True)
        )
        if result.rowcount != 1:
            raise ConfigurationRequired()


class PricingAdapter:
    def __init__(self, session):
        self.session = session

    def get_unit_price(self, *, listing_id):
        return self.session.execute(select(listings.c.price).where(listings.c.id == listing_id)).scalar_one()

    def record_trade(self, *, listing_id, transaction_type, quantity, unit_price, trade_id):
        # Static strategy fixture: no repricing effect.
        pass


class CatalogAdapter:
    def __init__(self, session):
        self.session = session

    def validate_widget(self, *, widget_id, operation):
        if self.session.execute(select(catalog.c.id).where(catalog.c.id == widget_id)).scalar_one_or_none() is None:
            raise InvalidListing()


class LedgerAdapter:
    def __init__(self, session, env):
        self.session, self.env = session, env

    def lock_accounts(self, *, team_ids):
        for team in sorted(set(team_ids), key=str):
            row = self.session.execute(select(wallets).where(wallets.c.team_id == team).with_for_update()).one_or_none()
            if row is None:
                raise InsufficientCredits()

    def _wallet(self, team_id):
        self.lock_accounts(team_ids=[team_id])
        return self.session.execute(select(wallets).where(wallets.c.team_id == team_id)).mappings().one()

    def debit(self, *, team_id, amount, trade_id):
        row = self._wallet(team_id)
        if row["balance"] - row["reserved"] < amount:
            raise InsufficientCredits()
        self.session.execute(
            update(wallets).where(wallets.c.team_id == team_id).values(balance=wallets.c.balance - amount)
        )
        self.session.execute(entries.insert().values(reference=trade_id, kind="BUY", team_id=team_id, amount=amount))

    def credit(self, *, team_id, amount, trade_id):
        self._wallet(team_id)
        self.session.execute(
            update(wallets).where(wallets.c.team_id == team_id).values(balance=wallets.c.balance + amount)
        )
        self.session.execute(entries.insert().values(reference=trade_id, kind="SELL", team_id=team_id, amount=amount))
        if self.env.fail_credit:
            raise RuntimeError("injected credit failure")

    def reserve(self, *, team_id, reservation_id, additional_amount):
        row = self._wallet(team_id)
        if row["balance"] - row["reserved"] < additional_amount:
            raise InsufficientCredits()
        self.session.execute(
            update(wallets).where(wallets.c.team_id == team_id).values(reserved=wallets.c.reserved + additional_amount)
        )
        statement = insert(reservations).values(
            id=reservation_id, team_id=team_id, amount=additional_amount, state="ACTIVE"
        )
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[reservations.c.id],
                set_={"amount": reservations.c.amount + additional_amount},
            )
        )

    def release(self, *, team_id, reservation_id):
        self._wallet(team_id)
        row = (
            self.session.execute(select(reservations).where(reservations.c.id == reservation_id).with_for_update())
            .mappings()
            .one()
        )
        if row["team_id"] != team_id:
            raise ConfigurationRequired()
        if row["state"] != "ACTIVE":
            return
        self.session.execute(
            update(wallets).where(wallets.c.team_id == team_id).values(reserved=wallets.c.reserved - row["amount"])
        )
        self.session.execute(update(reservations).where(reservations.c.id == reservation_id).values(state="RELEASED"))

    def settle(self, *, team_id, reservation_id, amount, reference):
        self._wallet(team_id)
        row = (
            self.session.execute(select(reservations).where(reservations.c.id == reservation_id).with_for_update())
            .mappings()
            .one()
        )
        if row["team_id"] != team_id or row["amount"] != amount:
            raise ConfigurationRequired()
        if row["state"] == "SETTLED":
            return
        if row["state"] != "ACTIVE":
            raise ConfigurationRequired()
        self.session.execute(
            update(wallets)
            .where(wallets.c.team_id == team_id)
            .values(balance=wallets.c.balance - amount, reserved=wallets.c.reserved - amount)
        )
        self.session.execute(update(reservations).where(reservations.c.id == reservation_id).values(state="SETTLED"))
        self.session.execute(
            entries.insert().values(reference=reference, kind="AUCTION", team_id=team_id, amount=amount)
        )


class InventoryAdapter:
    def __init__(self, session, env):
        self.session, self.env = session, env

    def add(self, *, team_id, widget_id, quantity, reference):
        statement = insert(inventory).values(team_id=team_id, widget_id=widget_id, quantity=quantity)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[inventory.c.team_id, inventory.c.widget_id],
                set_={"quantity": inventory.c.quantity + quantity},
            )
        )
        if self.env.fail_inventory:
            raise RuntimeError("injected inventory failure")

    def remove(self, *, team_id, widget_id, quantity, reference):
        result = self.session.execute(
            update(inventory)
            .where(
                inventory.c.team_id == team_id,
                inventory.c.widget_id == widget_id,
                inventory.c.quantity >= quantity,
            )
            .values(quantity=inventory.c.quantity - quantity)
        )
        if result.rowcount != 1:
            raise InsufficientInventory()


class FixedTestBrokerage:
    """Fixture-only policy: a fixed whole-credit fee, not a production rule."""

    def fee(self, **kwargs):
        return 20
