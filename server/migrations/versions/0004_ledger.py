"""Module E: team_wallet, credit_ledger_entry, credit_reservation (+ append-only trigger).

Revision ID: 0004_ledger
Revises: 0003_catalog
"""

from alembic import op

revision = "0004_ledger"
down_revision = "0003_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- Shared helper: blocks UPDATE / DELETE / TRUNCATE on append-only tables.
        CREATE OR REPLACE FUNCTION fw_forbid_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only (% not allowed)', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TABLE team_wallet (
            team_id    UUID PRIMARY KEY REFERENCES team(id) ON DELETE RESTRICT,
            balance    BIGINT NOT NULL DEFAULT 0,
            held       BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_wallet_balance_nonneg   CHECK (balance >= 0),
            CONSTRAINT ck_wallet_held_nonneg      CHECK (held >= 0),
            CONSTRAINT ck_wallet_held_le_balance  CHECK (held <= balance)
        );

        CREATE TABLE credit_ledger_entry (
            id            BIGSERIAL PRIMARY KEY,
            team_id       UUID NOT NULL REFERENCES team(id) ON DELETE RESTRICT,
            kind          VARCHAR(16) NOT NULL,
            amount        BIGINT NOT NULL,
            balance_after BIGINT NOT NULL,
            ref_type      VARCHAR(40) NOT NULL,
            ref_id        VARCHAR(100) NOT NULL,
            reason        TEXT NOT NULL,
            actor         VARCHAR(200) NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_ledger_amount_nonzero       CHECK (amount <> 0),
            CONSTRAINT ck_ledger_amount_range         CHECK (amount BETWEEN -1000000 AND 1000000),
            CONSTRAINT ck_ledger_balance_after_nonneg CHECK (balance_after >= 0),
            CONSTRAINT ck_ledger_kind CHECK (kind IN ('GRANT','CREDIT','DEBIT','CAPTURE','ADJUST')),
            CONSTRAINT uq_ledger_team_ref_kind UNIQUE (team_id, ref_type, ref_id, kind)
        );
        CREATE INDEX ix_ledger_team_id_id ON credit_ledger_entry (team_id, id);

        CREATE TRIGGER trg_ledger_append_only
            BEFORE UPDATE OR DELETE ON credit_ledger_entry
            FOR EACH ROW EXECUTE FUNCTION fw_forbid_mutation();
        CREATE TRIGGER trg_ledger_no_truncate
            BEFORE TRUNCATE ON credit_ledger_entry
            FOR EACH STATEMENT EXECUTE FUNCTION fw_forbid_mutation();

        CREATE TABLE credit_reservation (
            id         UUID PRIMARY KEY,
            team_id    UUID NOT NULL REFERENCES team(id) ON DELETE RESTRICT,
            amount     BIGINT NOT NULL,
            status     VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
            ref_type   VARCHAR(40) NOT NULL,
            ref_id     VARCHAR(100) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at  TIMESTAMPTZ,
            CONSTRAINT ck_reservation_amount_pos CHECK (amount > 0),
            CONSTRAINT ck_reservation_amount_max CHECK (amount <= 1000000),
            CONSTRAINT ck_reservation_status CHECK (status IN ('ACTIVE','RELEASED','CAPTURED'))
        );
        CREATE UNIQUE INDEX uq_reservation_active_ref
            ON credit_reservation (team_id, ref_type, ref_id) WHERE status = 'ACTIVE';
        CREATE INDEX ix_reservation_team_status ON credit_reservation (team_id, status);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS credit_reservation;
        DROP TABLE IF EXISTS credit_ledger_entry;
        DROP TABLE IF EXISTS team_wallet;
        """
    )
    # fw_forbid_mutation() is left in place: Module F's tables may still use it.
