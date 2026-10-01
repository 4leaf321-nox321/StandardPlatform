"""인터페이스 — 여러 타입이 한 모양을 따른다(ADR 0006).

여러 그룹이 같은 개념을 따로 정의하게 되자 같은 것이 갈리기 시작했다(개발 DB 에서 `country`
가 한 타입은 고를 값, 다른 타입은 글자). 인터페이스는 공통 속성의 묶음이고, 구현한 타입은
**같은 키 · 같은 모양**의 속성을 가진다.

    object_interfaces           인터페이스 — slug · 이름 · 상위 인터페이스 · 목록 모양
    object_types.interface_slugs  구현하는 인터페이스(여럿)
    property_defs.owner_kind      'interface' 가 는다 — 표는 그대로다(칸이 글자라 제약이 없다)

**`object_types.parent_slug` 를 지운다.** 계층을 적는 자리가 둘이면 사람마다 다른 쪽에 적는다.
RDF 로만 쓰이던 메타라 잃는 것은 `rdfs:subClassOf` 한 줄이다 — 그래도 **지우기 전에 값이 있던
타입을 출력한다**(행을 새로 넣지는 않는다 — 마이그레이션에 행을 넣지 않는 것이 이 저장소의
규칙이다).

Revision ID: 0045_interfaces
Revises: 0044_group_parent
Create Date: 2026-10-01 08:39:48.517863

"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0045_interfaces"
down_revision: Union[str, Sequence[str], None] = "0044_group_parent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "object_interfaces",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("icon", sa.String(length=40), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "extends_slugs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "list_view",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("managed_by", sa.String(length=40), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_interfaces")),
        sa.UniqueConstraint("slug", name="uq_object_interfaces_slug"),
    )
    op.add_column(
        "object_types",
        sa.Column(
            "interface_slugs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )

    # **지우기 전에 남긴다** — 무엇이 사라졌는지 배포 기록에서 찾을 수 있게.
    if not context.is_offline_mode():
        rows = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT slug, parent_slug FROM object_types "
                    "WHERE parent_slug IS NOT NULL ORDER BY slug"
                )
            )
            .fetchall()
        )
        for slug, parent in rows:
            print(f"[0045] parent_slug 버림(인터페이스로 다시 적을 것): {slug} -> {parent}")
    op.drop_column("object_types", "parent_slug")


def downgrade() -> None:
    """Downgrade schema.

    `parent_slug` 는 칸만 돌아온다 — **값은 안 돌아온다**(지울 때 출력만 했다). 공통 속성
    정의(`owner_kind='interface'`)는 가리킬 표가 사라지므로 함께 지운다 — 안 지우면 주인 없는
    정의가 남는다.
    """
    op.execute("DELETE FROM property_defs WHERE owner_kind = 'interface'")
    op.add_column(
        "object_types",
        sa.Column("parent_slug", sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    )
    op.drop_column("object_types", "interface_slugs")
    op.drop_table("object_interfaces")
