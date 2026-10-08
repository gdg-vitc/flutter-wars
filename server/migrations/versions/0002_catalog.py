"""Module D: widget (the catalog). Never deleted, only archived; id and appdev_key never change.
No price, stock or allocation columns here (spec §6): those belong to Pricing, Market and Inventory.

Revision ID: 0002_catalog
Revises: 0001_temp_team
"""

from alembic import op

revision = "0002_catalog"
down_revision = "0001_temp_team"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE widget (
            id              VARCHAR(40)  PRIMARY KEY,
            appdev_key      VARCHAR(80)  NOT NULL,
            display_name    VARCHAR(60)  NOT NULL,
            description     VARCHAR(500),
            category        VARCHAR(30)  NOT NULL,
            flutter_classes JSONB        NOT NULL DEFAULT '[]'::jsonb,
            status          VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
            internal_notes  VARCHAR(1000),
            version         INTEGER      NOT NULL DEFAULT 1,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            archived_at     TIMESTAMPTZ,
            CONSTRAINT uq_widget_appdev_key      UNIQUE (appdev_key),
            CONSTRAINT ck_widget_id_format       CHECK (id ~ '^[a-z][a-z0-9_]{1,39}$'),
            CONSTRAINT ck_widget_status          CHECK (status IN ('ACTIVE','ARCHIVED')),
            CONSTRAINT ck_widget_archived_at     CHECK ((status = 'ARCHIVED') = (archived_at IS NOT NULL)),
            CONSTRAINT ck_widget_classes_array   CHECK (jsonb_typeof(flutter_classes) = 'array'),
            CONSTRAINT ck_widget_version_pos     CHECK (version >= 1)
        );
        CREATE INDEX ix_widget_status ON widget (status);

        -- Stable identity: once created, id and appdev_key can never change (spec §6 business rule).
        CREATE OR REPLACE FUNCTION fw_widget_identity_immutable() RETURNS trigger AS $$
        BEGIN
            IF NEW.id <> OLD.id OR NEW.appdev_key <> OLD.appdev_key THEN
                RAISE EXCEPTION 'widget id/appdev_key are immutable' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_widget_identity_immutable
            BEFORE UPDATE ON widget FOR EACH ROW EXECUTE FUNCTION fw_widget_identity_immutable();

        -- Widgets are archived, never deleted (history in ledger/inventory/trades must stay resolvable).
        CREATE OR REPLACE FUNCTION fw_widget_no_delete() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'widgets are never deleted; archive them instead (% blocked)', TG_OP
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_widget_no_delete
            BEFORE DELETE ON widget FOR EACH ROW EXECUTE FUNCTION fw_widget_no_delete();
        CREATE TRIGGER trg_widget_no_truncate
            BEFORE TRUNCATE ON widget FOR EACH STATEMENT EXECUTE FUNCTION fw_widget_no_delete();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS widget")
    op.execute("DROP FUNCTION IF EXISTS fw_widget_identity_immutable()")
    op.execute("DROP FUNCTION IF EXISTS fw_widget_no_delete()")
