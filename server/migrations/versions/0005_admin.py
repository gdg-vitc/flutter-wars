"""Module K: organizer, admin_action_log (append-only), operational_control (seeded).

Revision ID: 0005_admin
Revises: 0004_inventory
"""

from alembic import op

revision = "0005_admin"
down_revision = "0004_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organizer (
            id           UUID PRIMARY KEY,
            email        VARCHAR(254) NOT NULL UNIQUE,
            display_name VARCHAR(100) NOT NULL,
            role         VARCHAR(16)  NOT NULL,
            active       BOOLEAN      NOT NULL DEFAULT true,
            version      INTEGER      NOT NULL DEFAULT 1,
            created_by   VARCHAR(254) NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT ck_organizer_role        CHECK (role IN ('OWNER','OPERATOR','VIEWER')),
            CONSTRAINT ck_organizer_email_lower CHECK (email = lower(email))
        );

        CREATE TABLE admin_action_log (
            id          BIGSERIAL PRIMARY KEY,
            actor_id    VARCHAR(100) NOT NULL,
            actor_email VARCHAR(254) NOT NULL,
            actor_role  VARCHAR(16)  NOT NULL,
            action      VARCHAR(60)  NOT NULL,
            target_type VARCHAR(40)  NOT NULL,
            target_id   VARCHAR(100) NOT NULL,
            reason      TEXT         NOT NULL,
            details     JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT ck_admin_log_reason_nonblank CHECK (length(btrim(reason)) > 0),
            CONSTRAINT ck_admin_log_details_object  CHECK (jsonb_typeof(details) = 'object')
        );
        CREATE INDEX ix_admin_log_target ON admin_action_log (target_type, target_id);
        CREATE INDEX ix_admin_log_action ON admin_action_log (action);
        CREATE INDEX ix_admin_log_actor  ON admin_action_log (actor_email);

        -- fw_forbid_mutation() was created in 0003_ledger.
        CREATE TRIGGER trg_admin_log_append_only
            BEFORE UPDATE OR DELETE ON admin_action_log
            FOR EACH ROW EXECUTE FUNCTION fw_forbid_mutation();
        CREATE TRIGGER trg_admin_log_no_truncate
            BEFORE TRUNCATE ON admin_action_log
            FOR EACH STATEMENT EXECUTE FUNCTION fw_forbid_mutation();

        CREATE TABLE operational_control (
            scope      VARCHAR(16) PRIMARY KEY,
            frozen     BOOLEAN     NOT NULL DEFAULT false,
            reason     TEXT,
            changed_by VARCHAR(254),
            changed_at TIMESTAMPTZ,
            version    INTEGER     NOT NULL DEFAULT 1,
            CONSTRAINT ck_control_scope CHECK (scope IN ('ALL','TRADING','BIDDING'))
        );
        INSERT INTO operational_control (scope) VALUES ('ALL'), ('TRADING'), ('BIDDING');

        -- The three control rows must always exist: ensure_not_frozen() relies on them.
        CREATE TRIGGER trg_control_no_delete
            BEFORE DELETE ON operational_control
            FOR EACH ROW EXECUTE FUNCTION fw_forbid_mutation();
        CREATE TRIGGER trg_control_no_truncate
            BEFORE TRUNCATE ON operational_control
            FOR EACH STATEMENT EXECUTE FUNCTION fw_forbid_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operational_control")
    op.execute("DROP TABLE IF EXISTS admin_action_log")
    op.execute("DROP TABLE IF EXISTS organizer")
