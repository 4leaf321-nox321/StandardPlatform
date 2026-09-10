"""초기 스키마 — 계정·부서·토큰·공지·알림·감사.

한 리비전에 모은 이유: 이 시점 이전의 DB 는 없다. 표를 하나씩 나눠 놓으면 첫
설치에서 열몇 번을 순서대로 돌려야 하고, 그 순서가 곧 FK 순서라 한 번 어긋나면
읽기 어렵다.

**행은 여기서 안 심는다.** 첫 부서와 관리자 계정은 설치 시드가 만든다
(`scripts/seed_install.py`) — 마이그레이션에 넣으면 모델로 표를 만드는 시험이 그
행을 못 받아서 같은 목록을 시험 쪽에 한 벌 더 적게 되고, 두 벌은 반드시 갈린다.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _pk() -> sa.Column:
    return sa.Column("id", PgUUID(as_uuid=True), primary_key=True)


def upgrade() -> None:
    # --- 조직과 계정 --------------------------------------------------------
    #
    # workspaces 가 먼저다. users.home_workspace_id 가 그것을 가리킨다.
    op.create_table(
        "workspaces",
        _pk(),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("parent_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["workspaces.id"],
            name="fk_workspaces_parent_id_workspaces",
            # RESTRICT — 부서는 어차피 지우지 않고 보관한다. CASCADE 로 두면
            # 언젠가 실수로 부모를 지웠을 때 하위 부서가 조용히 함께 사라진다.
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"], unique=True)
    op.create_index("ix_workspaces_parent_id", "workspaces", ["parent_id"])

    op.create_table(
        "users",
        _pk(),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("is_system_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("home_workspace_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("requested_workspace_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failed_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["home_workspace_id"],
            ["workspaces.id"],
            name="fk_users_home_workspace_id_workspaces",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_workspace_id"],
            ["workspaces.id"],
            name="fk_users_requested_workspace_id_workspaces",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name="fk_users_decided_by_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    op.create_table(
        "workspace_members",
        _pk(),
        sa.Column("workspace_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_members_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workspace_members_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_pair"),
    )
    op.create_index("ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"])
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    # --- 토큰 ---------------------------------------------------------------

    op.create_table(
        "refresh_tokens",
        _pk(),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_refresh_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_tokens.id"],
            name="fk_refresh_tokens_replaced_by_id_refresh_tokens",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )

    op.create_table(
        "personal_access_tokens",
        _pk(),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False, server_default='["read"]'),
        # `<APP_SLUG>_pat_` + 6자. slug 가 길어지면 함께 길어지므로 넉넉히 둔다 —
        # 좁으면 토큰을 발급하는 자리에서 터지는데, 거기서는 원인이 안 보인다.
        sa.Column("prefix", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_personal_access_tokens_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_personal_access_tokens_user_id", "personal_access_tokens", ["user_id"])
    op.create_index("ix_personal_access_tokens_prefix", "personal_access_tokens", ["prefix"])
    op.create_index(
        "ix_personal_access_tokens_token_hash",
        "personal_access_tokens",
        ["token_hash"],
        unique=True,
    )

    # --- 공지와 알림 --------------------------------------------------------

    op.create_table(
        "notices",
        _pk(),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="info"),
        sa.Column("is_popup", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_notices_author_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_notices_published_at", "notices", ["published_at"])

    op.create_table(
        "notice_reads",
        _pk(),
        sa.Column("notice_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["notice_id"],
            ["notices.id"],
            name="fk_notice_reads_notice_id_notices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notice_reads_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("notice_id", "user_id", name="uq_notice_reads_pair"),
    )
    op.create_index("ix_notice_reads_notice_id", "notice_reads", ["notice_id"])
    op.create_index("ix_notice_reads_user_id", "notice_reads", ["user_id"])

    op.create_table(
        "notifications",
        _pk(),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(300), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notifications_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_kind", "notifications", ["kind"])
    op.create_index("ix_notifications_read_at", "notifications", ["read_at"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    # --- 기록 ---------------------------------------------------------------

    op.create_table(
        "access_logs",
        _pk(),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_access_logs_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_access_logs_user_id", "access_logs", ["user_id"])
    op.create_index("ix_access_logs_action", "access_logs", ["action"])
    op.create_index("ix_access_logs_created_at", "access_logs", ["created_at"])

    op.create_table(
        "audit_entries",
        _pk(),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("actor_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("actor_label", sa.String(200), nullable=False),
        sa.Column("actor_client", sa.String(40), nullable=True),
        sa.Column("actor_token", sa.String(100), nullable=True),
        sa.Column("target_table", sa.String(60), nullable=False),
        # **외래키를 안 건다.** 지워진 대상의 기록이 그 삭제 때문에 사라지면 안 된다.
        sa.Column("target_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("target_label", sa.String(300), nullable=False),
        sa.Column("workspace_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("changes", JSONB(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_audit_entries_actor_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_audit_entries_action", "audit_entries", ["action"])
    op.create_index("ix_audit_entries_actor_id", "audit_entries", ["actor_id"])
    op.create_index("ix_audit_entries_actor_client", "audit_entries", ["actor_client"])
    op.create_index("ix_audit_entries_target_table", "audit_entries", ["target_table"])
    op.create_index("ix_audit_entries_target_id", "audit_entries", ["target_id"])
    op.create_index("ix_audit_entries_workspace_id", "audit_entries", ["workspace_id"])
    op.create_index("ix_audit_entries_request_id", "audit_entries", ["request_id"])
    op.create_index("ix_audit_entries_created_at", "audit_entries", ["created_at"])


def downgrade() -> None:
    # 만든 것의 역순. FK 가 걸린 쪽을 먼저 지운다.
    op.drop_table("audit_entries")
    op.drop_table("access_logs")
    op.drop_table("notifications")
    op.drop_table("notice_reads")
    op.drop_table("notices")
    op.drop_table("personal_access_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("workspace_members")
    op.drop_table("users")
    op.drop_table("workspaces")
