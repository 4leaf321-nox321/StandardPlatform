"""디지털 트윈 역량 — 확장 전용 표.

**기준 정보는 여기 없다.** 시험 항목과 시뮬레이션은 온톨로지 타입의 객체다(`objects`) —
별칭 · 일괄 입력 · 내보내기 · 부서 소유 · 코어 공개가 거기 이미 있어서, 전용 표로 두면 그
여섯을 다시 만들게 된다. 여기 있는 것은 **그 위에 얹히는 평가**다.

⚠️ 표 이름은 `cae_dt_` 로 시작한다 — 확장 `caegroup` 에 CAE 그룹의 다른 기능이 붙어도
   어느 기능의 표인지 이름이 말한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CaeDtSetting(Base):
    """이 설치의 설정 — **한 줄뿐이다.**

    어느 온톨로지 타입을 시험 항목 · 시뮬레이션으로 쓰는지 여기서 정한다. 타입 slug 를
    코드에 박으면 형제 설치에서 이름이 다를 때 깨지고, 온톨로지를 고치는 순간 확장이
    멈춘다 — 정의를 바꾸는 일이 배포가 아니라는 이 플랫폼의 전제와 어긋난다.
    """

    __tablename__ = "cae_dt_settings"
    # **행이 둘이 되면 어느 것이 참인지 물을 자리가 없다.** 그래서 DB 가 막는다.
    __table_args__ = (CheckConstraint("id = 1", name="ck_cae_dt_settings_single"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    subject_type_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """시험 항목 타입. 비어 있으면 화면이 「먼저 기준 정보를 정하세요」 를 말한다."""
    agent_type_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """시뮬레이션 타입."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CaeDtPair(Base):
    """연계 — 시험 항목과 시뮬레이션의 짝 하나. **평가가 붙는 자리다.**

    「이 시험을 이 시뮬레이션으로 어디까지 볼 수 있나」 가 이 모듈이 재는 것이고, 그
    물음의 단위가 연계다. 시험에만 매기면 어느 시뮬레이션의 값인지 모르고, 시뮬레이션에만
    매기면 같은 도구가 시험마다 다른 수준인 사실이 사라진다.
    """

    __tablename__ = "cae_dt_pairs"
    __table_args__ = (
        # **같은 짝이 둘이면 평가가 갈린다.** 어느 쪽이 참인지 화면이 답할 수 없다.
        UniqueConstraint("subject_id", "agent_id", name="uq_cae_dt_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True
    )
    """어느 부서의 연계인가 — 권한과 집계의 단위다. **RESTRICT** 다: 부서를 지우려 할 때
    걸린 연계가 목록에 뜨고(확장 지점), 모르고 지워 평가가 사라지는 일을 막는다."""
    subject_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), index=True
    )
    """시험 항목 객체. **CASCADE** 다 — 객체를 지우면 그 연계와 평가도 함께 간다. 객체가
    병합되면(`merged_into`) 이긴 쪽으로 옮긴다. 안 옮기면 평가가 사라진 객체를 가리키고,
    그 손실은 집계 숫자에서만 드러난다."""
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), index=True
    )
    """시뮬레이션 객체."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CaeDtAssessment(Base):
    """평가 — 연계의 축 하나. **축 종류마다 채우는 칸이 다르다.**

    원본은 값과 선택을 한 칸(`rung`)에 쉼표로 쌓았다. 여기서는 종류마다 칸을 가른다 —
    문자열을 갈라 읽는 코드가 화면 · API · 집계에 세 번 생기고, 그중 하나만 고쳐지는 날
    같은 평가가 다르게 읽힌다.

        수치형(가상검증률)      value + rung — **수준은 문턱이 정한다**(사람이 안 고른다)
        수준 선택(적용 범위)     rung
        선택형(자동화 · 시험 대체) rungs       — 켠 항목들. 서열은 켠 개수
        매트릭스(모델링 수준)    rungs + defects — 바탕 토글과 불량 유형별 재현
                                 (**수준은 셈이 접는다**)
    """

    __tablename__ = "cae_dt_assessments"
    __table_args__ = (
        # **축 하나에 평가는 하나다.** 둘이면 화면이 어느 것을 그릴지 정할 수 없다.
        UniqueConstraint("pair_id", "axis", name="uq_cae_dt_assessment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pair_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("cae_dt_pairs.id", ondelete="CASCADE"), index=True
    )
    """**CASCADE** — 연계를 해제하면 평가도 함께 간다. 화면이 그 수를 확인 문구에 넣는다."""
    axis: Mapped[str] = mapped_column(String(40))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    rung: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rungs: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    defects: Mapped[dict[str, dict[str, str]]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    """매트릭스 축의 불량 유형별 재현 — `{불량 유형: {test: "2026-07", market: …}}`.
    **수준은 이것을 세어 접는다**(`definitions.modeling_level`)."""
    note: Mapped[str] = mapped_column(Text, default="")
    """근거 — **비우면 저장하지 않는다.** 수준만 남은 평가는 다음 사람이 확인할 수 없다."""
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """축이 정한 칸 — 비교 시험 건수 · 오차 · 단계별 소요 시간. 모양은 축마다 다르다."""
    evidence_tier: Mapped[str] = mapped_column(String(20))
    """무엇을 보고 매겼나 — 진술 · 확인 · 검증."""
    evidence_ref: Mapped[str] = mapped_column(String(300), default="")
    """근거 자료(문서번호 · 파일명 · 화면 경로). **확인 · 검증이면 비울 수 없다** — 등급만
    받고 자료를 안 받으면 「검증」 이 말뿐이 된다."""
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    assessed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assessed_by_label: Mapped[str] = mapped_column(String(100), default="")
    """**그때의 이름을 박는다.** 계정이 지워지면 누가 매겼는지 모르게 되는데, 그것은 평가
    이력이 존재하는 이유와 정면으로 어긋난다."""


class CaeDtAssessmentHistory(Base):
    """평가가 바뀐 기록.

    **감사 기록으로 갈음하지 않는다.** 감사는 시스템 관리자만 읽는데, 이 이력은 그 자료를
    채운 **담당자가** 봐야 한다 — 「지난번엔 왜 이렇게 적었나」 가 다음 평가의 근거다.
    """

    __tablename__ = "cae_dt_assessment_history"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pair_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("cae_dt_pairs.id", ondelete="CASCADE"), index=True
    )
    axis: Mapped[str] = mapped_column(String(40), index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """바뀐 뒤의 모습 한 벌. 이전 값은 그 앞 줄이 들고 있다."""
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_by_label: Mapped[str] = mapped_column(String(100), default="")
