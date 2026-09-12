#!/usr/bin/env bash
# 배포 — 준비 · 설치 · 갱신 · 초기화 · 상태.
#
#   sudo ./deploy.sh prepare   최초 1회: apt 패키지, postgres, DB 역할·DB 생성
#   sudo ./deploy.sh install   SIF 배치 + .env 생성 + systemd + 마이그레이션 + 시드 + 기동
#   sudo ./deploy.sh update    SIF 교체 + 마이그레이션 + 재시작 (자료 그대로)
#   sudo ./deploy.sh reset     DB 통째로 초기화 (파괴적)
#   sudo ./deploy.sh status    서비스 상태 + /api/health
#   sudo ./deploy.sh           자동: 설치된 흔적이 없으면 install, 있으면 update
#
# **번들이 자기가 무슨 플랫폼인지 말한다**(BUILD_INFO). 앱 이름·slug·포트가 거기
# 있으므로 이 스크립트에는 제품 이름이 박혀 있지 않다 — 박아 두면 포크할 때 바꿀
# 자리가 하나 더 늘고, 안 바꾸면 두 플랫폼이 **같은 DB 와 같은 유닛 이름**을 쓴다.
#
# 어디서 실행해도 된다. 짝이 되는 파일(app.sif · .env.example · 유닛 템플릿)을
# 자기 디렉터리에서 찾는다.

set -euo pipefail

err()  { echo "오류: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "경고: $*" >&2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ───────────────────────── 번들이 알려 주는 것 ─────────────────────────
[[ -f "$HERE/BUILD_INFO" ]] || err "BUILD_INFO 가 없습니다 ($HERE). 릴리스 번들 안에서 실행하세요."
bundle() { sed -n "s|^$1=||p" "$HERE/BUILD_INFO" | tail -n1; }

APP_NAME="$(bundle app_name)"
APP_SLUG="$(bundle app_slug)"
APP_PORT_DEFAULT="$(bundle port)"
VERSION="$(bundle version)"
[[ -n "$APP_SLUG" && -n "$APP_NAME" ]] || err "BUILD_INFO 에 app_name/app_slug 가 없습니다."

# ───────────────────────── 사전 확인 ─────────────────────────
[[ $EUID -eq 0 ]] || err "root 로 실행하세요 (sudo)"

OPERATOR="${OPERATOR:-${SUDO_USER:-}}"
[[ -n "$OPERATOR" && "$OPERATOR" != "root" ]] \
    || err "운영 계정을 알 수 없습니다. 일반 사용자로 sudo 하거나 OPERATOR=<이름> 을 주세요."
id "$OPERATOR" >/dev/null 2>&1 || err "그런 계정이 없습니다: $OPERATOR"

# **설치 경로·DB·유닛 이름이 전부 slug 에서 나온다.** 한 서버에 여러 플랫폼을
# 얹을 때 이것이 겹치면 서로를 덮어쓴다.
INSTALL_DIR="${INSTALL_DIR:-/home/$OPERATOR/apps/$APP_SLUG}"
DB_NAME="${DB_NAME:-$APP_SLUG}"
DB_USER="${DB_USER:-$APP_SLUG}"
APP_PORT="${APP_PORT:-$APP_PORT_DEFAULT}"
SERVICE_NAME="$APP_SLUG"
SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

as_op() { sudo -u "$OPERATOR" "$@"; }

# 데이터 소스 동기화 타이머 — 화면에서 간격을 정한 소스를 몇 분마다 돌린다. 앱과 같은 SIF.
SYNC_SERVICE_NAME="${APP_SLUG}-sync"
SYNC_SERVICE_UNIT="/etc/systemd/system/${SYNC_SERVICE_NAME}.service"
SYNC_TIMER_UNIT="/etc/systemd/system/${SYNC_SERVICE_NAME}.timer"
SYNC_ENABLED="${SYNC_ENABLED:-1}"                      # 0 으로 두면 타이머 안 설치

# ───────────────────────── MCP 서버 (Claude 연동, 선택) ─────────────────────────
# 별도 venv + 별도 systemd 유닛. 백엔드 SIF 와 의존성이 충돌해 컨테이너에 못 넣는다.
MCP_SERVICE_NAME="${APP_SLUG}-mcp"
MCP_SERVICE_UNIT="/etc/systemd/system/${MCP_SERVICE_NAME}.service"
# 설치된 systemd 유닛에서 Environment=KEY=VALUE 값을 읽는다(없으면 빈 문자열).
# → 한 번 배포한 MCP 설정을 다음 배포가 자동으로 기억하게 하는 장치.
unit_env() {  # $1=key
    [[ -f "$MCP_SERVICE_UNIT" ]] || return 0
    sed -n "s|^Environment=$1=||p" "$MCP_SERVICE_UNIT" | tail -n1
}

MCP_ENABLED="${MCP_ENABLED:-1}"                        # 0 으로 두면 MCP 전체 건너뜀
# 우선순위: 명시한 env > 설치된 유닛에 저장된 값 > 기본값(번들의 BUILD_INFO).
# → 최초 한 번 'MCP_HOST=0.0.0.0 ./deploy.sh' 하면, 이후 './deploy.sh update' 가
#   매번 다시 지정하지 않아도 같은 값을 유지한다(되돌리려면 그때만 env 로 덮어쓰기).
MCP_PORT_DEFAULT="$(bundle mcp_port)"; MCP_PORT_DEFAULT="${MCP_PORT_DEFAULT:-$((APP_PORT + 2))}"
MCP_HOST="${MCP_HOST:-$(unit_env MCP_HOST)}";  MCP_HOST="${MCP_HOST:-127.0.0.1}"
MCP_PORT="${MCP_PORT:-$(unit_env MCP_PORT)}";  MCP_PORT="${MCP_PORT:-$MCP_PORT_DEFAULT}"
MCP_API_BASE="${MCP_API_BASE:-$(unit_env PLATFORM_API_BASE)}"
MCP_API_BASE="${MCP_API_BASE:-http://127.0.0.1:$APP_PORT}"
# DNS rebinding 보호 허용 Host(쉼표구분). 비우면 server.py 가 비-localhost 바인딩 시
# 보호를 끈다(사내망). 외부 노출 시 도메인/IP 지정 권장. 이 값도 위처럼 기억된다.
MCP_ALLOWED_HOSTS="${MCP_ALLOWED_HOSTS:-$(unit_env MCP_ALLOWED_HOSTS)}"

# ───────────────────────── 조각들 ─────────────────────────
ensure_dirs() {
    info "설치 폴더 준비: $INSTALL_DIR"
    # **운영 데이터는 SIF 밖이다.** 이미지가 통째로 교체돼도 살아남아야 한다.
    as_op mkdir -p "$INSTALL_DIR"/{filestore,logs}
}

generate_env_if_missing() {
    # **있으면 손대지 않는다.** 여기에 JWT 비밀키가 있어서, 덮으면 전원이 다시
    # 로그인한다. 그리고 그것은 배포가 할 일이 아니다.
    [[ -f "$INSTALL_DIR/.env" ]] && { info ".env 가 이미 있습니다 — 그대로 둡니다."; return 0; }
    [[ -f "$HERE/.env.example" ]] || err ".env.example 이 스크립트 옆에 없습니다 ($HERE)"

    local pw secret
    pw="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

    info ".env 생성 (난수 비밀키 + DB 비밀번호)"
    # **DB 비밀번호를 여기서 돌린다.** 그래야 .env 와 Postgres 역할이 반드시
    # 같은 값을 갖는다 — 따로 두면 언제 어긋났는지 알 방법이 없다.
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
ALTER USER "$DB_USER" WITH PASSWORD '$pw';
SQL

    sed -e "s|REPLACE_WITH_RANDOM_48_CHAR_SECRET|$secret|" \
        -e "s|REPLACE_DB_USER|$DB_USER|" \
        -e "s|REPLACE_DB_PASSWORD|$pw|" \
        -e "s|REPLACE_DB_NAME|$DB_NAME|" \
        -e "s|^PORT=.*|PORT=$APP_PORT|" \
        "$HERE/.env.example" > "$INSTALL_DIR/.env"
    chown "$OPERATOR:$OPERATOR" "$INSTALL_DIR/.env"
    # 비밀키가 들어 있다. 남이 읽을 이유가 없다.
    chmod 600 "$INSTALL_DIR/.env"
    warn "$INSTALL_DIR/.env 를 만들었습니다 — 공개 전에 CORS·백업 경로를 확인하세요."
}

place_sif() {
    [[ -f "$HERE/app.sif" ]] || err "app.sif 가 $HERE 에 없습니다 (릴리스 번들에서 실행하세요)"
    info "app.sif 배치 → $INSTALL_DIR/app.sif"
    install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/app.sif" "$INSTALL_DIR/app.sif"
}

# 컨테이너 안에서 한 번 실행. **웹 서비스와 같은 SIF·같은 .env 를 쓴다** —
# 다른 파이썬으로 마이그레이션을 돌리면 그 둘이 다른 스키마를 볼 수 있다.
in_container() {
    as_op apptainer exec \
        --bind "$INSTALL_DIR/.env:/opt/app/backend/.env:ro" \
        --bind "$INSTALL_DIR/filestore:/data/filestore" \
        --bind "$INSTALL_DIR/logs:/data/logs" \
        "$INSTALL_DIR/app.sif" "$@"
}

run_migrations() {
    info "마이그레이션 적용"
    in_container sh -c 'cd /opt/app/backend && /opt/app/venv/bin/python -m alembic upgrade head'
}

run_seed() {
    # 뿌리 부서와 관리자 계정. **이것 없이는 아무도 로그인할 수 없다** —
    # 가입은 승인이 필요한데 승인할 사람이 없기 때문이다. 멱등하다.
    info "설치 시드 (이미 있으면 건드리지 않음)"
    in_container sh -c 'cd /opt/app/backend && /opt/app/venv/bin/python scripts/seed_install.py'
}

render_service_unit() {
    [[ -f "$HERE/app.service.template" ]] || err "app.service.template 이 $HERE 에 없습니다"
    info "systemd 유닛 렌더 → $SERVICE_UNIT"
    sed -e "s|@@USER@@|$OPERATOR|g" \
        -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" \
        -e "s|@@APP_NAME@@|$APP_NAME|g" \
        "$HERE/app.service.template" > "$SERVICE_UNIT"
    chmod 644 "$SERVICE_UNIT"
    systemctl daemon-reload
}

# ── MCP 서버 (선택) — 별도 venv + systemd. 실패는 전부 비치명적(백엔드 배포 무관). ──
render_mcp_service_unit() {
    [[ -f "$HERE/mcp.service.template" ]] \
        || { warn "mcp.service.template 없음 — MCP 유닛 건너뜀"; return 1; }
    info "MCP systemd 유닛 렌더 → $MCP_SERVICE_UNIT"
    sed -e "s|@@USER@@|$OPERATOR|g" \
        -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" \
        -e "s|@@APP_NAME@@|$APP_NAME|g" \
        -e "s|@@APP_SLUG@@|$APP_SLUG|g" \
        -e "s|@@API_BASE@@|$MCP_API_BASE|g" \
        -e "s|@@MCP_HOST@@|$MCP_HOST|g" \
        -e "s|@@MCP_PORT@@|$MCP_PORT|g" \
        -e "s|@@MCP_ALLOWED_HOSTS@@|$MCP_ALLOWED_HOSTS|g" \
        "$HERE/mcp.service.template" > "$MCP_SERVICE_UNIT"
    chmod 644 "$MCP_SERVICE_UNIT"
    systemctl daemon-reload
}

setup_mcp() {
    [[ "$MCP_ENABLED" == "1" ]] || { info "MCP 비활성(MCP_ENABLED=0) — 건너뜀"; return 0; }
    [[ -d "$HERE/mcp_server" ]] || { warn "번들에 mcp_server/ 없음 — MCP 건너뜀"; return 0; }

    info "MCP 서버 설치 (별도 venv + systemd)"
    local md="$INSTALL_DIR/mcp_server"

    # 1) 소스 배치
    as_op mkdir -p "$md"
    install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/mcp_server/server.py" "$md/server.py"
    # 사용 가이드 — get_guide 가 읽는 본문. server.py 옆 guide/ 에 있어야 한다.
    # 이게 빠지면 get_guide 가 "가이드를 읽을 수 없습니다" 를 돌려주고, AI 는
    # 도구 설명만으로 일하게 된다(치명적이진 않지만 품질이 떨어진다).
    if [[ -d "$HERE/mcp_server/guide" ]]; then
        as_op mkdir -p "$md/guide"
        as_op cp -r "$HERE/mcp_server/guide/." "$md/guide/"
    fi
    install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/mcp_server/requirements.txt" "$md/requirements.txt"
    [[ -f "$HERE/mcp_server/README.md" ]] \
        && install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/mcp_server/README.md" "$md/README.md" || true

    # 2) venv 없으면 생성
    if [[ ! -x "$md/venv/bin/python" ]]; then
        info "MCP venv 생성: $md/venv"
        if ! as_op python3 -m venv "$md/venv"; then
            warn "python3 -m venv 실패('python3-venv' 설치 필요?) — MCP 미설치(백엔드 영향 없음)"; return 0
        fi
    fi

    # 3) 의존성 설치 — 동봉 휠 우선(오프라인), 없으면 온라인 시도
    local pip="$md/venv/bin/pip"
    if [[ -d "$HERE/mcp_server/wheels" ]]; then
        as_op rm -rf "$md/wheels"
        as_op cp -r "$HERE/mcp_server/wheels" "$md/wheels"   # 재실행 안전(중첩 방지)
        info "MCP 의존성 설치 (오프라인 휠)"
        if ! as_op "$pip" install -q --no-index --find-links "$md/wheels" -r "$md/requirements.txt"; then
            warn "오프라인 휠 설치 실패 — MCP 미설치. 수동: cd $md && ./venv/bin/pip install --no-index --find-links wheels -r requirements.txt"; return 0
        fi
    else
        info "MCP 의존성 설치 (온라인 pip — 휠 미동봉)"
        if ! as_op "$pip" install -q -r "$md/requirements.txt"; then
            warn "pip 설치 실패(폐쇄망?). 백엔드 배포는 정상. 수동 설치 후 'systemctl restart $MCP_SERVICE_NAME'"; return 0
        fi
    fi

    # 4) 유닛 렌더 + 기동
    render_mcp_service_unit || return 0
    systemctl enable "$MCP_SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl restart "$MCP_SERVICE_NAME" \
        || warn "MCP 서비스 기동 실패 — 'journalctl -u $MCP_SERVICE_NAME' 확인"
    info "MCP 서버: http://$MCP_HOST:$MCP_PORT/mcp  → 백엔드 $MCP_API_BASE"
}

# ── 동기화 타이머 — 같은 SIF 재사용, 별도 venv 없음. 비치명적. ──
setup_sync_timer() {
    [[ "$SYNC_ENABLED" == "1" ]] || { info "동기화 타이머 비활성(SYNC_ENABLED=0) — 건너뜀"; return 0; }
    [[ -f "$HERE/sync.service.template" && -f "$HERE/sync.timer.template" ]] \
        || { warn "sync.*.template 없음 — 동기화 타이머 건너뜀"; return 0; }
    info "동기화 타이머 렌더 → $SYNC_TIMER_UNIT"
    sed -e "s|@@USER@@|$OPERATOR|g" \
        -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" \
        -e "s|@@APP_NAME@@|$APP_NAME|g" \
        -e "s|@@APP_SLUG@@|$APP_SLUG|g" \
        "$HERE/sync.service.template" > "$SYNC_SERVICE_UNIT"
    sed -e "s|@@APP_NAME@@|$APP_NAME|g" "$HERE/sync.timer.template" > "$SYNC_TIMER_UNIT"
    chmod 644 "$SYNC_SERVICE_UNIT" "$SYNC_TIMER_UNIT"
    systemctl daemon-reload
    systemctl enable --now "${SYNC_SERVICE_NAME}.timer" >/dev/null 2>&1 \
        || warn "동기화 타이머 기동 실패 — 'systemctl status ${SYNC_SERVICE_NAME}.timer' 확인"
    info "동기화 타이머: 5분마다 차례가 된 데이터 소스를 돌립니다 (journalctl -u $SYNC_SERVICE_NAME)"
}

health_check() {
    # 기동 직후에는 아직 안 뜬다. 몇 초 기다려 준다.
    local url="http://127.0.0.1:$APP_PORT/api/health"
    for _ in $(seq 1 10); do
        if curl -fsS --max-time 2 "$url" 2>/dev/null; then echo; return 0; fi
        sleep 1
    done
    warn "$url 이 응답하지 않습니다 — journalctl -u $SERVICE_NAME -n 50"
    return 1
}

# ───────────────────────── 명령 ─────────────────────────
cmd_prepare() {
    info "OS 패키지 설치 (apptainer, postgresql, python3-venv)"
    # python3-venv: MCP 서버가 별도 venv 로 돈다. 없으면 install 때 MCP 만 조용히
    # 건너뛰어지고, 그 사실은 Claude 를 붙이는 날에야 드러난다.
    apt-get update
    apt-get install -y --no-install-recommends \
        apptainer postgresql postgresql-contrib ca-certificates curl python3 python3-venv

    info "postgresql 기동"
    systemctl enable postgresql
    systemctl start postgresql

    ensure_dirs

    info "DB 역할 확인: $DB_USER"
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
        # 비밀번호는 .env 를 만들 때 돌린다 — 여기 값은 어디에도 안 쓰인다.
        sudo -u postgres psql -c "CREATE USER \"$DB_USER\" WITH PASSWORD 'placeholder-rotated-on-env-creation';"
        info "DB 역할 생성: $DB_USER"
    else
        info "DB 역할이 이미 있습니다: $DB_USER"
    fi

    info "데이터베이스 확인: $DB_NAME"
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
        sudo -u postgres psql -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\" ENCODING 'UTF8';"
        info "데이터베이스 생성: $DB_NAME"
    else
        info "데이터베이스가 이미 있습니다: $DB_NAME"
    fi

    cat <<MSG

[OK] 서버 준비 완료.  ($APP_NAME · DB $DB_NAME · 포트 $APP_PORT)
  다음: sudo ./deploy.sh install
MSG
}

cmd_install() {
    [[ -f "$HERE/app.sif" ]] || err "app.sif 가 없습니다 — 릴리스 번들을 풀고 그 안에서 실행하세요"
    info "$APP_NAME $VERSION — 계정 $OPERATOR · 설치 경로 $INSTALL_DIR"

    ensure_dirs
    generate_env_if_missing
    place_sif
    run_migrations
    run_seed
    render_service_unit

    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    health_check || true

    setup_mcp || warn "MCP 설정 건너뜀(비치명적)"
    setup_sync_timer || warn "동기화 타이머 건너뜀(비치명적)"

    cat <<MSG

[OK] 설치 완료.
  로그   : sudo journalctl -u $SERVICE_NAME -f
  접속   : http://<서버주소>:$APP_PORT/
  자료   : $INSTALL_DIR  (filestore·logs·.env — 백업 대상)
  MCP    : sudo systemctl status $MCP_SERVICE_NAME   (Claude 연동, 선택)

  위에 찍힌 관리자 임시 비밀번호는 **다시 표시되지 않습니다.**
  첫 로그인에서 변경이 강제됩니다.
MSG
}

cmd_update() {
    [[ -f "$HERE/app.sif" ]]     || err "app.sif 가 $HERE 에 없습니다"
    [[ -f "$INSTALL_DIR/.env" ]] || err "$INSTALL_DIR/.env 가 없습니다 — 먼저 install 하세요"

    info "$SERVICE_NAME 중지"
    systemctl stop "$SERVICE_NAME" || true

    if [[ -f "$INSTALL_DIR/app.sif" ]]; then
        # **직전 이미지를 남긴다.** 롤백은 이 파일을 되돌리는 것뿐이다.
        info "직전 SIF 보관 → app.sif.prev"
        mv "$INSTALL_DIR/app.sif" "$INSTALL_DIR/app.sif.prev"
    fi
    place_sif
    run_migrations
    # 템플릿(경로·하드닝)이 바뀌었을 수 있으므로 다시 렌더한다.
    render_service_unit

    systemctl start "$SERVICE_NAME"
    health_check || true

    # MCP 도 함께 갱신(소스 교체 + 유닛 재렌더 + 재기동). 비치명적.
    setup_mcp || warn "MCP 설정 건너뜀(비치명적)"
    setup_sync_timer || warn "동기화 타이머 건너뜀(비치명적)"

    cat <<MSG

[OK] 갱신 완료. ($VERSION)

  **파일만 되돌아간다 — 마이그레이션은 취소되지 않는다.**
  롤백:
    sudo systemctl stop $SERVICE_NAME
    sudo mv $INSTALL_DIR/app.sif.prev $INSTALL_DIR/app.sif
    sudo systemctl start $SERVICE_NAME
MSG
}

cmd_reset() {
    cat <<MSG

  ⚠ 초기화 — 다음이 **전부 사라집니다**:
      DB       : $DB_NAME 을 지우고 빈 상태로 다시 만듭니다
      첨부     : $INSTALL_DIR/filestore/* 를 지웁니다
  .env · DB 역할 · systemd 유닛은 그대로 둡니다.

MSG
    read -r -p "정말 진행하려면 '$DB_NAME' 을 그대로 입력하세요: " confirm
    [[ "$confirm" == "$DB_NAME" ]] || err "취소했습니다. 아무것도 바뀌지 않았습니다."

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    info "데이터베이스 재생성: $DB_NAME"
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DROP DATABASE IF EXISTS "$DB_NAME";
CREATE DATABASE "$DB_NAME" OWNER "$DB_USER" ENCODING 'UTF8';
SQL
    info "첨부 삭제"
    as_op find "$INSTALL_DIR/filestore" -mindepth 1 -delete 2>/dev/null || true

    run_migrations
    run_seed
    systemctl start "$SERVICE_NAME"
    health_check || true
    echo
    echo "[OK] 초기화 완료. 위에 찍힌 관리자 임시 비밀번호로 로그인하세요."
}

cmd_status() {
    echo "== $APP_NAME ($APP_SLUG) =="
    echo "  설치 경로 : $INSTALL_DIR"
    echo "  DB        : $DB_NAME"
    echo "  포트      : $APP_PORT"
    [[ -f "$INSTALL_DIR/app.sif" ]] && echo "  SIF       : $(stat -c '%y' "$INSTALL_DIR/app.sif")"
    echo
    systemctl --no-pager --lines=10 status "$SERVICE_NAME" || true
    echo
    echo "== /api/health =="
    health_check || true
    if [[ -f "$MCP_SERVICE_UNIT" ]]; then
        echo
        echo "== MCP ($MCP_SERVICE_NAME · http://$MCP_HOST:$MCP_PORT/mcp) =="
        systemctl --no-pager --lines=5 status "$MCP_SERVICE_NAME" || true
    fi
    if [[ -f "$SYNC_TIMER_UNIT" ]]; then
        echo
        echo "== 동기화 타이머 ($SYNC_SERVICE_NAME.timer) =="
        systemctl --no-pager list-timers "${SYNC_SERVICE_NAME}.timer" || true
    fi
}

cmd_auto() {
    if [[ -f "$INSTALL_DIR/app.sif" && -f "$INSTALL_DIR/.env" && -f "$SERVICE_UNIT" ]]; then
        info "기존 설치를 찾았습니다 → update"
        cmd_update
    else
        info "새 설치 → install"
        cmd_install
    fi
}

usage() {
    cat <<MSG
$APP_NAME 배포 스크립트 ($VERSION)

  sudo ./deploy.sh [prepare|install|update|reset|status]

  prepare   최초 1회: apt 패키지, postgres, DB 역할·DB
  install   SIF + .env + systemd, 마이그레이션, 시드, 기동
  update    SIF 교체 + 마이그레이션 + 재시작 (자료 그대로)
  reset     DB·첨부 초기화 (파괴적)
  status    서비스 상태 + health
  (없으면)  자동: 처음이면 install, 아니면 update

지금 설정 (env 로 덮을 수 있음):
  OPERATOR    = $OPERATOR
  INSTALL_DIR = $INSTALL_DIR
  DB_NAME     = $DB_NAME
  DB_USER     = $DB_USER
  APP_PORT    = $APP_PORT
  MCP_ENABLED = $MCP_ENABLED   (MCP_HOST=$MCP_HOST MCP_PORT=$MCP_PORT MCP_API_BASE=$MCP_API_BASE)
MSG
}

case "${1:-}" in
    prepare)        cmd_prepare ;;
    install)        cmd_install ;;
    update)         cmd_update  ;;
    reset)          cmd_reset   ;;
    status)         cmd_status  ;;
    ""|auto)        cmd_auto    ;;
    -h|--help|help) usage       ;;
    *)              usage; exit 1 ;;
esac
