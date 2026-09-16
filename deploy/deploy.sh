#!/usr/bin/env bash
# 배포 — 준비 · 설치 · 갱신 · 초기화 · 상태.
#
#   sudo ./deploy.sh prepare   최초 1회: apt 패키지, apptainer(공식 PPA), postgres, DB 역할·DB 생성
#   sudo ./deploy.sh install   SIF 배치 + .env 생성 + systemd + 마이그레이션 + 시드 + 기동
#   sudo ./deploy.sh update    SIF 교체 + 마이그레이션 + 재시작 (자료 그대로)
#   sudo ./deploy.sh reset     DB 통째로 초기화 (파괴적)
#   sudo ./deploy.sh status    서비스 상태 + /api/health
#   sudo ./deploy.sh           자동: 설치된 흔적이 없으면 install, 있으면 update
#
# 서버 두 대 이중화(ha.sh · pg-ha.sh — 자세한 것은 README 「이중화」):
#   sudo ./deploy.sh db-primary | db-standby --from <IP> | db-promote | db-demote | db-status
#   sudo ./deploy.sh lb        메인 서버용 nginx 조각 · keepalived(DB VIP)를 env 에서 다시 만들고 반영
#
# **번들 하나로 여러 플랫폼(인스턴스)을 설치한다.** 어느 플랫폼인지는 `APP_SLUG` 가 정한다 —
# 처음 한 번 env 로 주면(`APP_SLUG=plmhub APP_NAME="PLM 기준정보" APP_PORT=8040 EXTENSIONS=hub`)
# /etc/platform-instances/<slug>.conf 에 남아 다음부터는 `APP_SLUG=plmhub ./deploy.sh update` 로
# 충분하고, 이 서버에 인스턴스가 하나뿐이면 그것마저 생략된다. 안 주면 번들의 기본값
# (BUILD_INFO — 틀의 이름)으로 뜬다. slug 하나에서 DB · 유닛 · 경로 · 주소가 전부 나온다.
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

APP_NAME_DEFAULT="$(bundle app_name)"
APP_SLUG_DEFAULT="$(bundle app_slug)"
APP_PORT_DEFAULT="$(bundle port)"
VERSION="$(bundle version)"
[[ -n "$APP_SLUG_DEFAULT" && -n "$APP_NAME_DEFAULT" ]] || err "BUILD_INFO 에 app_name/app_slug 가 없습니다."

# ───────────────────────── 사전 확인 ─────────────────────────
# 'render' 는 아무것도 바꾸지 않는다 — root 없이 유닛 · nginx · keepalived 설정을 보여 준다.
RENDER_ONLY=0; [[ "${1:-}" =~ ^(render|-h|--help|help)$ ]] && RENDER_ONLY=1
[[ $EUID -eq 0 || $RENDER_ONLY -eq 1 ]] || err "root 로 실행하세요 (sudo)"

OPERATOR="${OPERATOR:-${SUDO_USER:-}}"
[[ -n "$OPERATOR" && "$OPERATOR" != "root" ]] \
    || err "운영 계정을 알 수 없습니다. 일반 사용자로 sudo 하거나 OPERATOR=<이름> 을 주세요."
[[ $RENDER_ONLY -eq 1 ]] || id "$OPERATOR" >/dev/null 2>&1 || err "그런 계정이 없습니다: $OPERATOR"

# ───────────────────────── 이 설치는 무슨 플랫폼인가 ─────────────────────────
# 우선순위: env > /etc/platform-instances/<slug>.conf(지난 설치가 남긴 것) > 번들 기본값.
ETC="${ETC:-}"
INSTANCES_DIR="$ETC/etc/platform-instances"
instance_conf_get() { [[ -f "$INSTANCES_DIR/$1.conf" ]] && sed -n "s|^$2=||p" "$INSTANCES_DIR/$1.conf" | tail -n1 || true; }

if [[ -z "${APP_SLUG:-}" ]]; then
    # slug 를 안 줬다 — 이 서버에 설치된 인스턴스가 하나면 그것, 여럿이면 물어본다.
    mapfile -t _known < <(ls "$INSTANCES_DIR"/*.conf 2>/dev/null | xargs -rn1 basename | sed 's/\.conf$//')
    if [[ ${#_known[@]} -eq 1 ]]; then
        APP_SLUG="${_known[0]}"
    elif [[ ${#_known[@]} -gt 1 ]]; then
        err "이 서버에 인스턴스가 여럿입니다: ${_known[*]} — APP_SLUG=<slug> 로 어느 것인지 주세요."
    else
        APP_SLUG="$APP_SLUG_DEFAULT"
    fi
fi
[[ "$APP_SLUG" =~ ^[a-z][a-z0-9]{0,31}$ ]] || err "APP_SLUG 는 소문자·숫자 한 덩어리 32자 이내여야 합니다: $APP_SLUG"
APP_NAME="${APP_NAME:-$(instance_conf_get "$APP_SLUG" APP_NAME)}"; APP_NAME="${APP_NAME:-$APP_NAME_DEFAULT}"
APP_TAGLINE="${APP_TAGLINE:-$(instance_conf_get "$APP_SLUG" APP_TAGLINE)}"
EXTENSIONS="${EXTENSIONS-$(instance_conf_get "$APP_SLUG" EXTENSIONS)}"
APP_PORT="${APP_PORT:-$(instance_conf_get "$APP_SLUG" APP_PORT)}"; APP_PORT="${APP_PORT:-$APP_PORT_DEFAULT}"

# **설치 경로·DB·유닛 이름이 전부 slug 에서 나온다.** 한 서버에 여러 플랫폼을
# 얹을 때 이것이 겹치면 서로를 덮어쓴다.
INSTALL_DIR="${INSTALL_DIR:-$(instance_conf_get "$APP_SLUG" INSTALL_DIR)}"
INSTALL_DIR="${INSTALL_DIR:-/home/$OPERATOR/apps/$APP_SLUG}"
PG_VERSION="${PG_VERSION:-16}"                          # 두 서버가 같아야 복제가 된다
DB_NAME="${DB_NAME:-$APP_SLUG}"
DB_USER="${DB_USER:-$APP_SLUG}"
SERVICE_NAME="$APP_SLUG"
SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"

# sed 치환값에 들어가면 뜻을 갖는 글자(& | \)를 막는다 — 이름에 '&' 가 있으면 매치 전체가 들어간다.
sed_escape() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }

# 이 인스턴스의 설정을 남긴다 — 다음 배포가 기억한다.
instance_save() {
    mkdir -p "$INSTANCES_DIR"
    cat > "$INSTANCES_DIR/$APP_SLUG.conf" <<EOF
# $APP_NAME ($APP_SLUG) — deploy.sh 가 기억한다(env 로 덮으면 갱신). 설치 뒤 slug 는 바꾸지 않는다.
APP_NAME=$APP_NAME
APP_TAGLINE=$APP_TAGLINE
APP_PORT=$APP_PORT
EXTENSIONS=$EXTENSIONS
INSTALL_DIR=$INSTALL_DIR
DATA_DIR=${DATA_DIR:-}
BACKUP_HOST_DIR=${BACKUP_HOST_DIR:-}
EOF
    chmod 644 "$INSTANCES_DIR/$APP_SLUG.conf"
}

as_op() { sudo -u "$OPERATOR" "$@"; }

# ───────────────────────── 이중화 · 공용 스토리지 ─────────────────────────
# ha.sh 가 /etc/platform-ha.conf(호스트) 와 $INSTALL_DIR/deploy.conf(플랫폼) 를 읽어
# HA_ROLE · LB_MODE · PEER_IP · DB_VIP · PUBLIC_HOST · DATA_DIR (local 이면 WEB_VIP) 를 채운다(env 가 우선).
[[ -f "$HERE/ha.sh" ]] || err "ha.sh 가 $HERE 에 없습니다 (릴리스 번들에서 실행하세요)"
# shellcheck disable=SC1091
source "$HERE/ha.sh"
ha_load

# **무엇이 공용이고 무엇이 로컬인가.** DATA_DIR(/data/<공통폴더>/<slug>)를 주면 첨부 · 백업 ·
# 인증서 · .env 는 거기(두 서버가 같이 본다), 코드(SIF) · 로그는 이 서버($INSTALL_DIR)다.
# 안 주면 지금까지처럼 전부 $INSTALL_DIR — 단독 서버.
if [[ -n "$DATA_DIR" ]]; then
    ENV_FILE="$DATA_DIR/.env"
    FILESTORE_HOST_DIR="$DATA_DIR/filestore"
    BACKUP_HOST_DIR="$DATA_DIR/backup"
else
    ENV_FILE="$INSTALL_DIR/.env"
    FILESTORE_HOST_DIR="$INSTALL_DIR/filestore"
    BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-}"
fi
LOG_HOST_DIR="$INSTALL_DIR/logs"
# 앱이 DB 를 찾는 주소 — DB VIP 가 있으면 그것, 이중화인데 아직 없으면 주(master) 서버, 아니면 로컬.
if [[ -n "$DB_VIP" ]]; then DB_HOST="$DB_VIP"
elif [[ "$HA_ROLE" == "backup" ]]; then DB_HOST="$PEER_IP"
elif [[ "$HA_ROLE" == "master" ]]; then DB_HOST="$SELF_IP"
else DB_HOST="localhost"; fi
DB_HOST="${DB_HOST_OVERRIDE:-$DB_HOST}"

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
# **앱 포트 +2** — 인스턴스마다 앱 포트가 다르므로 번들의 mcp_port 는 기본 포트일 때만 맞는다.
MCP_PORT_DEFAULT="$(bundle mcp_port)"
[[ "$APP_PORT" == "$APP_PORT_DEFAULT" && -n "$MCP_PORT_DEFAULT" ]] || MCP_PORT_DEFAULT=$((APP_PORT + 2))
MCP_HOST="${MCP_HOST:-$(unit_env MCP_HOST)}"
# 이중화면 상대 서버의 nginx 도 이 MCP 에 붙어야 한다 — 로컬에만 열면 절반의 요청이 502 다.
[[ -n "$HA_ROLE" ]] && MCP_HOST="${MCP_HOST:-0.0.0.0}"; MCP_HOST="${MCP_HOST:-127.0.0.1}"
MCP_PORT="${MCP_PORT:-$(unit_env MCP_PORT)}";  MCP_PORT="${MCP_PORT:-$MCP_PORT_DEFAULT}"
MCP_API_BASE="${MCP_API_BASE:-$(unit_env PLATFORM_API_BASE)}"
MCP_API_BASE="${MCP_API_BASE:-http://127.0.0.1:$APP_PORT}"
# DNS rebinding 보호 허용 Host(쉼표구분). 비우면 server.py 가 비-localhost 바인딩 시
# 보호를 끈다(사내망). 외부 노출 시 도메인/IP 지정 권장. 이 값도 위처럼 기억된다.
MCP_ALLOWED_HOSTS="${MCP_ALLOWED_HOSTS:-$(unit_env MCP_ALLOWED_HOSTS)}"
# 우리 nginx(LB_MODE=local)는 Host 를 그대로 넘기므로 공개 호스트명이 허용 목록에 있어야 한다.
# 메인 서버의 nginx 가 무엇을 넘기는지는 모르므로 그때는 비워 둔다(0.0.0.0 이면 보호를 끈다 — 사내망).
[[ "$HA_ROLE" != "" && "$LB_MODE" == "local" && -z "$MCP_ALLOWED_HOSTS" ]] && MCP_ALLOWED_HOSTS="$PUBLIC_HOST,$WEB_VIP,$SELF_IP,$PEER_IP"

# ───────────────────────── 조각들 ─────────────────────────
# apptainer 는 **우분투 기본 저장소에 없다** — 공식 PPA 에만 있다. `apt-get install apptainer`
# 를 바로 부르면 새 서버에서 「Unable to locate package」 로 멈추고, set -e 라 그 뒤의 준비가
# 통째로 안 된다. CI(`.github/actions/apptainer`)가 까는 길과 같은 길로 간다.
APPTAINER_PPA="ppa:apptainer/ppa"
APPTAINER_DOCS="https://apptainer.org/docs/admin/main/installation.html"
APPTAINER_DEBS="https://github.com/apptainer/apptainer/releases"
OS_RELEASE="${OS_RELEASE:-/etc/os-release}"

# **source 하지 않는다** — os-release 의 VERSION 이 번들 버전(VERSION)을 덮는다.
os_id() { sed -n 's/^ID=//p' "$OS_RELEASE" 2>/dev/null | tr -d '"'; }

ensure_apptainer() {
    if command -v apptainer >/dev/null 2>&1; then
        info "apptainer 가 이미 있습니다: $(apptainer --version 2>/dev/null || echo '?')"
        return 0
    fi
    # 이미 닿는 저장소(사내 미러 등)에 있으면 그것을 쓴다 — PPA 를 굳이 더하지 않는다.
    if apt-cache policy apptainer 2>/dev/null | grep -q 'Candidate: [0-9]'; then
        info "apptainer 설치 (이미 닿는 저장소에서)"
        apt-get install -y --no-install-recommends apptainer
        return 0
    fi
    if [[ "$(os_id)" != "ubuntu" ]]; then
        err "apt 에서 apptainer 를 찾을 수 없고 우분투가 아니라 PPA 를 쓸 수 없습니다. $APPTAINER_DOCS 대로 먼저 설치하고 'sudo ./deploy.sh prepare' 를 다시 돌리세요 — 이미 만든 DB·폴더는 그대로 둡니다."
    fi
    info "apptainer 공식 PPA 추가: $APPTAINER_PPA"
    if ! { apt-get install -y --no-install-recommends software-properties-common \
            && add-apt-repository -y "$APPTAINER_PPA" \
            && apt-get update; }; then
        err "PPA 를 추가하지 못했습니다 — 서버가 ppa.launchpadcontent.net 에 닿지 않는 것 같습니다(폐쇄망·프록시). 닿는 PC 에서 apptainer .deb 를 받아($APPTAINER_DEBS) 옮기고 'sudo apt install ./apptainer_*.deb' 로 설치한 뒤 'sudo ./deploy.sh prepare' 를 다시 돌리세요 — 이미 만든 DB·폴더는 그대로 둡니다."
    fi
    apt-get install -y --no-install-recommends apptainer \
        || err "PPA 를 더했는데 apptainer 를 설치하지 못했습니다 — 위의 apt 출력을 확인하세요."
    info "apptainer 설치: $(apptainer --version 2>/dev/null || echo '?')"
}

ensure_dirs() {
    info "설치 폴더 준비: $INSTALL_DIR$( [[ -n "$DATA_DIR" ]] && echo " · 공용 $DATA_DIR" )"
    # **운영 데이터는 SIF 밖이다.** 이미지가 통째로 교체돼도 살아남아야 한다.
    as_op mkdir -p "$INSTALL_DIR" "$LOG_HOST_DIR"
    if [[ -n "$DATA_DIR" ]]; then
        [[ -d "$(dirname "$DATA_DIR")" ]] || err "공용 폴더가 없습니다: $(dirname "$DATA_DIR") — /data 가 마운트됐는지 확인하세요"
        mkdir -p "$DATA_DIR" "$FILESTORE_HOST_DIR" "$BACKUP_HOST_DIR" "$DATA_DIR/tls" "$DATA_DIR/db"
        chown "$OPERATOR:$OPERATOR" "$DATA_DIR" "$FILESTORE_HOST_DIR" "$BACKUP_HOST_DIR"
    else
        as_op mkdir -p "$FILESTORE_HOST_DIR"
    fi
    instance_save
    ha_save
}

generate_env_if_missing() {
    # **있으면 손대지 않는다.** 여기에 JWT 비밀키가 있어서, 덮으면 전원이 다시
    # 로그인한다. 그리고 그것은 배포가 할 일이 아니다.
    [[ -f "$ENV_FILE" ]] && { info ".env 가 이미 있습니다($ENV_FILE) — 그대로 둡니다."; return 0; }
    [[ -f "$HERE/.env.example" ]] || err ".env.example 이 스크립트 옆에 없습니다 ($HERE)"
    # 비밀번호는 **주 DB** 에서 돌린다. 대기 서버(읽기 전용)에서는 할 수 없다 — 이중화의 두 번째
    # 서버는 공용 폴더의 .env 를 그대로 쓰므로 여기 올 일이 없다. 왔다면 순서가 틀린 것이다.
    if pg_in_recovery; then
        if [[ -n "$DATA_DIR" ]]; then
            err "이 서버의 DB 는 대기입니다. .env 는 주 서버에서 install 할 때 $ENV_FILE 에 만들어집니다 — 주 서버에서 먼저 install"
        else
            err "이 서버의 DB 는 대기입니다. 공용 폴더(DATA_DIR)가 없으니 주 서버의 .env 를 그대로 복사해 두세요:
  scp <주 서버>:$INSTALL_DIR/.env $ENV_FILE   (JWT 비밀 · DB 주소가 두 서버에서 같아야 합니다)"
        fi
    fi

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
        -e "s|@localhost:5432/|@$DB_HOST:5432/|" \
        -e "s|^PORT=.*|PORT=$APP_PORT|" \
        -e "s|^APP_SLUG=.*|APP_SLUG=$APP_SLUG|" \
        -e "s|^APP_NAME=.*|APP_NAME=$(sed_escape "$APP_NAME")|" \
        -e "s|^APP_TAGLINE=.*|APP_TAGLINE=$(sed_escape "$APP_TAGLINE")|" \
        -e "s|^EXTENSIONS=.*|EXTENSIONS=$EXTENSIONS|" \
        "$HERE/.env.example" > "$ENV_FILE"
    if [[ -n "$BACKUP_HOST_DIR" ]]; then
        # 컨테이너 안에서 보이는 경로다 — 유닛이 $BACKUP_HOST_DIR 를 /data/backup 에 건다.
        sed -i -e "s|^# BACKUP_DIR=.*|BACKUP_DIR=/data/backup|" "$ENV_FILE"
    fi
    if [[ -n "$HA_ROLE" ]]; then
        # 프록시 뒤 · 경로 접두어 · https. nginx 가 /<slug>/ 를 떼고 넘기고, 앱은 화면 · 쿠키 ·
        # API 주소를 이 접두어 아래로 맞춘다.
        sed -i -e "s|^REFRESH_COOKIE_SECURE=.*|REFRESH_COOKIE_SECURE=true|" "$ENV_FILE"
        cat >> "$ENV_FILE" <<EOF

# --- 이중화 · 프록시 뒤 (deploy.sh 가 넣었다) ---
# https://$PUBLIC_HOST/$APP_SLUG/ — nginx 가 접두어를 떼고 넘긴다. 화면 · 쿠키 · API 가 이 아래로 맞춰진다.
PUBLIC_PATH=/$APP_SLUG
# X-Forwarded-* 를 믿는다 — 프록시(nginx) 뒤에서만 켠다.
TRUST_PROXY=true
EOF
    fi
    chown "$OPERATOR:$OPERATOR" "$ENV_FILE"
    # 비밀키가 들어 있다. 남이 읽을 이유가 없다.
    chmod 600 "$ENV_FILE"
    warn "$ENV_FILE 를 만들었습니다 — 공개 전에 CORS·백업 경로를 확인하세요."
}

# 이름 · 설명 · 확장은 배포로 바꿀 수 있다 — `EXTENSIONS=hub,bom sudo ./deploy.sh update`.
# **slug 만은 못 바꾼다** — DB · 쿠키 · 토큰 · 유닛 이름이 전부 거기서 나왔다.
sync_env_identity() {
    [[ -f "$ENV_FILE" ]] || return 0
    local current; current="$(sed -n 's|^APP_SLUG=||p' "$ENV_FILE" | tail -n1)"
    if [[ -n "$current" && "$current" != "$APP_SLUG" ]]; then
        err ".env 의 APP_SLUG 는 $current 인데 지금은 $APP_SLUG 입니다 — slug 는 설치 뒤 바꿀 수 없습니다 (다른 인스턴스면 APP_SLUG=$current 로)"
    fi
    local key value
    for key in APP_SLUG APP_NAME APP_TAGLINE EXTENSIONS; do
        value="${!key}"
        if grep -q "^$key=" "$ENV_FILE"; then
            sed -i "s|^$key=.*|$key=$(sed_escape "$value")|" "$ENV_FILE"
        else
            printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
        fi
    done
}

place_sif() {
    [[ -f "$HERE/app.sif" ]] || err "app.sif 가 $HERE 에 없습니다 (릴리스 번들에서 실행하세요)"
    info "app.sif 배치 → $INSTALL_DIR/app.sif"
    install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/app.sif" "$INSTALL_DIR/app.sif"
}

# 컨테이너 안에서 한 번 실행. **웹 서비스와 같은 SIF·같은 .env 를 쓴다** —
# 다른 파이썬으로 마이그레이션을 돌리면 그 둘이 다른 스키마를 볼 수 있다.
in_container() {
    local backup_bind=()
    [[ -n "$BACKUP_HOST_DIR" ]] && backup_bind=(--bind "$BACKUP_HOST_DIR:/data/backup")
    as_op apptainer exec \
        --bind "$ENV_FILE:/opt/app/backend/.env:ro" \
        --bind "$FILESTORE_HOST_DIR:/data/filestore" \
        --bind "$LOG_HOST_DIR:/data/logs" \
        ${backup_bind[@]+"${backup_bind[@]}"} \
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

# 유닛 템플릿의 자리표시 — 세 유닛(앱 · 동기화 · 백업)이 같은 경로를 본다.
# 백업 폴더가 없으면 그 --bind 줄을 통째로 뺀다(apptainer 는 없는 원본을 거부한다).
render_unit_paths() {  # $1=template
    # 줄 끝의 '\' 가 다음 줄로 잇는다 — 빈 줄로 두면 거기서 명령이 끊기므로 줄을 아예 지운다.
    local backup_sed
    if [[ -n "$BACKUP_HOST_DIR" ]]; then
        backup_sed="s|^@@BACKUP_BIND@@.*|    --bind $BACKUP_HOST_DIR:/data/backup \\\\|"
    else
        backup_sed='/^@@BACKUP_BIND@@/d'
    fi
    sed -e "s|@@USER@@|$OPERATOR|g" \
        -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" \
        -e "s|@@APP_NAME@@|$APP_NAME|g" \
        -e "s|@@APP_SLUG@@|$APP_SLUG|g" \
        -e "s|@@ENV_FILE@@|$ENV_FILE|g" \
        -e "s|@@FILESTORE_DIR@@|$FILESTORE_HOST_DIR|g" \
        -e "s|@@LOG_DIR@@|$LOG_HOST_DIR|g" \
        -e "s|@@BACKUP_DIR@@|${BACKUP_HOST_DIR:-}|g" \
        -e "$backup_sed" \
        "$1"
}

render_service_unit() {
    [[ -f "$HERE/app.service.template" ]] || err "app.service.template 이 $HERE 에 없습니다"
    info "systemd 유닛 렌더 → $SERVICE_UNIT"
    render_unit_paths "$HERE/app.service.template" > "$SERVICE_UNIT"
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
        -e "s|@@APP_SLUG@@|$APP_SLUG|g" \
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
    render_unit_paths "$HERE/sync.service.template" > "$SYNC_SERVICE_UNIT"
    sed -e "s|@@APP_NAME@@|$APP_NAME|g" "$HERE/sync.timer.template" > "$SYNC_TIMER_UNIT"
    chmod 644 "$SYNC_SERVICE_UNIT" "$SYNC_TIMER_UNIT"
    systemctl daemon-reload
    systemctl enable --now "${SYNC_SERVICE_NAME}.timer" >/dev/null 2>&1 \
        || warn "동기화 타이머 기동 실패 — 'systemctl status ${SYNC_SERVICE_NAME}.timer' 확인"
    info "동기화 타이머: 5분마다 차례가 된 데이터 소스를 돌립니다 (journalctl -u $SYNC_SERVICE_NAME)"
}

# ── 백업 타이머 — 백업 폴더를 알 때만. 이중화면 두 서버 모두 걸리고, backup.sh 가 「오늘
# 것이 이미 있으면」 건너뛴다(한 대가 죽어도 다른 대가 받는다). ──
BACKUP_SERVICE_NAME="${APP_SLUG}-backup"
BACKUP_SERVICE_UNIT="/etc/systemd/system/${BACKUP_SERVICE_NAME}.service"
BACKUP_TIMER_UNIT="/etc/systemd/system/${BACKUP_SERVICE_NAME}.timer"
setup_backup_timer() {
    [[ -n "$BACKUP_HOST_DIR" ]] || return 0
    [[ -f "$HERE/backup.service.template" && -f "$HERE/backup.timer.template" ]] \
        || { warn "backup.*.template 없음 — 백업 타이머 건너뜀"; return 0; }
    install -o "$OPERATOR" -g "$OPERATOR" -m 755 "$HERE/backup.sh" "$INSTALL_DIR/backup.sh"
    install -o "$OPERATOR" -g "$OPERATOR" -m 755 "$HERE/restore.sh" "$INSTALL_DIR/restore.sh"
    # 두 서버가 같은 시각에 같은 덤프를 받지 않게 — 대기(backup) 서버는 30분 뒤.
    local at="03:00"; [[ "$HA_ROLE" == "backup" ]] && at="03:30"
    info "백업 타이머 렌더 → $BACKUP_TIMER_UNIT (매일 $at → $BACKUP_HOST_DIR)"
    render_unit_paths "$HERE/backup.service.template" > "$BACKUP_SERVICE_UNIT"
    sed -e "s|@@APP_NAME@@|$APP_NAME|g" -e "s|@@AT@@|$at|g" "$HERE/backup.timer.template" > "$BACKUP_TIMER_UNIT"
    chmod 644 "$BACKUP_SERVICE_UNIT" "$BACKUP_TIMER_UNIT"
    systemctl daemon-reload
    systemctl enable --now "${BACKUP_SERVICE_NAME}.timer" >/dev/null 2>&1 \
        || warn "백업 타이머 기동 실패 — 'systemctl status ${BACKUP_SERVICE_NAME}.timer' 확인"
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
    info "OS 패키지 설치 (postgresql-$PG_VERSION, python3-venv$( [[ -n "$HA_ROLE" ]] && echo ', keepalived' )$( [[ "$LB_MODE" == "local" ]] && echo ', nginx' ))"
    # python3-venv: MCP 서버가 별도 venv 로 돈다. 없으면 install 때 MCP 만 조용히
    # 건너뛰어지고, 그 사실은 Claude 를 붙이는 날에야 드러난다.
    # apptainer 는 여기 없다 — 기본 저장소에 없어서, 맨 끝 `ensure_apptainer` 가 따로 깐다.
    # **PostgreSQL 은 버전을 박아 깐다.** 두 서버의 버전이 다르면 복제가 안 된다 — 「postgresql」
    # 메타패키지는 OS 가 주는 것을 깔아 서버마다 달라질 수 있다.
    # keepalived 는 DB VIP 를 나중에 받아도 바로 켤 수 있게 이중화면 늘 깐다. nginx 는 우리가 LB 일 때만.
    local ha_pkgs=(); [[ -n "$HA_ROLE" ]] && ha_pkgs=(keepalived)
    [[ "$LB_MODE" == "local" ]] && ha_pkgs+=(nginx openssl)
    apt-get update
    apt-get install -y --no-install-recommends \
        "postgresql-$PG_VERSION" "postgresql-client-$PG_VERSION" postgresql-contrib \
        ca-certificates curl python3 python3-venv rsync ${ha_pkgs[@]+"${ha_pkgs[@]}"}

    info "postgresql 기동"
    systemctl enable postgresql
    systemctl start postgresql || warn "postgresql 이 뜨지 않았습니다 — 대기 서버라면 db-standby 뒤에 뜹니다"

    ensure_dirs

    if pg_in_recovery; then
        info "이 서버의 DB 는 대기(복제본) — 역할 · 데이터베이스는 주에서 만든 것이 넘어온다. 건너뜁니다."
        ensure_apptainer
        cat <<MSG

[OK] 대기 서버 준비 완료.  ($APP_NAME · 주 $PEER_IP)
  다음: sudo ./deploy.sh install
MSG
        return 0
    fi

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

    # **맨 끝에 둔다.** PPA 에 닿지 않는 서버에서 여기서 멈춰도 DB·폴더는 이미 서 있고,
    # apptainer 만 따로 깐 뒤 prepare 를 다시 돌리면 된다(앞 단계는 멱등하다).
    ensure_apptainer

    cat <<MSG

[OK] 서버 준비 완료.  ($APP_NAME · DB $DB_NAME · 포트 $APP_PORT$( [[ -n "$HA_ROLE" ]] && echo " · 이중화 $HA_ROLE" ))
  다음: sudo ./deploy.sh install$( [[ "$HA_ROLE" == "master" ]] && printf '\n  이중화: sudo ./deploy.sh db-primary   (그 뒤 상대 서버에서 prepare → db-standby → install)' )
MSG
}

cmd_install() {
    [[ -f "$HERE/app.sif" ]] || err "app.sif 가 없습니다 — 릴리스 번들을 풀고 그 안에서 실행하세요"
    info "$APP_NAME ($APP_SLUG) $VERSION — 계정 $OPERATOR · 설치 경로 $INSTALL_DIR · 포트 $APP_PORT · 확장 ${EXTENSIONS:-없음}"

    ensure_dirs
    generate_env_if_missing
    sync_env_identity
    place_sif
    run_migrations
    run_seed
    render_service_unit

    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    health_check || true

    setup_mcp || warn "MCP 설정 건너뜀(비치명적)"
    setup_sync_timer || warn "동기화 타이머 건너뜀(비치명적)"
    setup_backup_timer || warn "백업 타이머 건너뜀(비치명적)"
    setup_lb

    cat <<MSG

[OK] 설치 완료.
  로그   : sudo journalctl -u $SERVICE_NAME -f
  접속   : $( [[ -n "$HA_ROLE" ]] && echo "https://$PUBLIC_HOST/$APP_SLUG/  (직접: http://$SELF_IP:$APP_PORT/)" || echo "http://<서버주소>:$APP_PORT/" )
  자료   : 첨부 $FILESTORE_HOST_DIR · 설정 $ENV_FILE · 로그 $LOG_HOST_DIR$( [[ -n "$BACKUP_HOST_DIR" ]] && echo " · 백업 $BACKUP_HOST_DIR" )
  MCP    : sudo systemctl status $MCP_SERVICE_NAME   (Claude 연동, 선택)

  위에 찍힌 관리자 임시 비밀번호는 **다시 표시되지 않습니다.**
  첫 로그인에서 변경이 강제됩니다.
MSG
}

cmd_update() {
    [[ -f "$HERE/app.sif" ]]     || err "app.sif 가 $HERE 에 없습니다"
    [[ -f "$ENV_FILE" ]] || err "$ENV_FILE 가 없습니다 — 먼저 install 하세요"
    info "$APP_NAME ($APP_SLUG) $VERSION — 포트 $APP_PORT · 확장 ${EXTENSIONS:-없음}"
    ensure_dirs
    sync_env_identity

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
    setup_backup_timer || warn "백업 타이머 건너뜀(비치명적)"
    setup_lb

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
      첨부     : $FILESTORE_HOST_DIR/* 를 지웁니다
  .env · DB 역할 · systemd 유닛은 그대로 둡니다.

MSG
    read -r -p "정말 진행하려면 '$DB_NAME' 을 그대로 입력하세요: " confirm
    [[ "$confirm" == "$DB_NAME" ]] || err "취소했습니다. 아무것도 바뀌지 않았습니다."

    pg_in_recovery && err "이 서버의 DB 는 대기입니다 — 초기화는 주 서버에서."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    info "데이터베이스 재생성: $DB_NAME"
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DROP DATABASE IF EXISTS "$DB_NAME";
CREATE DATABASE "$DB_NAME" OWNER "$DB_USER" ENCODING 'UTF8';
SQL
    info "첨부 삭제"
    as_op find "$FILESTORE_HOST_DIR" -mindepth 1 -delete 2>/dev/null || true

    run_migrations
    run_seed
    systemctl start "$SERVICE_NAME"
    health_check || true
    echo
    echo "[OK] 초기화 완료. 위에 찍힌 관리자 임시 비밀번호로 로그인하세요."
}

cmd_status() {
    echo "== $APP_NAME ($APP_SLUG) · 확장 ${EXTENSIONS:-없음} =="
    echo "  설치 경로 : $INSTALL_DIR$( [[ -n "$DATA_DIR" ]] && echo "  · 공용 $DATA_DIR" )"
    echo "  DB        : $DB_NAME @ $DB_HOST"
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
    if [[ -f "$BACKUP_TIMER_UNIT" ]]; then
        echo
        echo "== 백업 타이머 ($BACKUP_SERVICE_NAME.timer → $BACKUP_HOST_DIR) =="
        systemctl --no-pager list-timers "${BACKUP_SERVICE_NAME}.timer" || true
        [[ -f "$BACKUP_HOST_DIR/LAST_BACKUP.txt" ]] && head -n1 "$BACKUP_HOST_DIR/LAST_BACKUP.txt"
    fi
    echo
    ha_status
}

# 아무것도 바꾸지 않고 결과만 — ETC=<폴더> 를 주면 거기 쓰고, 없으면 임시 폴더에 쓴 뒤 화면에 보여 준다.
cmd_render() {
    local show=0
    if [[ -z "${ETC:-}" ]]; then ETC="$(mktemp -d)"; show=1; fi
    local tpl unit
    mkdir -p "$ETC/etc/systemd/system"
    # 설정 파일도 그 아래에 — 실제 배포가 남길 것과 같은 모양을 본다.
    instance_save; ha_save
    for tpl in app.service sync.service backup.service; do
        [[ -f "$HERE/$tpl.template" ]] || continue
        case "$tpl" in app.service) unit="$SERVICE_NAME.service" ;; sync.service) unit="$SYNC_SERVICE_NAME.service" ;; *) unit="$BACKUP_SERVICE_NAME.service" ;; esac
        render_unit_paths "$HERE/$tpl.template" > "$ETC/etc/systemd/system/$unit"
    done
    render_lb
    if [[ $show -eq 1 ]]; then
        find "$ETC" -type f | sort | while read -r f; do echo "### ${f#"$ETC"}"; cat "$f"; echo; done
    else
        echo "렌더 결과: $ETC"
    fi
}

cmd_auto() {
    if [[ -f "$INSTALL_DIR/app.sif" && -f "$ENV_FILE" && -f "$SERVICE_UNIT" ]]; then
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

  prepare   최초 1회: apt 패키지, apptainer(공식 PPA), postgres, DB 역할·DB
  install   SIF + .env + systemd, 마이그레이션, 시드, 기동
  update    SIF 교체 + 마이그레이션 + 재시작 (자료 그대로)
  reset     DB·첨부 초기화 (파괴적)
  status    서비스 상태 + health (+ 이중화 · DB 주/대기)
  (없으면)  자동: 처음이면 install, 아니면 update

  이중화(서버 두 대) — README 「이중화」:
  db-primary              이 서버의 PostgreSQL 을 주로 (복제 계정 · 감시 · 원복 잠금)
  db-standby [--from IP]  이 서버의 PostgreSQL 을 대기로 (데이터는 주에서 새로 받는다)
  db-promote              대기를 주로 (장애)      db-demote  주를 곱게 내림 (계획 전환)
  db-status               역할 · 복제 지연 · VIP
  lb                      메인 서버용 nginx 조각 · keepalived(DB VIP)를 지금 설정으로 다시 만들고 반영

지금 설정 (env 로 덮을 수 있음):
  APP_SLUG    = $APP_SLUG   (APP_NAME="$APP_NAME" EXTENSIONS=${EXTENSIONS:-없음}) — 다른 인스턴스는 APP_SLUG=<slug>
  OPERATOR    = $OPERATOR
  INSTALL_DIR = $INSTALL_DIR
  DATA_DIR    = ${DATA_DIR:-(없음 — 단독 서버, 전부 INSTALL_DIR)}
  DB_NAME     = $DB_NAME @ $DB_HOST
  DB_USER     = $DB_USER
  APP_PORT    = $APP_PORT
  MCP_ENABLED = $MCP_ENABLED   (MCP_HOST=$MCP_HOST MCP_PORT=$MCP_PORT MCP_API_BASE=$MCP_API_BASE)
  HA_ROLE     = ${HA_ROLE:-(없음)}   PEER_IP=$PEER_IP DB_VIP=${DB_VIP:-(없음)} PUBLIC_HOST=$PUBLIC_HOST LB_MODE=$LB_MODE${WEB_VIP:+ WEB_VIP=$WEB_VIP}
MSG
}

case "${1:-}" in
    prepare)        cmd_prepare ;;
    install)        cmd_install ;;
    update)         cmd_update  ;;
    reset)          cmd_reset   ;;
    status)         cmd_status  ;;
    db-primary)     shift; ensure_dirs; cmd_db primary "$@" ;;
    db-standby)     shift; [[ "${1:-}" == "--from" ]] && shift; ensure_dirs; cmd_db standby "${1:-}" ;;
    db-promote)     cmd_db promote ;;
    db-demote)      cmd_db demote ;;
    db-status)      cmd_db status ;;
    lb)             ensure_dirs; setup_lb ;;
    render)         cmd_render ;;
    ""|auto)        cmd_auto    ;;
    -h|--help|help) usage       ;;
    *)              usage; exit 1 ;;
esac
