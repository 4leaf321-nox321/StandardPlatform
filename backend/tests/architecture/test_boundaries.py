"""구조 규칙을 시험이 지킨다.

지침 문서에만 적힌 규칙은 반드시 어긋난다 — 급할 때 사람은 문서를 안 읽는다.
여기서 검사하는 것만이 실제로 지켜지는 규칙이다.

**이 파일은 포크한 플랫폼이 그대로 들고 간다.** 도메인이 늘어날수록 값이 커지는
시험이라, 여기를 지우면 그때부터 경계는 관습일 뿐이다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
MODULES = BACKEND / "app" / "modules"

#: 모듈 이름이 프론트와 같아야 한다는 규칙의 예외. **사유와 함께** 적는다 —
#: 목록에 있다는 것 자체가 "여기는 일부러 다르다" 는 기록이다.
FRONTEND_MERGED: set[str] = {
    # 묶음 가져오기 — 지금은 로컬 정제 도구(pipeline/)와 MCP 가 부른다. 묶음을 올려
    # 미리 보는 화면이 생기면 그 모듈을 만들고 여기서 뺀다.
    "bundles",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_모듈끼리_라우터를_직접_부르지_않는다() -> None:
    """조립 지점은 main.py 하나다.

    모듈이 서로의 routes 를 import 하면 조립 순서가 여러 곳에 흩어지고, 그때
    "이 엔드포인트가 왜 안 뜨지" 를 물을 자리가 없어진다.
    """
    offenders: list[str] = []
    for path in MODULES.rglob("*.py"):
        module = path.relative_to(MODULES).parts[0]
        for name in _imports(path):
            if not name.startswith("app.modules."):
                continue
            other = name.split(".")[2]
            if other != module and name.endswith(".routes"):
                offenders.append(f"{path.relative_to(BACKEND)} -> {name}")
    assert not offenders, "모듈이 남의 라우터를 직접 부릅니다: " + ", ".join(offenders)


def test_shared_는_도메인_라우터를_모른다() -> None:
    """방향은 shared -> 모듈 한 쪽이다.

    shared 가 라우터를 알면 순환이 생기고, 그때 import 순서 하나로 서버가 안 뜬다.
    모델과 서비스는 부를 수 있다(권한 판정이 그것을 필요로 한다).
    """
    for path in (BACKEND / "app" / "shared").rglob("*.py"):
        for name in _imports(path):
            assert not name.endswith(".routes"), f"{path.name} 이 {name} 을 부릅니다"


def test_확장_지점은_도메인을_모른다() -> None:
    """`shared/extensions.py` 는 **레지스트리일 뿐이다.**

    여기가 도메인 모델을 알기 시작하면 "공통 틀" 이라는 말이 거짓이 되고, 포크한
    플랫폼은 안 쓰는 표를 import 하다 기동에서 터진다. 계정 모델만 예외다 —
    「누가 보고 있나」 를 받아야 남은 일을 사람에 따라 다르게 낼 수 있다.
    """
    allowed = {"app.modules.accounts.models"}
    for name in _imports(BACKEND / "app" / "shared" / "extensions.py"):
        if name.startswith("app.modules."):
            assert name in allowed, f"extensions.py 가 {name} 을 압니다"


def test_코어는_확장을_모른다() -> None:
    """확장은 인스턴스마다 켜고 끈다(`.env` 의 EXTENSIONS). 코어(`modules` · `shared`)가
    확장을 import 하면 **끈 인스턴스에서 코어가 안 뜨거나 없는 메뉴를 부른다** — 그리고
    그것은 확장을 켠 개발 PC 에서는 안 드러난다. 붙이는 자리는 `app/extensions.load` 하나다.
    """
    for root in ("modules", "shared"):
        for path in (BACKEND / "app" / root).rglob("*.py"):
            for name in _imports(path):
                assert not name.startswith("app.extensions"), (
                    f"{path.relative_to(BACKEND)} 이 확장 {name} 을 압니다"
                )


def test_코드는_제품_이름을_branding_에서_import_하지_않는다() -> None:
    """이름은 설치마다 다르다(`.env`). 코드가 `branding` 의 기본값을 쓰면 **모든 설치가
    틀의 이름으로 보인다** — 읽는 자리는 `get_settings().app_name` 뿐이다. `config.py` 만
    기본값을 받으려고 branding 을 안다."""
    for path in (BACKEND / "app").rglob("*.py"):
        if path.name in ("branding.py", "config.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.branding":
                names = {alias.name for alias in node.names}
                assert names <= {"ERROR_PREFIX"}, (
                    f"{path.relative_to(BACKEND)} 이 branding 에서 {sorted(names)} 를 가져온다"
                )


def test_모든_모델이_all_models_에_있다() -> None:
    """빠뜨리면 autogenerate 가 **기존 표를 지우는** 마이그레이션을 만든다.

    앱에서는 안 드러나고, 배포 뒤 마이그레이션을 돌릴 때만 터진다.
    """
    registered = (BACKEND / "app" / "all_models.py").read_text(encoding="utf-8")
    for path in MODULES.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            # Base 를 상속한 것만 ORM 모델이다.
            if any(isinstance(base, ast.Name) and base.id == "Base" for base in node.bases):
                assert node.name in registered, (
                    f"{node.name} 이 all_models.py 에 없습니다 ({path.relative_to(BACKEND)})"
                )


#: 코드에 박힌 오류 코드처럼 생긴 것. 접두사가 섞였는지 본다.
_CODE_LITERAL = re.compile(r"\"([A-Z]{2,6})-[A-Z]+-\d{4}\"")


def test_오류_코드_접두사가_하나다() -> None:
    """다른 프로젝트의 접두사가 섞이면 **로그 검색이 절반만 걸린다** — 그리고
    안 걸린 절반은 없는 것처럼 보인다.

    이 저장소는 접두사를 `app/branding.py` 하나에서 정하고 `errors.code()` 로
    조립한다. 문자열을 손으로 이으면 포크할 때 한 자리가 안 바뀌고, 그 사실은
    아무 데도 안 뜬다.
    """
    from app.branding import ERROR_PREFIX

    offenders: list[str] = []
    for path in (BACKEND / "app").rglob("*.py"):
        for found in _CODE_LITERAL.findall(path.read_text(encoding="utf-8")):
            if found != ERROR_PREFIX:
                offenders.append(f"{path.relative_to(BACKEND)}: {found}-")
    assert not offenders, "오류 코드 접두사가 섞였습니다: " + ", ".join(offenders)


def test_시험이_플랫폼_이름을_손으로_박지_않는다() -> None:
    """**포크한 이름으로도 시험이 돌아야 한다.**

    시험이 `"APP-"` 같은 값을 박아 두면, 포크해서 `ERROR_PREFIX` 를 바꾼 순간
    **올바른 코드인데도 시험이 깨진다** — 그리고 그때 사람은 자기가 뭘 잘못했나를
    먼저 찾는다. 실측으로 드러났다: 이 틀을 복사해 이름만 바꿨더니 시험 둘이
    빨갛게 됐고, 원인은 시험 쪽이었다.

    그래서 시험도 `branding` 에서 읽는다. 코드에 적용한 규칙("문자열을 손으로
    잇지 않는다")이 시험에도 그대로 적용된다.
    """
    from app.branding import DEFAULT_APP_NAME, DEFAULT_APP_SLUG, ERROR_PREFIX

    banned = {
        f'"{ERROR_PREFIX}-': "오류 코드 접두사",
        f'"{DEFAULT_APP_SLUG}': "APP_SLUG",
        f'"{DEFAULT_APP_NAME}"': "APP_NAME",
    }
    offenders: list[str] = []
    for path in (BACKEND / "tests").rglob("*.py"):
        # 이 시험 자신은 그 값들을 **비교하려고** 들고 있다.
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for needle, label in banned.items():
            if needle in text:
                offenders.append(f"{path.relative_to(BACKEND)}: {label}")
    assert not offenders, (
        "시험이 플랫폼 이름을 손으로 박았습니다(포크하면 깨집니다): " + ", ".join(offenders)
    )


def test_모듈_이름이_백엔드와_프론트에서_같다() -> None:
    """이름이 갈리면 **어느 화면이 어느 API 를 쓰는지** 추적이 사람의 기억에 걸린다.

    예외는 위 FRONTEND_MERGED 에 사유와 함께 적는다.
    """
    frontend = BACKEND.parent / "frontend" / "src" / "modules"
    if not frontend.exists():  # pragma: no cover - 백엔드만 받은 설치
        return

    backend_modules = {
        path.name
        for path in MODULES.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    }
    frontend_modules = {path.name for path in frontend.iterdir() if path.is_dir()}

    only_backend = backend_modules - frontend_modules - FRONTEND_MERGED
    assert not only_backend, f"프론트에 짝이 없는 모듈: {sorted(only_backend)}"
