#!/usr/bin/env bash
# 정제 도구 묶음 — 사용자 PC(Claude Desktop · Gemini CLI)에 옮기는 zip 하나.
#
#   ./deploy/build_pipeline_kit.sh            버전은 git describe 에서
#   ./deploy/build_pipeline_kit.sh v0.2.0     직접 지정
#
# 산출물: release/sp-pipeline-<버전>.zip (+ .sha256)
#   sp_*.py · AGENTS.md · README.md · requirements.txt · templates/ · core/
#   · guide/GUIDE.md(모델링 규약) · wheels/(인터넷 없이 까는 휠) · VERSION
#
# **휠을 여러 OS · 파이썬 버전 몫으로 받는다.** 사용자 PC 가 무엇인지 빌드하는 쪽은 모른다 —
# 한 벌만 넣으면 맞지 않는 PC 에서 설치가 「맞는 휠이 없다」 로 멈춘다. pip 가 설치할 때
# 자기에게 맞는 것만 고른다.
#   KIT_PLATFORMS="win_amd64"  KIT_PYTHONS="3.12"  으로 좁힐 수 있다.
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
PLATFORMS="${KIT_PLATFORMS:-win_amd64 manylinux2014_x86_64 macosx_11_0_arm64}"
PYTHONS="${KIT_PYTHONS:-3.11 3.12 3.13}"

echo "==> [1/3] 도구 · 안내"
rm -rf "$STAGE" "$OUT_DIR/$ZIP.zip" "$OUT_DIR/$ZIP.zip.sha256"
mkdir -p "$STAGE/guide" "$STAGE/wheels"
cp pipeline/sp_*.py pipeline/AGENTS.md pipeline/CLAUDE.md pipeline/GEMINI.md \
   pipeline/README.md pipeline/requirements.txt "$STAGE/"
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

echo "==> [2/3] 휠 (${PLATFORMS} × 파이썬 ${PYTHONS})"
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

echo "==> [3/3] 묶기"
( cd "$OUT_DIR" && python3 -m zipfile -c "$ZIP.zip" "$NAME" \
    && sha256sum "$ZIP.zip" > "$ZIP.zip.sha256" )
# **풀어 둔 폴더를 남기지 않는다.** release/ 에는 서버 번들 폴더도 있다 — 둘이 나란히 있으면
# 「번들 폴더 하나」 를 찾는 릴리스 검사가 이것을 집는다(v0.2.0 에서 실제로 막혔다).
rm -rf "$STAGE"

SIZE=$(du -h "$OUT_DIR/$ZIP.zip" | cut -f1)
echo
echo "[OK] $OUT_DIR/$ZIP.zip  ($SIZE)"
echo "     $OUT_DIR/$ZIP.zip.sha256"
echo "     사용자 PC 에서 풀고: python sp_setup.py --work-root <작업 폴더들> --server <플랫폼>"
