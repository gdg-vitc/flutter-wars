-- Modules I/J only. Apply once in a reviewed migration transaction.

-- Foundation/Migration owner must add external FKs after agreeing owner table names.

CREATE TYPE auctionstate AS ENUM ('DRAFT', 'OPEN', 'CLOSED', 'SETTLED');

CREATE TYPE tradetype AS ENUM ('BUY', 'SELL');


CREATE TABLE auction (
	id UUID NOT NULL,
	round_id UUID NOT NULL,
	listing_id UUID NOT NULL,
	widget_id VARCHAR(40) NOT NULL,
	quantity INTEGER NOT NULL,
	state auctionstate NOT NULL,
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
	closes_at TIMESTAMP WITH TIME ZONE NOT NULL,
	minimum_bid INTEGER,
	accepted_bid_order BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_auction_quantity CHECK (quantity > 0),
	CONSTRAINT ck_auction_window CHECK (starts_at < closes_at),
	CONSTRAINT ck_auction_minimum CHECK (minimum_bid IS NULL OR minimum_bid BETWEEN 0 AND 2147483647),
	CONSTRAINT ck_auction_order CHECK (accepted_bid_order >= 0)
)

;

CREATE INDEX ix_auction_round_id ON auction (round_id);


CREATE TABLE trade_transaction (
	id UUID NOT NULL,
	team_id UUID NOT NULL,
	listing_id UUID NOT NULL,
	widget_id VARCHAR(40) NOT NULL,
	idempotency_key UUID NOT NULL,
	transaction_type tradetype NOT NULL,
	quantity INTEGER NOT NULL,
	unit_price INTEGER NOT NULL,
	gross_amount INTEGER NOT NULL,
	brokerage_amount INTEGER NOT NULL,
	final_amount INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_trade_gross_calculation CHECK (gross_amount = unit_price::bigint * quantity::bigint),
	CONSTRAINT ck_trade_final_calculation CHECK (final_amount = gross_amount - brokerage_amount),
	CONSTRAINT ck_trade_buy_brokerage CHECK (transaction_type <> 'BUY' OR brokerage_amount = 0),
	CONSTRAINT uq_trade_team_type_idempotency UNIQUE (team_id, transaction_type, idempotency_key),
	CONSTRAINT ck_trade_quantity_positive CHECK (quantity > 0),
	CONSTRAINT ck_trade_unit_price_range CHECK (unit_price BETWEEN 0 AND 2147483647),
	CONSTRAINT ck_trade_gross_amount_range CHECK (gross_amount BETWEEN 0 AND 2147483647),
	CONSTRAINT ck_trade_brokerage_amount_range CHECK (brokerage_amount BETWEEN 0 AND 2147483647),
	CONSTRAINT ck_trade_final_amount_range CHECK (final_amount BETWEEN 0 AND 2147483647)
)

;

CREATE INDEX ix_trade_team_created ON trade_transaction (team_id, created_at, id);

CREATE INDEX ix_trade_transaction_team_id ON trade_transaction (team_id);


CREATE TABLE auction_bid (
	id UUID NOT NULL,
	auction_id UUID NOT NULL,
	team_id UUID NOT NULL,
	amount INTEGER NOT NULL,
	amount_reached_at TIMESTAMP WITH TIME ZONE NOT NULL,
	amount_reached_order BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_bid_auction_team UNIQUE (auction_id, team_id),
	CONSTRAINT uq_bid_reached_order UNIQUE (auction_id, amount_reached_order),
	CONSTRAINT ck_bid_amount CHECK (amount BETWEEN 0 AND 2147483647),
	CONSTRAINT ck_bid_order CHECK (amount_reached_order > 0),
	FOREIGN KEY(auction_id) REFERENCES auction (id)
)

;

CREATE INDEX ix_auction_bid_auction_id ON auction_bid (auction_id);

CREATE INDEX ix_auction_bid_winner ON auction_bid (auction_id, amount, amount_reached_order);


CREATE TABLE auction_result (
	auction_id UUID NOT NULL,
	winner_team_id UUID,
	winning_amount INTEGER,
	settled_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (auction_id),
	CONSTRAINT ck_auction_result_winner CHECK ((winner_team_id IS NULL AND winning_amount IS NULL) OR (winner_team_id IS NOT NULL AND winning_amount IS NOT NULL AND winning_amount BETWEEN 0 AND 2147483647)),
	FOREIGN KEY(auction_id) REFERENCES auction (id)
)

;


CREATE TABLE auction_bid_receipt (
	id UUID NOT NULL,
	auction_id UUID NOT NULL,
	team_id UUID NOT NULL,
	idempotency_key UUID NOT NULL,
	bid_id UUID NOT NULL,
	amount INTEGER NOT NULL,
	amount_reached_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_bid_request UNIQUE (auction_id, team_id, idempotency_key),
	CONSTRAINT ck_bid_receipt_amount CHECK (amount BETWEEN 0 AND 2147483647),
	FOREIGN KEY(auction_id) REFERENCES auction (id),
	FOREIGN KEY(bid_id) REFERENCES auction_bid (id)
)

;

CREATE FUNCTION ij_reject_trade_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Trade history is immutable';
END;
$$;
CREATE TRIGGER trade_history_immutable BEFORE UPDATE OR DELETE ON trade_transaction
FOR EACH ROW EXECUTE FUNCTION ij_reject_trade_rewrite();
