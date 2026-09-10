"""배포 자산이 지키는 것들.

여기서 잡는 것은 전부 **돌려 보기 전에는 안 보인다.** 그리고 돌려 보는 자리는
대개 운영 서버다.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"


def _scripts() -> list[Path]:
    return sorted(DEPLOY.glob("*.sh")) if DEPLOY.exists() else []


def test_셸_스크립트는_실행_가능하고_shebang_이_있다() -> None:
    """**tar 는 실행 비트를 보존한다.** 번들을 푼 서버에서 `./deploy.sh` 가
    「Permission denied」 로 막히면, 그것은 배포를 시작하기도 전의 벽이다.
    """
    for path in _scripts():
        first = path.read_bytes().split(b"\n", 1)[0]
        assert first.startswith(b"#!"), f"{path.name} 에 shebang 이 없습니다"
        assert b"bash" in first, f"{path.name} 은 bash 로 실행돼야 합니다: {first!r}"
        if os.name != "nt":  # pragma: no cover - Windows 체크아웃에서는 못 본다
            mode = path.stat().st_mode
            assert mode & stat.S_IXUSR, f"{path.name} 에 실행 비트가 없습니다"


def test_셸_스크립트는_엄격_모드로_돈다() -> None:
    """`set -euo pipefail` 이 없으면 **중간 단계가 실패해도 다음 줄이 그냥 돈다.**

    배포에서 그것은 「마이그레이션은 실패했는데 서비스는 새 코드로 재시작된」
    상태를 만들고, 화면은 500 을 내면서 원인을 말하지 않는다.
    """
    for path in _scripts():
        head = path.read_text(encoding="utf-8")[:2000]
        assert "set -euo pipefail" in head, f"{path.name} 에 set -euo pipefail 이 없습니다"


def test_배포_스크립트가_번들에_함께_담긴다() -> None:
    """**서버가 tar 하나만 받는 환경이어도 그다음 배포가 돌아야 한다.**

    빠뜨리면 첫 배포에 저장소를 클론하는 수밖에 없고, 폐쇄망에는 그 길이 없다.
    """
    builder = DEPLOY / "build_bundle.sh"
    if not builder.exists():  # pragma: no cover - 배포 자산을 뺀 설치
        return
    text = builder.read_text(encoding="utf-8")
    # 백업·복구가 여기 있는 이유: README 가 번들 안에서 `./backup.sh` 를 시킨다.
    # 없으면 그 사실은 **백업이 필요해진 날**에야 드러나고, SSH 로만 닿는 서버에서
    # 「저장소에서 마저 가져오기」 는 성립하지 않는다.
    for name in (
        "deploy.sh",
        "backup.sh",
        "restore.sh",
        "app.service.template",
        ".env.production.example",
    ):
        assert name in text, f"{name} 이 번들에 안 담깁니다 (build_bundle.sh)"


def test_번들이_약속한_파일이_실제로_담긴다() -> None:
    """**README 가 번들 안에서 시키는 명령은 번들 안에 있어야 한다.**

    `README_OPERATOR.md` 는 번들에 함께 들어가 「`ls` 하면 이것들이 보인다」 고
    적는다. 그 목록과 `build_bundle.sh` 가 실제로 담는 것이 어긋나면, 운영자는
    **없는 스크립트를 치게 된다** — 그리고 그 자리는 대개 서버에 SSH 로 붙어 있는
    자리다.
    """
    builder = DEPLOY / "build_bundle.sh"
    readme = DEPLOY / "README_OPERATOR.md"
    if not builder.exists() or not readme.exists():  # pragma: no cover
        return

    built = builder.read_text(encoding="utf-8")
    promised = readme.read_text(encoding="utf-8").split("---", 1)[0]

    for name in ("deploy.sh", "backup.sh", "restore.sh", "app.sif", "BUILD_INFO"):
        if name in promised:
            assert name in built or name == "app.sif", (
                f"README 는 번들에 {name} 이 있다고 적는데 build_bundle.sh 는 안 담습니다"
            )


def test_셸_스크립트에_CRLF_가_없다() -> None:
    """**줄바꿈 하나가 배포를 시작도 못 하게 만든다.**

    Windows 의 Git 은 `core.autocrlf=true` 가 기본이라, 그 기계에서 클론하면 셸
    스크립트가 CRLF 로 체크아웃된다. 그 파일이 리눅스 서버에 닿으면:

        -bash: ./deploy.sh: /usr/bin/env bash^M: bad interpreter: No such file or directory

    파일은 있고, 실행 비트도 있고, 눈으로 보면 멀쩡하다. `.gitattributes` 가
    막지만 **막혔는지 확인하는 자리**가 따로 있어야 한다 — 설정은 언젠가 빠진다.
    """
    for path in _scripts():
        assert b"\r\n" not in path.read_bytes(), (
            f"{path.name} 에 CRLF 가 있습니다. .gitattributes 를 확인하고 "
            "`dos2unix` 또는 재체크아웃 하세요 — 서버에서 bad interpreter 로 죽습니다"
        )


def test_줄바꿈을_저장소가_정한다() -> None:
    """`.gitattributes` 가 없으면 줄바꿈이 **클론한 사람의 설정**에서 정해진다.

    그러면 같은 커밋이 기계마다 다른 파일로 체크아웃되고, 그 차이는 리뷰에
    안 보인다.
    """
    attrs = REPO / ".gitattributes"
    assert attrs.exists(), ".gitattributes 가 없습니다 (Windows 클론에서 CRLF 가 섞입니다)"
    text = attrs.read_text(encoding="utf-8")
    assert "eol=lf" in text, ".gitattributes 가 LF 를 강제하지 않습니다"
    assert "*.sh" in text, ".gitattributes 가 셸 스크립트를 짚지 않습니다"


def test_태그를_밀면_번들이_나온다() -> None:
    """**번들을 만들 자리가 저장소 안에 있어야 한다.**

    `build_bundle.sh` 는 리눅스 · apptainer · npm 을 요구한다. 저장소를 받는
    기계가 Windows 라면 그 기계에서는 만들 수 없다 — Apptainer 는 Windows
    네이티브 빌드가 없다. 그 자리가 비어 있으면 **배포 경로가 통째로 끊긴다.**
    """
    release = REPO / ".github" / "workflows" / "release.yml"
    if not (REPO / ".github").exists():  # pragma: no cover - CI 자산을 뺀 포크
        return
    assert release.exists(), "태그 릴리스 워크플로가 없습니다 (.github/workflows/release.yml)"

    text = release.read_text(encoding="utf-8")
    assert "build_bundle.sh" in text, "릴리스가 번들을 안 만듭니다"
    assert "gh release create" in text, "릴리스가 산출물을 안 올립니다"
    # **검증을 통과해야 나간다.** 안 그러면 아무도 안 돌려 본 코드가 tar 로 나가고,
    # 깨졌다는 사실은 운영 서버에서 드러난다.
    assert "needs: verify" in text, "릴리스가 검증을 안 기다립니다"
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "workflow_call" in ci, "ci.yml 을 릴리스에서 불러 쓸 수 없습니다"


def test_번들이_자기가_무슨_플랫폼인지_말한다() -> None:
    """`deploy.sh` 가 DB 이름·포트·유닛 이름을 BUILD_INFO 에서 읽는다.

    배포 스크립트에 제품 이름을 박아 두면 포크할 때 바꿀 자리가 하나 더 늘고,
    **안 바꾸면 두 플랫폼이 같은 DB 와 같은 systemd 유닛을 쓴다** — 나중 배포가
    앞의 것을 그대로 지우고, 그 사실은 아무 데도 안 적힌다.
    """
    builder = DEPLOY / "build_bundle.sh"
    installer = DEPLOY / "deploy.sh"
    if not builder.exists() or not installer.exists():  # pragma: no cover
        return

    written = builder.read_text(encoding="utf-8")
    for key in ("app_name=", "app_slug=", "port=", "version="):
        assert key in written, f"build_bundle.sh 가 BUILD_INFO 에 {key} 를 안 씁니다"

    read = installer.read_text(encoding="utf-8")
    for key in ("app_name", "app_slug", "port"):
        assert f"bundle {key}" in read, f"deploy.sh 가 {key} 를 안 읽습니다"


def test_배포_자산에_플랫폼_이름을_박지_않는다() -> None:
    """포크할 때 바꿀 자리를 늘리지 않는다.

    문서(`README_OPERATOR.md`)는 예외다 — 거기서는 `<slug>` 자리표시자를 쓴다.
    검사하는 것은 **동작에 쓰이는 스크립트와 유닛 템플릿**이다.
    """
    from app.branding import APP_NAME, APP_SLUG

    targets = [*_scripts(), *DEPLOY.glob("*.template")]
    offenders = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if APP_SLUG in text or APP_NAME in text:
            offenders.append(path.name)
    assert not offenders, (
        "배포 자산에 플랫폼 이름이 박혔습니다(포크하면 두 플랫폼이 겹칩니다): "
        + ", ".join(offenders)
    )


def test_bind_mount_대상이_이미지에_있다() -> None:
    """**마운트 대상은 이미지 안에 미리 있어야 한다.**

    없으면 마운트가 조용히 엉뚱한 데 붙거나 실패하고, 그 사실은 **파일을 쓰는
    순간**에야 드러난다 — 그리고 그 오류는 그 기능을 쓰는 사람에게만 보인다.
    """
    definition = DEPLOY / "apptainer.def"
    unit = DEPLOY / "app.service.template"
    if not definition.exists() or not unit.exists():  # pragma: no cover
        return

    def_text = definition.read_text(encoding="utf-8")
    unit_text = unit.read_text(encoding="utf-8")

    # 유닛이 --bind 로 넣는 컨테이너 쪽 경로를 모은다.
    wanted = set()
    for line in unit_text.splitlines():
        if "--bind" not in line:
            continue
        spec = line.split("--bind", 1)[1].strip().rstrip("\\").strip()
        parts = spec.split(":")
        if len(parts) >= 2:
            wanted.add(parts[1])

    for target in sorted(wanted):
        if target.endswith(".env"):
            # 파일 마운트는 이미지 안의 파일을 덮으므로 mkdir 대상이 아니다.
            continue
        assert "mkdir -p" in def_text and target in def_text, (
            f"{target} 이 apptainer.def 의 %post 에서 안 만들어집니다"
        )
