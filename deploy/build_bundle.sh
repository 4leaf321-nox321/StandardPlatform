#!/usr/bin/env bash
# 릴리스 번들 하나를 만든다 — 폐쇄망 서버에 이 파일 하나만 옮기면 된다.
#
#   ./deploy/build_bundle.sh            버전은 git describe 에서
#   ./deploy/build_bundle.sh v0.1.0     직접 지정
#
# 산출물: release/<slug>-<버전>.tar.gz
#   app.sif · deploy.sh · app.service.template · mcp.service.template · .env.example
#   · BUILD_INFO · README.md · mcp_server/ (서버 + 오프라인 설치용 휠)
#
# **배포 스크립트를 번들에 함께 담는다.** 서버가 릴리스만 받는 환경이어도 tar 하나로
# 그다음 배포가 돌아야 한다 — 빠뜨리면 첫 배포에 저장소를 클론하는 수밖에 없고,
# 폐쇄망에는 그 길이 없다.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v apptainer >/dev/null || { echo "오류: apptainer 가 없습니다"; exit 1; }
command -v npm       >/dev/null || { echo "오류: npm 이 없습니다 (프론트 빌드에 필요)"; exit 1; }

# ── 플랫폼 정보 — **branding.py 와 config.py 에서 읽는다** ────────────────────
#
# 여기서 다시 적으면 그때부터 두 벌이다. 배포 스크립트가 이 값으로 DB 이름·
# systemd 유닛 이름·설치 경로를 정하므로, 어긋나면 두 플랫폼이 서로를 덮어쓴다.
read -r APP_NAME APP_SLUG APP_PORT <<EOF
$(python3 - <<'PY'
import ast, pathlib, sys

def literals(path, names):
    tree = ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))
    out = {}
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id in names:
            try:
                out[target.id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out

branding = literals('backend/app/branding.py', {'APP_NAME', 'APP_SLUG'})
config = literals('backend/app/config.py', {'port'})
for key in ('APP_NAME', 'APP_SLUG'):
    if key not in branding:
        sys.exit(f'branding.py 에서 {key} 를 읽지 못했습니다.')
if 'port' not in config:
    sys.exit('config.py 에서 port 를 읽지 못했습니다.')
print(branding['APP_NAME'], branding['APP_SLUG'], config['port'])
PY
)
EOF
[[ -n "$APP_SLUG" ]] || { echo "오류: 플랫폼 정보를 읽지 못했습니다"; exit 1; }

VERSION="${1:-$(git describe --tags --always --dirty 2>/dev/null || date +%Y%m%d-%H%M%S)}"
RELEASE_NAME="${APP_SLUG}-${VERSION}"
OUT_DIR="release"
STAGE="${OUT_DIR}/${RELEASE_NAME}"

# MCP 서버 포트 — **앱 포트 +2.** 플랫폼마다 10씩 벌리는 규칙 안에서 운영(+0)·개발(+1)
# 다음 자리다. 따로 적으면 옆 플랫폼과 겹치는지 볼 자리가 하나 더 생긴다.
MCP_PORT=$((APP_PORT + 2))

echo "==> $APP_NAME ($APP_SLUG) · 포트 $APP_PORT (MCP $MCP_PORT) · 버전 $VERSION"

# ── 1. 프론트엔드 ─────────────────────────────────────────────────────────────
# **이게 없으면 배포된 앱이 모든 페이지에 API 의 JSON 404 를 돌려준다** —
# 라우팅 버그처럼 보이지만 아니다. 그래서 SIF 를 만들기 **전에** 여기서 만든다.
echo
echo "==> [1/4] 프론트엔드 빌드"
(cd frontend && npm ci && npm run build)
[[ -f frontend/dist/index.html ]] || { echo "오류: frontend/dist/index.html 이 없습니다"; exit 1; }

# 프론트는 API 절대주소를 굽지 않는다(항상 상대경로 /api). 굽는 방식이면 값이
# 빠졌을 때 **사용자 브라우저가 자기 PC 를 부른다** — 흔적이 남았는지 본다.
if grep -rlqs -e '127.0.0.1:80' -e 'localhost:80' frontend/dist/assets 2>/dev/null; then
    echo "오류: 번들에 개발 서버 주소가 남아 있습니다. API 주소를 굽지 않도록 고치세요."
    exit 1
fi
echo "    API 주소 검사 통과"

# ── 2. SIF ────────────────────────────────────────────────────────────────────
echo
echo "==> [2/4] Apptainer SIF 빌드 (처음에는 5~10분)"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# **이미지가 자기 버전을 들고 들어가게 한다.** 안 넣으면 `/api/health` 가
# `version: unknown` 을 돌려주고, 「지금 서버에 뭐가 깔렸나」 를 물을 자리가
# 없어진다 — 실측으로 그렇게 나왔다. .def 의 %files 가 이 파일을 집는다.
cat > deploy/BUILD_INFO.txt <<INFO
version=$VERSION
app_name=$APP_NAME
app_slug=$APP_SLUG
python=3.12
INFO
# CI 러너에는 subuid/subgid 매핑이 없어 비특권 빌드가 실패한다. 로컬 개발은
# sudo 없이 그대로 되게 두고, 필요한 곳에서만 켠다.
APPTAINER_BUILD=(apptainer build --force)
if [[ "${USE_SUDO_APPTAINER:-0}" == "1" ]]; then
    APPTAINER_BUILD=(sudo apptainer build --force)
fi
"${APPTAINER_BUILD[@]}" "$STAGE/app.sif" deploy/apptainer.def

# ── 3. 스크립트·문서·BUILD_INFO ───────────────────────────────────────────────
echo
echo "==> [3/4] 배포 스크립트와 문서 동봉"
cp deploy/deploy.sh                  "$STAGE/"
# **백업·복구도 함께 담는다.** README 가 번들 안에서 `./backup.sh` 를 시키는데
# 없으면, 그 사실은 **백업이 필요해진 날**에야 드러난다. 그리고 SSH 로만 닿는
# 서버에서는 「저장소에서 마저 가져오기」 가 성립하지 않는다.
cp deploy/backup.sh                  "$STAGE/"
cp deploy/restore.sh                 "$STAGE/"
cp deploy/app.service.template       "$STAGE/"
cp deploy/mcp.service.template       "$STAGE/"
cp deploy/.env.production.example    "$STAGE/.env.example"
cp deploy/README_OPERATOR.md         "$STAGE/README.md"
chmod +x "$STAGE"/*.sh

# ── 3b. MCP 서버 (별도 venv 로 운영 서버에서 돌아감) ──────────────────────────
# 백엔드 SIF 와 의존성이 충돌해 컨테이너에 못 넣는다. 소스 + 오프라인 설치용 휠을
# 동봉 → deploy.sh 가 운영 호스트에서 venv 만들고 `pip install --no-index` 로 설치.
echo "==> [3b/4] MCP 서버 + 오프라인 휠 동봉"
mkdir -p "$STAGE/mcp_server"
cp mcp_server/server.py        "$STAGE/mcp_server/"
cp mcp_server/requirements.txt "$STAGE/mcp_server/"
[[ -f mcp_server/README.md ]] && cp mcp_server/README.md "$STAGE/mcp_server/" || true
# 스킬 스텁(사용자가 ~/.claude/skills 로 설치) — 번들에 동봉
[[ -d mcp_server/skill ]] && cp -r mcp_server/skill "$STAGE/mcp_server/skill" || true
# 사용 가이드(get_guide 가 읽는 본문) — **서버가 쥔다.** 빠지면 get_guide 가
# 빈 응답을 주고 AI 는 도구 설명만으로 헤맨다.
[[ -d mcp_server/guide ]] && cp -r mcp_server/guide "$STAGE/mcp_server/guide" || true
# 빌드 머신(인터넷 O)에서 휠을 받아 둔다. 운영 호스트가 폐쇄망이어도 설치되게.
# 빌드/운영 아키텍처가 같다고 가정(둘 다 linux x86_64). 다르면 --platform 지정 필요.
if python3 -m pip download --only-binary=:all: \
        -r mcp_server/requirements.txt -d "$STAGE/mcp_server/wheels" >/dev/null 2>&1; then
    echo "    휠 $(ls "$STAGE/mcp_server/wheels" | wc -l)개 동봉"
else
    echo "    ⚠ pip download 실패 — 휠 미동봉. 운영 호스트에 인터넷/사내 PyPI 있어야 MCP 설치됨."
fi

# **번들이 자기가 무슨 플랫폼인지 말한다.** deploy.sh 가 여기서 DB 이름·포트·
# 유닛 이름을 읽으므로, 배포 스크립트에는 제품 이름이 박혀 있지 않다.
cat > "$STAGE/BUILD_INFO" <<INFO
app_name=$APP_NAME
app_slug=$APP_SLUG
port=$APP_PORT
mcp_port=$MCP_PORT
version=$VERSION
python=3.12
built_at=$(date -Is)
INFO

# ── 4. tar ────────────────────────────────────────────────────────────────────
echo
echo "==> [4/4] 묶기"
tar czf "${OUT_DIR}/${RELEASE_NAME}.tar.gz" -C "$OUT_DIR" "$RELEASE_NAME"

# **체크섬을 함께 낸다.** 이 파일은 기계를 두 번 건넌다(빌드 -> 내려받는 PC ->
# scp -> 서버). 중간에 잘리거나 절반만 받아진 tar 는 **푸는 순간**에야 드러나고,
# 그때는 배포하려고 서버에 붙어 있는 자리다.
( cd "$OUT_DIR" && sha256sum "${RELEASE_NAME}.tar.gz" > "${RELEASE_NAME}.tar.gz.sha256" )

SIZE=$(du -h "${OUT_DIR}/${RELEASE_NAME}.tar.gz" | cut -f1)
echo
echo "[OK] ${OUT_DIR}/${RELEASE_NAME}.tar.gz  (${SIZE})"
echo "     ${OUT_DIR}/${RELEASE_NAME}.tar.gz.sha256"
echo "     이 파일 하나를 운영 서버로 옮기면 됩니다."
