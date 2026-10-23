"""장표 라우터(ADR 0037) — 틀은 시스템 관리자, 장표는 누구나 만들고 작성자 · 시스템 관리자가
고친다. 보는 것은 누구나(ADR 0034).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from app.modules.ontology.services import InvalidValue
from app.modules.slides import pptx, services
from app.modules.slides.models import Slide, SlideRef, SlideTemplate
from app.modules.slides.schemas import (
    AREA_LIMIT_MAX,
    CandidateOut,
    CandidatesOut,
    PreviewIn,
    RenderOut,
    SlideCreateIn,
    SlideOut,
    SlidePatchIn,
    SlideSummaryOut,
    SubjectOut,
    TemplateIn,
    TemplateOut,
    UsedInOut,
)
from app.shared import audit
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import Conflict, NotFound, code
from app.shared.pagination import Page, clamp_limit
from app.shared.permissions import (
    can_edit_authored,
    require_authored_edit,
    visible_owner_clause,
)

router = APIRouter(prefix="/slides", tags=["slides"])

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
#: 파일 이름에 못 쓰는 글자 — Windows 가 거절하는 것.
_UNSAFE = re.compile(r'[\\/:*?"<>|\n\r\t]+')


# --- 틀 ----------------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[TemplateOut]:
    """틀 전부 — 장표를 만들 때 고른다. 이름 차례."""
    rows = list(db.scalars(select(SlideTemplate).order_by(SlideTemplate.name)))
    return [_template_out(db, one) for one in rows]


@router.get("/templates/{template_id}", response_model=TemplateOut)
def get_template(
    template_id: uuid.UUID,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TemplateOut:
    return _template_out(db, _template(db, template_id))


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(
    payload: TemplateIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> TemplateOut:
    """틀을 만든다 — **세워 보고** 저장한다(틀린 주소 · 칸은 어느 영역의 무엇인지 말하며
    거절)."""
    subject_type = services.validate(db, payload)
    _name_free(db, payload.name)
    row = SlideTemplate(
        name=payload.name.strip(),
        description=payload.description,
        subject_type_id=subject_type.id,
        rows=payload.rows,
        cols=payload.cols,
        areas=[one.model_dump() for one in payload.areas],
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="slides.template.create",
        actor=user,
        target_table="slide_templates",
        target_id=row.id,
        target_label=row.name,
    )
    db.commit()
    db.refresh(row)
    return _template_out(db, row)


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: uuid.UUID,
    payload: TemplateIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> TemplateOut:
    """틀을 통째로 고친다. **주제 타입은 바꾸지 못한다** — 이 틀로 만든 장표의 주제가 다른
    타입이
    된다. 영역의 키를 바꾸거나 빼면 그 영역에 써 둔 글 · 고른 기록은 장표에서 안
    보인다(남는다)."""
    row = _template(db, template_id)
    subject_type = services.validate(db, payload)
    if subject_type.id != row.subject_type_id and _slide_count(db, row.id):
        raise Conflict(
            code("SLIDES", 20),
            "이 틀로 만든 장표가 있어 주제 타입을 변경할 수 없습니다 — 새 틀을 생성하세요.",
        )
    if payload.name.strip() != row.name:
        _name_free(db, payload.name)
    row.name = payload.name.strip()
    row.description = payload.description
    row.subject_type_id = subject_type.id
    row.rows = payload.rows
    row.cols = payload.cols
    row.areas = [one.model_dump() for one in payload.areas]
    audit.record(
        db,
        action="slides.template.update",
        actor=user,
        target_table="slide_templates",
        target_id=row.id,
        target_label=row.name,
    )
    db.commit()
    db.refresh(row)
    return _template_out(db, row)


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(
    template_id: uuid.UUID,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> Response:
    """틀을 지운다 — **이 틀로 만든 장표가 없을 때만.** 장표가 있으면 몇 장인지 말하며
    막는다."""
    row = _template(db, template_id)
    used = int(
        db.scalar(select(func.count()).select_from(Slide).where(Slide.template_id == row.id))
        or 0
    )
    if used:
        raise Conflict(
            code("SLIDES", 21),
            f"이 틀로 만든 장표가 {used}장 있습니다(삭제한 것 포함) — 틀을 삭제하면 그 장표를 "
            "표시할 수 없습니다.",
            details={"slides": used},
        )
    audit.record(
        db,
        action="slides.template.delete",
        actor=user,
        target_table="slide_templates",
        target_id=row.id,
        target_label=row.name,
    )
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/templates/preview", response_model=RenderOut)
def preview_template(
    payload: PreviewIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RenderOut:
    """저장하지 않은 틀을 주제 하나로 세워 본다 — 틀 편집의 미리 보기."""
    subject_type = services.validate(db, payload.template)
    subject = _subject(db, user, payload.subject_id, subject_type)
    return RenderOut(
        rows=payload.template.rows,
        cols=payload.template.cols,
        areas=services.render(
            db, user, [one.model_dump() for one in payload.template.areas], subject, {}
        ),
    )


# --- 장표 --------------------------------------------------------------------


@router.get("", response_model=Page[SlideSummaryOut])
def list_slides(
    template_id: uuid.UUID | None = Query(default=None, description="이 틀로 만든 것만"),
    subject_id: uuid.UUID | None = Query(default=None, description="이 주제의 것만"),
    q: str | None = Query(default=None, max_length=100, description="제목에 든 말"),
    mine: bool = Query(default=False, description="내가 만든 것만"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Page[SlideSummaryOut]:
    """장표 목록 — 새로 고친 것부터."""
    size = clamp_limit(limit)
    stmt = select(Slide).where(Slide.deleted_at.is_(None))
    if template_id is not None:
        stmt = stmt.where(Slide.template_id == template_id)
    if subject_id is not None:
        stmt = stmt.where(Slide.subject_id == subject_id)
    needle = (q or "").strip()
    if needle:
        stmt = stmt.where(Slide.title.icontains(needle, autoescape=True))
    if mine:
        stmt = stmt.where(Slide.author_id == user.id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(func.coalesce(Slide.edited_at, Slide.created_at).desc(), Slide.id)
            .limit(size)
            .offset(offset)
        )
    )
    return Page[SlideSummaryOut](
        items=[_summary(db, one) for one in rows], total=total, limit=size, offset=offset
    )


@router.post("", response_model=SlideOut, status_code=201)
def create_slide(
    payload: SlideCreateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SlideOut:
    """장표를 만든다 — 틀 하나와 주제 하나. 누구나."""
    template = _template(db, payload.template_id)
    subject_type = db.get(ObjectType, template.subject_type_id)
    assert subject_type is not None  # FK 다
    subject = _subject(db, user, payload.subject_id, subject_type)
    title = (payload.title or "").strip() or f"{subject.label} {template.name}"
    row = Slide(
        template_id=template.id,
        subject_id=subject.id,
        title=title[:200],
        author_id=user.id,
        author_name=user.display_name,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="slides.create",
        actor=user,
        target_table="slides",
        target_id=row.id,
        target_label=row.title,
    )
    db.commit()
    db.refresh(row)
    return _slide_out(db, user, row)


@router.get("/{slide_id}", response_model=SlideOut)
def get_slide(
    slide_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SlideOut:
    """장표 — 영역의 기록은 **지금** 센다(살아 있는 값)."""
    return _slide_out(db, user, _slide(db, slide_id))


@router.patch("/{slide_id}", response_model=SlideOut)
def update_slide(
    slide_id: uuid.UUID,
    payload: SlidePatchIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SlideOut:
    """제목 · 글 영역의 글 · 고르기 영역의 기록을 고친다 — 작성자와 시스템 관리자."""
    row = _slide(db, slide_id)
    require_authored_edit(user, row.author_id, what="장표", code_value=code("SLIDES", 30))
    template = _template(db, row.template_id)
    areas = {one["key"]: one for one in template.areas}
    content = {key: dict(value) for key, value in (row.content or {}).items()}
    for key, text in (payload.texts or {}).items():
        if (areas.get(key) or {}).get("kind") != "text":
            raise InvalidValue(code("SLIDES", 31), f"글 영역이 아닙니다: {key}")
        content.setdefault(key, {})["text"] = text
    for key, picked in (payload.picks or {}).items():
        area = areas.get(key) or {}
        if area.get("kind") != "records" or area.get("fill") != "pick" or area.get("pair_of"):
            raise InvalidValue(code("SLIDES", 32), f"선택 영역이 아닙니다: {key}")
        subject = db.get(ObjectInstance, row.subject_id) if row.subject_id else None
        content.setdefault(key, {})["picks"] = [
            str(one) for one in services.check_picks(db, user, area, subject, picked)
        ]
    for key, made in (payload.made or {}).items():
        if (areas.get(key) or {}).get("kind") != "text":
            raise InvalidValue(code("SLIDES", 31), f"글 영역이 아닙니다: {key}")
        if made is None:
            content.setdefault(key, {}).pop("made", None)
            continue
        visible = db.scalar(
            select(ObjectInstance.id).where(
                ObjectInstance.id == made,
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
        if visible is None:
            raise NotFound(code("SLIDES", 39), "만든 기록을 찾을 수 없습니다.")
        content.setdefault(key, {})["made"] = str(made)
    if payload.title is not None:
        row.title = payload.title.strip()
    row.content = content
    row.edited_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _slide_out(db, user, row)


@router.get(
    "/{slide_id}/pptx",
    response_class=Response,
    responses={200: {"content": {PPTX: {}}, "description": "장표 한 장의 pptx"}},
)
def slide_pptx(
    slide_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """장표를 pptx 로 — **지금** 센 값으로(살아 있는 값). 파일은 그때의 사본이라 이름 · 바닥에
    기준
    시각을 찍는다. 영역마다 PPT 표 하나(짝 영역은 표 하나)."""
    shown = _slide_out(db, user, _slide(db, slide_id))
    stamp = shown.rendered_at.astimezone().strftime("%Y%m%d-%H%M")
    name = f"{_UNSAFE.sub('_', shown.title).strip() or '장표'}_{stamp}.pptx"
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "")
    return Response(
        content=pptx.build(shown),
        media_type=PPTX,
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(name)}"
        },
    )


@router.get("/{slide_id}/areas/{area_key}/candidates", response_model=CandidatesOut)
def pick_candidates(
    slide_id: uuid.UUID,
    area_key: str,
    q: str = Query(default="", max_length=100, description="이름 · 식별자에 든 말"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CandidatesOut:
    """선택 영역의 후보 — 틀의 조건(주제와 이어지는 칸 · 고정 조건)에 맞는 것, 50건까지."""
    row = _slide(db, slide_id)
    area = next(
        (one for one in _template(db, row.template_id).areas if one.get("key") == area_key),
        None,
    )
    if area is None or area.get("fill") != "pick" or area.get("pair_of"):
        raise NotFound(code("SLIDES", 38), f"선택 영역이 아닙니다: {area_key}")
    subject = db.get(ObjectInstance, row.subject_id) if row.subject_id else None
    found, total = services.candidates(db, user, area, subject, q, AREA_LIMIT_MAX)
    return CandidatesOut(
        items=[CandidateOut(id=one.id, label=one.label) for one in found], total=total
    )


@router.delete("/{slide_id}", status_code=204)
def delete_slide(
    slide_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """장표를 지운다 — 목록에서 내려가고 행은 남는다. 기록은 그대로다(장표는 기록을 가리킬
    뿐)."""
    row = _slide(db, slide_id)
    require_authored_edit(user, row.author_id, what="장표", code_value=code("SLIDES", 33))
    row.deleted_at = datetime.now(UTC)
    row.deleted_by_id = user.id
    audit.record(
        db,
        action="slides.delete",
        actor=user,
        target_table="slides",
        target_id=row.id,
        target_label=row.title,
    )
    db.commit()
    return Response(status_code=204)


@router.get("/used-in/{object_id}", response_model=list[UsedInOut])
def used_in(
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[UsedInOut]:
    """이 객체가 쓰인 장표 — 주제인 장표와, **마지막으로 열었을 때** 영역에 실렸던 장표. 장표는
    살아 있는 값이라 그 뒤에 빠졌을 수 있다(`seen_at`)."""
    out: list[UsedInOut] = []
    subjects = db.scalars(
        select(Slide)
        .where(Slide.subject_id == object_id, Slide.deleted_at.is_(None))
        .order_by(func.coalesce(Slide.edited_at, Slide.created_at).desc())
        .limit(50)
    )
    for one in subjects:
        out.append(UsedInOut(slide=_summary(db, one), role="subject", areas=[], seen_at=None))
    seen: dict[uuid.UUID, tuple[Slide, list[str], datetime]] = {}
    for slide, area_key, seen_at in db.execute(
        select(Slide, SlideRef.area_key, SlideRef.seen_at)
        .join(SlideRef, SlideRef.slide_id == Slide.id)
        .where(SlideRef.object_id == object_id, Slide.deleted_at.is_(None))
        .order_by(SlideRef.seen_at.desc())
        .limit(200)
    ):
        template = db.get(SlideTemplate, slide.template_id)
        title = next(
            (
                str(one.get("title") or area_key)
                for one in (template.areas if template else [])
                if one.get("key") == area_key
            ),
            area_key,
        )
        if slide.id in seen:
            seen[slide.id][1].append(title)
        else:
            seen[slide.id] = (slide, [title], seen_at)
    for slide, titles, seen_at in seen.values():
        out.append(
            UsedInOut(slide=_summary(db, slide), role="record", areas=titles, seen_at=seen_at)
        )
    return out


# --- 도우미 ------------------------------------------------------------------


def _template(db: Session, template_id: uuid.UUID) -> SlideTemplate:
    row = db.get(SlideTemplate, template_id)
    if row is None:
        raise NotFound(code("SLIDES", 22), "틀을 찾을 수 없습니다.")
    return row


def _name_free(db: Session, name: str) -> None:
    if db.scalar(select(SlideTemplate.id).where(SlideTemplate.name == name.strip())):
        raise Conflict(code("SLIDES", 23), f"같은 이름의 틀이 있습니다: {name.strip()}")


def _slide_count(db: Session, template_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Slide)
            .where(Slide.template_id == template_id, Slide.deleted_at.is_(None))
        )
        or 0
    )


def _slide(db: Session, slide_id: uuid.UUID) -> Slide:
    row = db.get(Slide, slide_id)
    if row is None or row.deleted_at is not None:
        raise NotFound(code("SLIDES", 34), "장표를 찾을 수 없습니다.")
    return row


def _subject(
    db: Session, user: User, subject_id: uuid.UUID, subject_type: ObjectType
) -> ObjectInstance:
    """주제 — 틀의 주제 타입이고, 살아 있고, 이 사람이 볼 수 있는 객체."""
    found = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == subject_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if found is None:
        raise NotFound(code("SLIDES", 24), "주제 객체를 찾을 수 없습니다.")
    if found.type_id != subject_type.id:
        raise InvalidValue(
            code("SLIDES", 25),
            f"이 틀의 주제는 {subject_type.label}입니다 — 다른 타입의 객체를 선택했습니다.",
        )
    return found


def _template_out(db: Session, row: SlideTemplate) -> TemplateOut:
    subject_type = db.get(ObjectType, row.subject_type_id)
    assert subject_type is not None  # FK 다
    return TemplateOut(
        id=row.id,
        name=row.name,
        description=row.description,
        subject_type_slug=subject_type.slug,
        subject_type_label=subject_type.label,
        rows=row.rows,
        cols=row.cols,
        areas=row.areas,  # type: ignore[arg-type]
        slides=_slide_count(db, row.id),
        updated_at=row.updated_at,
    )


def _subject_out(db: Session, subject_id: uuid.UUID | None) -> SubjectOut | None:
    if subject_id is None:
        return None
    found = db.get(ObjectInstance, subject_id)
    if found is None:
        return None
    object_type = db.get(ObjectType, found.type_id)
    return SubjectOut(
        id=found.id,
        label=found.label,
        type_slug=object_type.slug if object_type else "",
        deleted=found.deleted_at is not None,
    )


def _summary(db: Session, row: Slide) -> SlideSummaryOut:
    template = db.get(SlideTemplate, row.template_id)
    return SlideSummaryOut(
        id=row.id,
        title=row.title,
        template_id=row.template_id,
        template_name=template.name if template else "",
        subject=_subject_out(db, row.subject_id),
        author_name=row.author_name,
        created_at=row.created_at,
        edited_at=row.edited_at,
    )


def _slide_out(db: Session, user: User, row: Slide) -> SlideOut:
    template = _template(db, row.template_id)
    subject = db.get(ObjectInstance, row.subject_id) if row.subject_id else None
    rendered_at = datetime.now(UTC)
    areas = services.render(db, user, template.areas, subject, row.content or {})
    # 지금 실린 기록을 적어 둔다 — 기록 상세의 「이 기록이 쓰인 장표」.
    services.remember(db, row, areas)
    db.commit()
    return SlideOut(
        **_summary(db, row).model_dump(),
        rows=template.rows,
        cols=template.cols,
        areas=areas,
        can_edit=can_edit_authored(user, row.author_id),
        rendered_at=rendered_at,
    )
