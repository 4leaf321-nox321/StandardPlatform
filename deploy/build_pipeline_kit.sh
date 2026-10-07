#!/usr/bin/env bash
# 정제 도구 묶음 — 사용자 PC(Claude Desktop · Gemini CLI)에 옮기는 zip 하나.
#
#   ./deploy/build_pipeline_kit.sh            버전은 git describe 에서
#   ./deploy/build_pipeline_kit.sh v0.2.0     직접 지정
#
# 산출물: release/sp-pipeline-<버전>.zip (+ .sha256)
#   sp_*.py · install.cmd · check.cmd · AGENTS.md · README.md · requirements.txt · templates/
#   · core/ · guide/GUIDE.md(모델링 규약) · python/(Windows 파이썬 — 부품을 깐 채로)
#   · wheels/(macOS · 리눅스가 인터넷 없이 까는 휠) · VERSION
#
# **Windows 는 파이썬을 넣어 보낸다**(`python/`). python.org 의 내장용(embeddable) 판에 부품을
# 미리 깐다 — PC 에 파이썬이 없어도, 어느 판이 있어도 돈다. 설치는 venv 도 pip 도 안 돈다.
# 전에는 PC 의 파이썬을 썼고, 설치가 거기서 막혔다 — python.org 의 최신판(3.14)은 휠이 없어
# 거절, PATH 체크를 빠뜨림, Store 의 가짜 python, pip 가 cp949 로 읽다 죽음.
#   전역 설치가 아니다 — 레지스트리 · PATH 를 안 건드리고, 키트 폴더를 지우면 끝이다.
#
# **macOS · 리눅스는 휠을 여러 파이썬 버전 몫으로 받는다** — 그 PC 의 파이썬으로 venv 를 만든다.
#   KIT_PLATFORMS="manylinux2014_x86_64"  KIT_PYTHONS="3.12"  으로 좁힐 수 있다.
#   내장용 파이썬은 한 번 받아 KIT_CACHE(기본 ~/.cache/sp-pipeline-kit)에 둔다.
#
# **저장소에 없는 사내 파일**(허브 정의 · 대응 파일 — `docs/사내/plm/`)은 KIT_PRIVATE_DIR 로 함께
# 담는다. GitHub 릴리스의 zip 에는 없고, 이 PC 에서 만든 zip 에만 든다 — 그 zip 은 사내로 들고
# 들어가는 것이지 올리는 것이 아니다.
#   KIT_PRIVATE_DIR=docs/사내 ./deploy/build_pipeline_kit.sh v0.3.1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="${1:-$(git describe --tags --always --dirty 2>/dev/null || echo dev)}"
NAME="sp-pipeline-${VERSION}"
ZIP="$NAME"
OUT_DIR="${OUT_DIR:-release}"
STAGE="$OUT_DIR/$NAME"
PLATFORMS="${KIT_PLATFORMS:-manylinux2014_x86_64 macosx_11_0_arm64}"
PYTHONS="${KIT_PYTHONS:-3.11 3.12 3.13}"
# Windows 에 넣는 파이썬 — **판을 올릴 때 두 값을 같이 바꾼다.** 해시는 python.org 의 Sigstore
# 서명(`<zip>.sigstore`)을 릴리스 관리자 신원으로 검증한 뒤 적는다(3.14 는 hugo@python.org):
#   python -m sigstore verify identity --bundle python-<판>-embed-amd64.zip.sigstore \
#     --cert-identity hugo@python.org --cert-oidc-issuer https://github.com/login/oauth \
#     python-<판>-embed-amd64.zip
EMBED_PYTHON="3.14.8"
EMBED_SHA256="a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310"
CACHE="${KIT_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/sp-pipeline-kit}"

echo "==> [1/4] 도구 · 안내"
rm -rf "$STAGE" "$OUT_DIR/$ZIP.zip" "$OUT_DIR/$ZIP.zip.sha256"
mkdir -p "$STAGE/guide" "$STAGE/wheels"
# `install.cmd` — 사용자가 **더블클릭**하는 설치(Windows). 화면 「내 정보」 의 「이 PC 에 등록」 이
# 복사한 등록 정보를 클립보드에서 읽는다. CRLF 다(.gitattributes).
# `check.cmd` — 설치했는데 Claude 에 안 뜰 때 더블클릭하는 점검.
cp pipeline/sp_*.py pipeline/AGENTS.md pipeline/CLAUDE.md pipeline/GEMINI.md \
   pipeline/README.md pipeline/requirements.txt pipeline/install.cmd pipeline/check.cmd "$STAGE/"
cp -r pipeline/templates pipeline/core "$STAGE/"
# 모델링 규약의 정본은 플랫폼 MCP 의 가이드다 — **복사해 넣되 고치지 않는다.**
# sp_mcp 의 pipeline_guide("modeling") 가 이것을 읽는다.
cp mcp_server/guide/GUIDE.md "$STAGE/guide/"
echo "$VERSION" > "$STAGE/VERSION"
if [[ -n "${KIT_PRIVATE_DIR:-}" ]]; then
    [[ -d "$KIT_PRIVATE_DIR" ]] || { echo "오류: KIT_PRIVATE_DIR 가 없습니다: $KIT_PRIVATE_DIR"; exit 1; }
    mkdir -p "$STAGE/사내"
    cp -r "$KIT_PRIVATE_DIR"/. "$STAGE/사내/"
    ZIP="${NAME}-private"
    echo "    사내 파일 동봉: $KIT_PRIVATE_DIR → 사내/ (파일 $(find "$STAGE/사내" -type f | wc -l)개) — 이 zip 은 올리지 않는다"
fi

echo "==> [2/4] Windows 파이썬 (내장용 ${EMBED_PYTHON} + 부품)"
EMBED_ZIP="$CACHE/python-${EMBED_PYTHON}-embed-amd64.zip"
mkdir -p "$CACHE"
if [[ ! -f "$EMBED_ZIP" ]]; then
    curl -fsSL --retry 3 -o "$EMBED_ZIP.part" \
        "https://www.python.org/ftp/python/${EMBED_PYTHON}/python-${EMBED_PYTHON}-embed-amd64.zip"
    mv "$EMBED_ZIP.part" "$EMBED_ZIP"
fi
echo "${EMBED_SHA256}  ${EMBED_ZIP}" | sha256sum -c --quiet - \
    || { echo "오류: 내장용 파이썬의 해시가 다릅니다 — 지우고 다시 받으세요: $EMBED_ZIP"; exit 1; }
python3 -m zipfile -e "$EMBED_ZIP" "$STAGE/python"
EMBED_ABI="${EMBED_PYTHON%.*}"          # 3.14.8 → 3.14
EMBED_TAG="${EMBED_ABI/./}"             # → 314
PTH="$STAGE/python/python${EMBED_TAG}._pth"
[[ -f "$PTH" ]] || { echo "오류: $PTH 가 없습니다 — 내장용 판의 모양이 바뀌었다"; exit 1; }
# 길을 이 파일이 **통째로** 정한다(내장용 판은 환경 변수의 길도, 스크립트의 자리도 안 본다).
#   표준 라이브러리 · 부품(Lib\site-packages) · 키트 폴더(`..` — sp_mcp 가 옆의 sp_pipeline 을
#   찾는 자리) · `import site`(pywin32 의 .pth 를 읽는다).
# PYTHONUTF8 은 그대로 듣는다 — Windows 에서 확인했다(없으면 cp949).
printf 'python%s.zip\r\n.\r\nLib\\site-packages\r\n..\r\nimport site\r\n' "$EMBED_TAG" > "$PTH"
python3 -m pip install --quiet --disable-pip-version-check --only-binary=:all: \
    --platform win_amd64 --python-version "$EMBED_ABI" --implementation cp \
    --target "$STAGE/python/Lib/site-packages" -r pipeline/requirements.txt pywin32 colorama
# 리눅스에서 깔아 생긴 실행 스크립트 — 리눅스용이라 Windows 에서는 쓸 데가 없다.
rm -rf "$STAGE/python/Lib/site-packages/bin"
echo "    $(du -sh "$STAGE/python" | cut -f1) — python/python.exe"

echo "==> [3/4] 휠 (${PLATFORMS} × 파이썬 ${PYTHONS})"
for target in $PLATFORMS; do
    extra=()
    # **pip download 는 환경 표시(marker)를 빌드하는 기계 기준으로 푼다.** 리눅스에서 받으면
    # Windows 에서만 필요한 것(mcp → pywin32, click → colorama)이 빠지고, 그 사실은 사용자
    # PC 의 오프라인 설치가 멈출 때에야 드러난다. 그래서 따로 적어 받는다.
    case "$target" in win*) extra=(pywin32 colorama) ;; esac
    for py in $PYTHONS; do
        python3 -m pip download --quiet --disable-pip-version-check --only-binary=:all: \
            --platform "$target" --python-version "$py" --implementation cp \
            -d "$STAGE/wheels" -r pipeline/requirements.txt "${extra[@]}"
    done
done
echo "    휠 $(find "$STAGE/wheels" -name '*.whl' | wc -l)개"

echo "==> [4/4] 묶기"
# **압축해서** 묶는다 — `python -m zipfile -c` 는 압축 없이 담는다(휠만 있을 때는 이미 압축된
# 것이라 몰랐다. 파이썬 · 부품은 그대로 담으면 두 배가 넘는다).
( cd "$OUT_DIR" && python3 - "$ZIP.zip" "$NAME" <<'PY'
import sys, zipfile
from pathlib import Path

target, root = sys.argv[1], Path(sys.argv[2])
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as kit:
    for path in sorted(root.rglob("*")):
        kit.write(path, path.as_posix())
PY
  sha256sum "$ZIP.zip" > "$ZIP.zip.sha256" )
# **풀어 둔 폴더를 남기지 않는다.** release/ 에는 서버 번들 폴더도 있다 — 둘이 나란히 있으면
# 「번들 폴더 하나」 를 찾는 릴리스 검사가 이것을 집는다(v0.2.0 에서 실제로 막혔다).
rm -rf "$STAGE"

SIZE=$(du -h "$OUT_DIR/$ZIP.zip" | cut -f1)
echo
echo "[OK] $OUT_DIR/$ZIP.zip  ($SIZE)"
echo "     $OUT_DIR/$ZIP.zip.sha256"
echo "     사용자 PC 에서 풀고: 플랫폼 화면 「내 정보」 의 「이 PC 에 등록 정보 복사」 → install.cmd 더블클릭"
