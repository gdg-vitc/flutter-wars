"""Module F: team_widget_inventory, inventory_event (+ append-only trigger).

Revision ID: 0005_inventory
Revises: 0004_ledger
"""

from alembic import op

revision = "0005_inventory"
down_revision = "0004_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE team_widget_inventory (
            team_id    UUID NOT NULL REFERENCES team(id) ON DELETE RESTRICT,
            widget_id  VARCHAR(40) NOT NULL REFERENCES widget(id) ON DELETE RESTRICT,
            quantity   INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (team_id, widget_id),
            CONSTRAINT ck_inventory_quantity_nonneg CHECK (quantity >= 0)
        );

        CREATE TABLE inventory_event (
            id             BIGSERIAL PRIMARY KEY,
            team_id        UUID NOT NULL REFERENCES team(id) ON DELETE RESTRICT,
            widget_id      VARCHAR(40) NOT NULL REFERENCES widget(id) ON DELETE RESTRICT,
            kind           VARCHAR(16) NOT NULL,
            delta          INTEGER NOT NULL,
            quantity_after INTEGER NOT NULL,
            ref_type       VARCHAR(40) NOT NULL,
            ref_id         VARCHAR(100) NOT NULL,
            reason         TEXT NOT NULL,
            actor          VARCHAR(200) NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_inv_event_delta_nonzero     CHECK (delta <> 0),
            CONSTRAINT ck_inv_event_qty_after_nonneg  CHECK (quantity_after >= 0),
            CONSTRAINT ck_inv_event_kind CHECK (kind IN ('INCREMENT','DECREMENT','ADJUST')),
            CONSTRAINT uq_inv_event_team_widget_ref_kind UNIQUE (team_id, widget_id, ref_type, ref_id, kind)
        );
        CREATE INDEX ix_inv_event_team_widget ON inventory_event (team_id, widget_id);

        CREATE TRIGGER trg_inv_event_append_only
            BEFORE UPDATE OR DELETE ON inventory_event
            FOR EACH ROW EXECUTE FUNCTION fw_forbid_mutation();
        CREATE TRIGGER trg_inv_event_no_truncate
            BEFORE TRUNCATE ON inventory_event
            FOR EACH STATEMENT EXECUTE FUNCTION fw_forbid_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inventory_event; DROP TABLE IF EXISTS team_widget_inventory;")
