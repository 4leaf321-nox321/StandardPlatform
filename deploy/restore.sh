#!/usr/bin/env bash
# 백업에서 되돌리고 — **되돌아왔는지 확인한다.**
#
#   sudo ./restore.sh -b ~/backup/<slug> -d <slug>_restore_check          확인용 (기본)
#   sudo ./restore.sh -b ~/backup/<slug> -d <slug> -i ~/apps/<slug> -f    실제 복구 (서비스를 멈춘 뒤)
#   sudo ./restore.sh -b <백업> -d <slug> -F /data/<공통>/<slug>/filestore -f   첨부가 공용 스토리지에 있을 때
#
#   -b <폴더>   backup.sh 가 받은 폴더 (env/.env · db/ · filestore/)
#   -d <이름>   되돌릴 DB. 운영 DB 와 같은 이름이면 -f 가 있어야 한다
#   -D <파일>   쓸 덤프 (기본: db/ 의 가장 새것)
#   -i <폴더>   첨부를 <폴더>/filestore 로 되돌린다
#   -F <폴더>   첨부를 이 폴더로 되돌린다 (공용 스토리지)
#   -f          덮어쓰기 허락 — 운영 DB 이름 · 이미 있는 DB · 비어 있지 않은 첨부 폴더.
#               덮는 DB 는 지우지 않고 <이름>__before_restore_<시각> 으로 남긴다
#
# DB 서버에서 root(sudo)로 돌리면 DB 만들기 · 이름 바꾸기 · pgvector 는 그 서버의 postgres 계정이
# 한다(앱이 쓰는 클러스터인지 견준 뒤). 아니면 앱 계정 — CREATEDB 가 없으면 아무것도 안 바꾸고 멈춘다.
#
# ## 운영을 비운 채 끝나지 않는다
#
#   1. 덤프를 읽어 본다(`pg_restore -l`) — 못 읽거나 표의 행이 없으면 **아무것도 안 바꾸고** 멈춘다
#   2. 임시 DB(<이름>__restoring, 지금 DB 와 같은 정렬)에 푼다 — pg_restore 가 중간에 멈췄거나 무해한 것 밖의 오류(표 ·
#      행 · 색인 · 제약)를 냈거나, 표가 없거나 alembic_version 이 비었으면 임시 DB 를 지우고
#      멈춘다(운영 DB 는 그대로)
#   3. 그때서야 이름을 바꾼다 — 있던 DB 는 <이름>__before_restore_<시각> 으로 비켜 두고(지우지
#      않는다, 지우는 명령을 끝에 적는다) 임시 DB 를 <이름> 으로. 두 이름 바꾸기는 한 트랜잭션이다

set -euo pipefail

# `backup.sh` 의 마지막 줄은 "한 번은 실제로 복구해 보세요" 라고 말한다. 그런데 그
# 「복구」 가 사람이 손으로 치는 명령 두 줄이면, 치다 틀렸을 때 **절반만 돌아온
# 상태**가 되고 그것을 알아챌 자리가 없다.
#
# 전에는 덤프를 보기 전에 운영 DB 를 지웠고, pg_restore 의 실패는 무시하고 「표가 하나라도
# 있나」 만 봤다 — 덤프가 깨졌거나 앱 계정이 DB 를 못 만들면 **운영이 빈 채** 끝났다.
#
# 이름을 바꾸려면 그 DB 에 접속이 없어야 한다 — 앱 · 워커 · MCP · 타이머를 멈추고 돌린다.
# 남아 있으면 풀기 전에 멈추고 접속한 곳을 적는다(끊지 않는다).
#
# ## 이 스크립트가 하는 일의 절반은 검사다
#
# 되돌리는 것 자체는 pg_restore 와 파일 복사다. **어려운 것은 둘의 시점이 맞는지**다.
# DB 에는 첨부의 경로와 해시가 있고 filestore 에는 그 내용이 있다 — 한쪽만 되돌리면
# 「DB 에는 있는데 파일이 없는」 행이 생기고 **그 상태로 앱이 뜬다.** 화면은 멀쩡하고
# 그 첨부를 열 때만 터진다.
#
# 그래서 복구가 끝나면 **DB 가 가리키는 파일이 실제로 있는지 세어 본다.**
#
# ## 기본은 확인용이다
#
# 다른 이름의 DB 로 풀어 "이 백업이 살아 있는가" 를 본다. 운영을 덮어쓰려면 `-f` 를
# 의식적으로 준다. **첨부는 `-i` · `-F` 를 줄 때만 되돌리고, 그 폴더가 비어 있지 않으면 DB
# 이름이 무엇이든 `-f` 가 있어야 한다** — 확인용 이름에 `-i` 를 붙였다고 운영 첨부를 덮던
# 때가 있었다. 첨부는 백업의 것을 더하고 덮을 뿐 **백업에 없는 파일(백업 뒤에 올린 것)을
# 지우지 않는다** — 비켜 둔 옛 DB 로 돌아갈 때 그 파일이 있어야 한다. 아무 첨부도 안
# 가리키는 파일은 앱의 첨부 정리가 하루 뒤에 지운다.

err()  { echo "오류: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "경고: $*" >&2; }

BACKUP_ROOT=""; DB_NAME=""; INSTALL_DIR=""; FILESTORE=""; DUMP_FILE=""; FORCE=0
# 안내 문구에 그대로 적는다(「root 로 다시 돌리세요」).
ARGS="$*"

while getopts ":b:d:i:F:D:fh" opt; do
    case "$opt" in
        b) BACKUP_ROOT="$OPTARG" ;;
        d) DB_NAME="$OPTARG" ;;
        i) INSTALL_DIR="$OPTARG" ;;
        F) FILESTORE="$OPTARG" ;;
        D) DUMP_FILE="$OPTARG" ;;
        f) FORCE=1 ;;
        h) sed -n '2,/^$/p' "$0"; exit 0 ;;
        *) err "쓰지 않는 옵션입니다. -h 로 사용법을 보세요." ;;
    esac
done

[[ -n "$BACKUP_ROOT" ]] || err "-b <백업 폴더> 가 필요합니다."
# 첨부를 되돌릴 자리 — -F 로 직접, 또는 -i 의 설치 폴더 아래.
[[ -n "$FILESTORE" || -z "$INSTALL_DIR" ]] || FILESTORE="$INSTALL_DIR/filestore"
[[ -n "$DB_NAME" ]]     || err "-d <복구할 DB 이름> 이 필요합니다."
# 이름은 SQL 에 그대로 들어간다 — 따옴표 · 공백이 섞이면 엉뚱한 문장이 된다.
[[ "$DB_NAME" =~ ^[A-Za-z0-9_-]{1,63}$ ]] || err "DB 이름은 영문 · 숫자 · _ · - 로 63자까지입니다: '$DB_NAME'"

# 접속 정보는 **백업이 함께 받아 둔 .env** 에서 읽는다. 앱이 살아 있지 않아도
# 복구할 수 있어야 한다 — 서버가 통째로 날아간 상황이 바로 복구가 필요한 상황이다.
ENV_FILE="$BACKUP_ROOT/env/.env"
[[ -f "$ENV_FILE" ]] || err "백업에 .env 가 없습니다: $ENV_FILE"
DSN="$(sed -n 's|^DATABASE_URL=||p' "$ENV_FILE" | tail -n1)"
[[ "$DSN" =~ ://([^:]+):([^@]*)@([^:/]+):([0-9]+)/(.+)$ ]] || err "DATABASE_URL 을 해석하지 못했습니다."
DB_USER="${BASH_REMATCH[1]}"; DB_PW="${BASH_REMATCH[2]}"
DB_HOST="${BASH_REMATCH[3]}"; DB_PORT="${BASH_REMATCH[4]}"; SOURCE_DB="${BASH_REMATCH[5]}"
SLUG="$(sed -n 's|^APP_SLUG=||p' "$ENV_FILE" | tail -n1)"; SLUG="${SLUG:-<slug>}"

if [[ "$DB_NAME" == "$SOURCE_DB" && $FORCE -ne 1 ]]; then
    err "'$DB_NAME' 은 운영 데이터베이스입니다.
확인만 하려면 다른 이름을 주세요:  -d ${SOURCE_DB}_restore_check
정말 운영을 백업 시점으로 되돌리려면 서비스를 멈추고 -f 를 주세요 — 지금 DB 는 지우지 않고
'${DB_NAME}__before_restore_<시각>' 으로 남기지만, 앱은 백업 뒤의 입력을 못 보게 됩니다."
fi

# ── 첨부 폴더 — **DB 이름과 상관없이** 비어 있지 않으면 -f ─────────────────────
if [[ -n "$FILESTORE" && $FORCE -ne 1 && -d "$FILESTORE" ]] \
    && [[ -n "$(find "$FILESTORE" -mindepth 1 -print -quit 2>/dev/null || true)" ]]; then
    err "첨부 폴더 '$FILESTORE' 가 비어 있지 않습니다 — 백업의 첨부로 덮어씁니다.
확인만 하려면 -i · -F 를 빼세요(백업 안의 첨부로 견줘 봅니다).
정말 이 폴더로 되돌리려면 -f 를 주세요. 아무것도 바꾸지 않았습니다."
fi

# ── 덤프 고르기 ───────────────────────────────────────────────────────────────
if [[ -z "$DUMP_FILE" ]]; then
    DUMP_FILE="$(find "$BACKUP_ROOT/db" -maxdepth 1 -name 'db-*.dump' | sort -r | head -n1)"
    [[ -n "$DUMP_FILE" ]] || err "백업에 덤프가 없습니다: $BACKUP_ROOT/db"
fi
[[ -f "$DUMP_FILE" ]] || err "덤프를 찾을 수 없습니다: $DUMP_FILE"
info "덤프: $DUMP_FILE ($(du -m "$DUMP_FILE" | cut -f1)MB)"

export PGPASSWORD="$DB_PW"
psql_() { psql -X -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -tAc "$1" "${2:-postgres}"; }
# 이 서버의 postgres 계정(peer 인증) — 같은 포트의 소켓으로. 그 계정이 못 읽는 폴더에서 부르면
# psql 이 경고를 내므로 / 에서.
LOCAL_ARGS=(-p "$DB_PORT"); [[ "$DB_HOST" == /* ]] && LOCAL_ARGS+=(-h "$DB_HOST")
su_psql() { (cd / && sudo -u postgres psql -X "${LOCAL_ARGS[@]}" "$@"); }
# **DB 를 만들고 · 지우고 · 이름을 바꾸는 일**은 `ADMIN` 계정으로 — 아래에서 정한다. 여러 문장을
# 한 번에 주면 **한 트랜잭션**이다 — 이름 바꾸기 둘이 함께 되거나 함께 안 된다.
ADMIN="app"
admin_sql() {
    if [[ "$ADMIN" == "postgres" ]]; then
        su_psql -q -v ON_ERROR_STOP=1 -c "$1" "${2:-postgres}" >/dev/null
    else
        psql -X -q -v ON_ERROR_STOP=1 -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -c "$1" "${2:-postgres}" >/dev/null
    fi
}
# 접속이 안 되는 것을 「없다」 로 읽지 않는다 — 없으면 만들고 있으면 비켜 두는 판단이 이것에 달렸다.
exists_db() {
    local got
    got="$(psql_ "SELECT 1 FROM pg_database WHERE datname='$1'")" \
        || err "DB 에 접속하지 못했습니다($DB_USER@$DB_HOST:$DB_PORT) — '$DB_NAME' 은 그대로입니다."
    [[ "$got" == "1" ]]
}
# 그 DB 에 붙은 접속 — 이름을 바꾸려면 0 이어야 한다. 끊지 않는다: 누가 붙어 있는지 사람이 본다.
# **계정이 있는 접속만 센다** — autovacuum 작업자도 그 DB 이름을 달고 뜨는데(계정이 없다), 이름
# 바꾸기는 그것을 스스로 내보낸다. 세면 서비스를 다 멈춘 직후에도 「접속 1 개: ?」 로 거절했다.
# 앱 계정에게는 남의 접속의 backend_type 이 가려져 보여서(실측) 그것으로는 못 가른다.
refuse_if_connected() {
    local who
    who="$(psql_ "SELECT coalesce(nullif(application_name, ''), usename::text)
                  FROM pg_stat_activity
                  WHERE datname='$1' AND usename IS NOT NULL AND pid <> pg_backend_pid()")"
    [[ -z "$who" ]] && return 0
    err "'$1' 에 접속 $(grep -c . <<<"$who") 개가 남아 있습니다: $(sort <<<"$who" | uniq -c | head -n5 | sed 's/^ *//' | paste -sd ',' -)
이름을 바꾸려면 접속이 없어야 합니다 — 앱 · 워커 · MCP 와 DB 를 쓰는 타이머를 멈추고 다시 돌리세요:
    sudo systemctl stop $SLUG $SLUG-worker $SLUG-mcp
$2"
}

# 그 DB 의 정렬 — 「LC_COLLATE|LC_CTYPE|제공자|ICU 로캘|인코딩」. 칸 이름이 판마다 달라(PG16
# daticulocale · PG17 datlocale, PG14 에는 제공자가 없다) 행을 JSON 으로 꺼내 읽는다.
locale_of() {
    psql_ "SELECT d.datcollate || '|' || d.datctype
                  || '|' || coalesce(to_jsonb(d)->>'datlocprovider', 'c')
                  || '|' || coalesce(to_jsonb(d)->>'daticulocale', to_jsonb(d)->>'datlocale', '')
                  || '|' || pg_encoding_to_char(d.encoding)
           FROM pg_database d WHERE d.datname='$1'"
}
provider_name() { case "$1" in c) echo libc ;; i) echo ICU ;; b) echo builtin ;; *) echo "$1" ;; esac; }

# PostgreSQL 이름은 63바이트까지 — 넘으면 **조용히 잘려** 적어 준 이름과 실제가 달라진다.
fit() { local room=$((63 - ${#2})); printf '%s%s' "${1:0:$room}" "$2"; }
TEMP_DB="$(fit "$DB_NAME" "__restoring")"
BEFORE_DB="$(fit "$DB_NAME" "__before_restore_$(date +%Y%m%d%H%M%S)")"

LISTING="$(mktemp)"; RESTORE_LOG=""; CREATED_TEMP=0; SWAPPED=0
cleanup() {
    rm -f "$LISTING" "$LISTING.err"
    if [[ $CREATED_TEMP -eq 1 && $SWAPPED -eq 0 ]]; then
        echo "==> 임시 DB $TEMP_DB 삭제 — '$DB_NAME' 은 그대로입니다." >&2
        if ! admin_sql "DROP DATABASE IF EXISTS \"$TEMP_DB\"" 2>/dev/null; then
            echo "경고: 임시 DB 를 지우지 못했습니다 — dropdb --host=$DB_HOST --port=$DB_PORT --username=$DB_USER $TEMP_DB" >&2
        fi
    fi
}
trap cleanup EXIT

# ── 덤프 검사 — **어느 DB 도 건드리기 전에** ──────────────────────────────────
# 깨진 덤프 · 다른 형식 · 빈 덤프로 운영을 지운 뒤에 그것을 알면 늦다.
if ! pg_restore -l "$DUMP_FILE" >"$LISTING" 2>"$LISTING.err"; then
    err "덤프를 읽지 못했습니다: $DUMP_FILE
$(head -n3 "$LISTING.err")
아무것도 바꾸지 않았습니다. 다른 덤프를 -D 로 고르세요(ls $BACKUP_ROOT/db)."
fi
DATA_ENTRIES="$(grep -cE '^[0-9]+; [0-9]+ [0-9]+ TABLE DATA ' "$LISTING" || true)"
[[ "$DATA_ENTRIES" -gt 0 ]] || err "덤프에 표의 행(TABLE DATA)이 하나도 없습니다: $DUMP_FILE
아무것도 바꾸지 않았습니다. 다른 덤프를 -D 로 고르세요(ls $BACKUP_ROOT/db)."
info "덤프 확인: 행이 든 표 $DATA_ENTRIES 개"
VECTOR=0; grep -qE 'EXTENSION - vector( |$)' "$LISTING" && VECTOR=1

# ── DB 를 만들고 이름을 바꾸는 계정 ─────────────────────────────────────────────
# **앱 계정에 CREATEDB 를 주지 않는다** — 앱이 털리면 그 권한으로 DB 를 마구 만든다. 그래서 root 로
# DB 서버에서 돌리면 그 서버의 postgres 계정이 만들고(임시 DB 의 주인은 앱 계정) 이름을 바꾼다.
# 다만 **앱이 쓰는 바로 그 서버**일 때만 — 같은 포트에 다른 클러스터(옛 판 · 대기)가 떠 있을 수 있어
# postmaster 시작 시각과 system_identifier 를 두 접속에서 견준다. DB 가 다른 서버면(이중화의 DB
# VIP 가 저쪽에 있다) 앱 계정으로 하고, 그 계정에 CREATEDB 가 없으면 **아무것도 안 바꾸고** 멈춘다.
#
# 대기 · 주인 · 접속은 늘 앱 계정의 접속으로 본다 — 앱이 보는 것이 기준이다. pg_restore 도 앱 계정이다.
RECOVERY="$(psql_ "SELECT pg_is_in_recovery()")" \
    || err "DB 에 접속하지 못했습니다($DB_USER@$DB_HOST:$DB_PORT). 아무것도 바꾸지 않았습니다."
[[ "$RECOVERY" == "f" ]] \
    || err "앱이 쓰는 DB($DB_HOST:$DB_PORT)가 대기(복제본)입니다 — 복구는 주 서버에서 합니다. 아무것도 바꾸지 않았습니다."
CLUSTER_SQL="SELECT pg_postmaster_start_time() || ' ' || system_identifier FROM pg_control_system()"
case "$DB_HOST" in localhost|127.0.0.1|::1|"[::1]"|/*) LOCAL_HOST=1 ;; *) LOCAL_HOST=0 ;; esac
if [[ "$(id -u)" == "0" ]] && LOCAL_ID="$(su_psql -tAc "$CLUSTER_SQL" postgres 2>/dev/null)"; then
    APP_ID="$(psql_ "$CLUSTER_SQL" 2>/dev/null || true)"
    if [[ -n "$APP_ID" && "$APP_ID" == "$LOCAL_ID" ]]; then
        ADMIN="postgres"
    elif [[ $LOCAL_HOST -eq 1 ]]; then
        err "이 서버의 포트 $DB_PORT 에 뜬 PostgreSQL 이 앱이 쓰는 것($DB_HOST:$DB_PORT)과 다릅니다(앱: '${APP_ID:-읽지 못함}', postgres: '$LOCAL_ID').
다른 클러스터를 건드리지 않으려고 멈춥니다. 아무것도 바꾸지 않았습니다."
    fi
fi
APP_SUPER="$(psql_ "SELECT rolsuper FROM pg_roles WHERE rolname=current_user")"
if [[ "$ADMIN" == "app" ]]; then
    if [[ "$(psql_ "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")" != "t" ]]; then
        err "앱 계정 $DB_USER 에 DB 를 만들 권한(CREATEDB)이 없어 임시 DB 를 만들 수 없습니다. 아무것도 바꾸지 않았습니다.
둘 중 하나로 다시 돌리세요:
  - DB 서버에서 root 로:  sudo ./restore.sh $ARGS   (그 서버의 postgres 계정이 만든다 — 권장)
  - 앱 계정에 권한을 주고: DB 서버에서  sudo -u postgres psql -c 'ALTER ROLE \"$DB_USER\" CREATEDB'"
    fi
    # pgvector 확장은 postgres 계정(슈퍼유저)만 만든다(ADR 0050).
    [[ $VECTOR -eq 0 || "$APP_SUPER" == "t" ]] || err "덤프에 pgvector 확장이 있습니다 — 확장은 postgres 계정만 만듭니다. DB 서버에서 root 로 다시 돌리세요:
    sudo ./restore.sh $ARGS
(postgresql-<판>-pgvector 패키지가 있어야 한다 — sudo ./deploy.sh pgvector 가 깐다). 아무것도 바꾸지 않았습니다."
fi
info "DB 를 만들고 이름을 바꾸는 계정: $([[ "$ADMIN" == "postgres" ]] && echo "이 서버의 postgres" || echo "앱 계정 $DB_USER")"

# ── 풀기 전에 막을 것 ─────────────────────────────────────────────────────────
# 맞출 DB 가 없으면 서버의 기본(template1)대로 만든다 — 무엇이 됐는지는 만든 뒤에 적는다.
TARGET_LOCALE=""; CREATE_OPTS="ENCODING 'UTF8'"
if exists_db "$DB_NAME"; then
    [[ $FORCE -eq 1 ]] || err "'$DB_NAME' 이 이미 있습니다. 백업으로 바꾸려면 -f 를 주세요 — 지금 것은 '$BEFORE_DB' 로 남깁니다.
아무것도 바꾸지 않았습니다."
    refuse_if_connected "$DB_NAME" "아무것도 바꾸지 않았습니다."
    # 이 앱의 DB 인가 — 앱 계정은 제 것만 이름을 바꿀 수 있고, postgres 로는 남의 DB 도 바뀐다.
    OWNER="$(psql_ "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='$DB_NAME'")"
    [[ "$OWNER" == "$DB_USER" || ( "$ADMIN" == "app" && "$APP_SUPER" == "t" ) ]] \
        || err "'$DB_NAME' 의 주인이 앱 계정($DB_USER)이 아니라 '$OWNER' 입니다 — 이 앱의 DB 가 아닐 수 있어 멈춥니다. 아무것도 바꾸지 않았습니다."
    # **정렬을 지금 DB 와 같게** — 기본(template1)으로 만들면 C.UTF-8 로 다시 만든 운영 DB 가 복구
    # 한 번에 조용히 en_US 로 돌아가, 이름의 차례 · 색인 · `<` 비교가 바뀐다(정렬은 덤프에 안 실린다).
    TARGET_LOCALE="$(locale_of "$DB_NAME")"
    IFS='|' read -r L_COLL L_CTYPE L_PROV L_ICU L_ENC <<<"$TARGET_LOCALE"
    for one in "$L_COLL" "$L_CTYPE" "$L_ENC"; do
        [[ "$one" =~ ^[A-Za-z0-9_.@-]+$ ]] || err "'$DB_NAME' 의 정렬을 읽지 못했습니다('$TARGET_LOCALE'). 아무것도 바꾸지 않았습니다."
    done
    [[ "$L_ICU" =~ ^[A-Za-z0-9_.@-]*$ ]] || err "'$DB_NAME' 의 ICU 로캘을 읽지 못했습니다('$L_ICU'). 아무것도 바꾸지 않았습니다."
    CREATE_OPTS="TEMPLATE template0 ENCODING '$L_ENC' LC_COLLATE '$L_COLL' LC_CTYPE '$L_CTYPE'"
    case "$L_PROV" in
        c) ;;
        i) CREATE_OPTS+=" LOCALE_PROVIDER icu ICU_LOCALE '$L_ICU'" ;;
        b) CREATE_OPTS+=" LOCALE_PROVIDER builtin BUILTIN_LOCALE '$L_ICU'" ;;
        *) err "'$DB_NAME' 의 정렬 제공자 '$L_PROV' 를 모릅니다. 아무것도 바꾸지 않았습니다." ;;
    esac
fi
exists_db "$BEFORE_DB" && err "'$BEFORE_DB' 가 이미 있습니다 — 1초 뒤에 다시 돌리세요."

# ── 임시 DB 에 풀기 ───────────────────────────────────────────────────────────
# **비어 있는 DB 로 복구한다.** 기존 표 위에 덮으면 덤프에 없는 옛 행이 남아,
# 복구했는데도 데이터가 섞인 상태가 된다. 임시 이름은 이 스크립트의 것이다 — 지난번에
# 멈춘 것이 남았으면 지우고 다시.
if exists_db "$TEMP_DB"; then
    info "지난번에 남은 임시 DB $TEMP_DB 삭제"
    refuse_if_connected "$TEMP_DB" "아무것도 바꾸지 않았습니다."
    admin_sql "DROP DATABASE \"$TEMP_DB\""
fi
# 주인은 앱 계정 — pg_restore 가 앱 계정으로 그 안에 표를 만든다(`--no-owner`).
admin_sql "CREATE DATABASE \"$TEMP_DB\" OWNER \"$DB_USER\" $CREATE_OPTS" \
    || err "임시 DB '$TEMP_DB' 를 만들지 못했습니다. '$DB_NAME' 은 그대로입니다."
CREATED_TEMP=1
info "임시 DB: $TEMP_DB"
IFS='|' read -r T_COLL T_CTYPE T_PROV T_ICU _ <<<"$(locale_of "$TEMP_DB")"
T_SHOWN="$T_COLL / $T_CTYPE ($(provider_name "$T_PROV")${T_ICU:+ $T_ICU})"
if [[ -n "$TARGET_LOCALE" ]]; then
    [[ "$T_COLL|$T_CTYPE|$T_PROV|$T_ICU" == "$L_COLL|$L_CTYPE|$L_PROV|$L_ICU" ]] \
        || err "임시 DB 의 정렬($T_SHOWN)이 '$DB_NAME' 과 다르게 만들어졌습니다. '$DB_NAME' 은 그대로입니다."
    info "정렬: $T_SHOWN — 지금 '$DB_NAME' 과 같게"
else
    info "정렬: $T_SHOWN — 맞출 '$DB_NAME' 이 없어 서버의 기본(template1)대로"
fi

# **pgvector 확장은 postgres 계정만 만든다**(ADR 0050). 덤프가 그것을 들고 있으면 앱 계정의 pg_restore 가
# 만들지 못해, 그것을 쓰는 표가 안 돌아온 채 「복구됐다」 고 끝난다. 그래서 먼저 만든다 — pg_restore 의
# 「이미 있다」 는 무해하다.
if [[ $VECTOR -eq 1 ]]; then
    VECTOR_SQL="CREATE SCHEMA IF NOT EXISTS extensions; GRANT USAGE ON SCHEMA extensions TO PUBLIC; CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions"
    if admin_sql "$VECTOR_SQL" "$TEMP_DB"; then
        info "pgvector 확장 준비 (extensions 스키마)"
    else
        err "덤프에 pgvector 확장이 있는데 만들지 못했습니다 — 이 서버에 postgresql-<판>-pgvector 패키지가 있어야 합니다(sudo ./deploy.sh pgvector 가 깐다). '$DB_NAME' 은 그대로입니다."
    fi
fi

info "복구 중 → $TEMP_DB"
# pg_restore 는 무해한 오류로 1 로 끝날 수 있다 — 앞에서 만든 `extensions` 스키마가 「이미 있다」,
# postgres 가 가진 확장의 주석을 못 단다. 그것만 넘기고 나머지는 실패로 본다:
#   - **중간에 멈춘 것** — 덤프 뒤쪽이 잘렸으면 목록은 읽히고 행을 읽다 멈춘다. 그때는 끝의
#     「errors ignored on restore」 줄이 없다(실측: 표는 다 서고 행은 0 인 채 1 로 끝났다)
#   - **넣지 못한 것** — 표 · 행(COPY) · 색인 · 제약. 이 서버에 확장(contrib · pgvector)이 없으면
#     그것을 쓰는 표 · 색인이 안 서고, 그 DB 는 비거나 덜 선 표로 뜬다(실측: 13건)
# 가르는 말은 pg_restore 가 쓰는 것이라 영어로 받는다(LC_ALL=C — 서버의 오류 문구는 번역돼도 된다).
RESTORE_LOG="$(mktemp "${TMPDIR:-/tmp}/restore-${DB_NAME}-XXXXXX")"
HARMLESS='^Command was: (CREATE SCHEMA|COMMENT ON|CREATE EXTENSION) '
RC=0
LC_ALL=C pg_restore --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" \
    --dbname "$TEMP_DB" --no-owner --no-privileges "$DUMP_FILE" 2>"$RESTORE_LOG" || RC=$?
if [[ $RC -ne 0 ]]; then
    ERRORS="$(sed -n 's/.*errors ignored on restore: \([0-9][0-9]*\).*/\1/p' "$RESTORE_LOG" | tail -n1)"
    [[ -n "$ERRORS" ]] || err "pg_restore 가 중간에 멈췄습니다(종료 코드 $RC) — 덤프가 잘렸거나 읽을 수 없습니다.
$(tail -n3 "$RESTORE_LOG")
전체 기록: $RESTORE_LOG"
    SKIPPED="$(grep -cE "$HARMLESS" "$RESTORE_LOG" || true)"
    [[ "$ERRORS" -le "$SKIPPED" ]] || err "pg_restore 가 넣지 못한 것이 $((ERRORS - SKIPPED)) 건입니다(표 · 행 · 색인 · 제약, 종료 코드 $RC).
$(grep -E '^Command was: |COPY failed' "$RESTORE_LOG" | grep -vE "$HARMLESS" | cut -c1-160 | head -n5)
전체 기록: $RESTORE_LOG — 이 서버에 확장(postgresql-contrib · pgvector)이 있는지부터 보세요."
    warn "pg_restore 가 무해한 오류 $ERRORS 건으로 끝났습니다(이미 있는 스키마 · 확장의 주석) — 기록: $RESTORE_LOG"
else
    rm -f "$RESTORE_LOG"
fi

TABLES="$(psql_ "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" "$TEMP_DB")"
info "복구된 표: $TABLES 개"
[[ "$TABLES" -gt 0 ]] || err "표가 하나도 복구되지 않았습니다. 덤프를 확인하세요."
# 이 앱의 DB 라는 표 — 마이그레이션 판이 없으면 앱이 빈 DB 로 읽고 처음부터 마이그레이션을 건다.
[[ "$(psql_ "SELECT to_regclass('public.alembic_version') IS NOT NULL" "$TEMP_DB")" == "t" ]] \
    || err "복구한 DB 에 alembic_version 표가 없습니다 — 이 앱의 덤프가 아니거나 덜 풀렸습니다."
REVISION="$(psql_ "SELECT version_num FROM public.alembic_version LIMIT 1" "$TEMP_DB")"
[[ -n "$REVISION" ]] || err "복구한 DB 의 alembic_version 이 비어 있습니다 — 마이그레이션 판을 모르는 DB 로 앱을 띄울 수 없습니다."
info "마이그레이션 판: $REVISION"

# ── 이름 바꾸기 — 검사를 다 통과한 뒤에만 ──────────────────────────────────────
KEPT=""
if exists_db "$DB_NAME"; then
    # 푸는 사이 다시 붙었을 수 있다(타이머).
    [[ $FORCE -eq 1 ]] || err "'$DB_NAME' 이 이미 있습니다. 백업으로 바꾸려면 -f 를 주세요."
    refuse_if_connected "$DB_NAME" "'$DB_NAME' 은 그대로입니다."
    admin_sql "ALTER DATABASE \"$DB_NAME\" RENAME TO \"$BEFORE_DB\"; ALTER DATABASE \"$TEMP_DB\" RENAME TO \"$DB_NAME\"" \
        || err "이름을 바꾸지 못했습니다 — '$DB_NAME' 은 그대로입니다(두 이름 바꾸기는 함께 무릅니다)."
    KEPT="$BEFORE_DB"
    info "옛 $DB_NAME → $BEFORE_DB (지우지 않고 남겼습니다)"
else
    admin_sql "ALTER DATABASE \"$TEMP_DB\" RENAME TO \"$DB_NAME\"" || err "임시 DB 의 이름을 '$DB_NAME' 으로 바꾸지 못했습니다."
fi
SWAPPED=1
info "$TEMP_DB → $DB_NAME"

# ── 첨부 ──────────────────────────────────────────────────────────────────────
CHECK_ROOT=""
if [[ -n "$FILESTORE" ]]; then
    if [[ -d "$BACKUP_ROOT/filestore" ]]; then
        info "첨부 복구: $BACKUP_ROOT/filestore -> $FILESTORE"
        mkdir -p "$FILESTORE"
        # `--delete` 를 주지 않는다 — 백업 뒤에 올린 파일은 비켜 둔 옛 DB 가 가리킨다.
        rsync -a "$BACKUP_ROOT/filestore/" "$FILESTORE/"
    else
        warn "백업에 filestore 가 없습니다."
    fi
    CHECK_ROOT="$FILESTORE"
elif [[ -d "$BACKUP_ROOT/filestore" ]]; then
    # -i 없이 리허설만 할 때도 백업 안의 것으로 견줘 볼 수 있다.
    CHECK_ROOT="$BACKUP_ROOT/filestore"
else
    info "첨부는 건드리지 않았습니다 (-i · -F 를 주지 않음)."
fi

# ── DB 가 가리키는 파일이 실제로 있나 ─────────────────────────────────────────
#
# **이 검사가 이 스크립트의 절반이다.** 표 이름을 손으로 적지 않고 카탈로그에
# 물어본다 — 도메인이 자기 경로 컬럼을 더해도 여기를 안 고쳐도 되고, 컬럼 이름을
# 잘못 적어 검사가 조용히 아무것도 안 보는 일이 없다.
CHECKED=0; MISSING=0
if [[ -n "$CHECK_ROOT" ]]; then
    info "가리키는 파일이 있는지 확인"
    while IFS='.' read -r table column; do
        [[ -n "$table" && -n "$column" ]] || continue
        while read -r rel; do
            [[ -n "$rel" ]] || continue
            CHECKED=$((CHECKED + 1))
            [[ -f "$CHECK_ROOT/$rel" ]] || MISSING=$((MISSING + 1))
        done < <(psql_ "SELECT \"$column\" FROM \"$table\" WHERE \"$column\" IS NOT NULL" "$DB_NAME")
    done < <(psql_ "SELECT table_name || '.' || column_name
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND column_name IN ('relative_path','storage_path','source_path')
                    ORDER BY 1" "$DB_NAME")
fi

kept_note() {
    [[ -n "$KEPT" ]] || return 0
    echo
    echo "  옛 DB 는 '$KEPT' 로 남겼습니다. 앱을 띄워 확인한 뒤 지우세요:"
    echo "    dropdb --host=$DB_HOST --port=$DB_PORT --username=$DB_USER $KEPT"
    echo "  복구 전으로 돌아가려면 서비스를 멈추고 두 이름을 맞바꿉니다(복구한 것은 $TEMP_DB 로 — 다음 복구가 지운다):"
    echo "    psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d postgres -c 'ALTER DATABASE \"$DB_NAME\" RENAME TO \"$TEMP_DB\"; ALTER DATABASE \"$KEPT\" RENAME TO \"$DB_NAME\"'"
}

echo
if [[ -z "$CHECK_ROOT" ]]; then
    warn "첨부를 안 봤습니다 — DB 만 되돌린 상태입니다."
elif [[ $CHECKED -eq 0 ]]; then
    # **0개를 「전부 있다」 로 적으면 안 된다.** 가리키는 파일이 없는 것과 검사가
    # 아무것도 못 찾은 것은 다르고, 뒤엣것은 검사가 고장난 것이다.
    warn "DB 가 가리키는 파일이 하나도 없습니다 — 첨부가 없는 설치이거나, 검사가 표를 못 찾았습니다."
elif [[ $MISSING -gt 0 ]]; then
    kept_note
    err "DB 가 가리키는 파일 $MISSING 개가 없습니다 (확인한 것 $CHECKED 개).

**DB 와 첨부의 시점이 어긋났습니다.** 이 상태로 앱을 띄우면 화면은 멀쩡하고
그 첨부를 열 때만 터집니다. 같은 백업 폴더의 filestore/ 를 함께 되돌리세요."
else
    info "가리키는 파일 $CHECKED 개가 전부 있습니다."
fi

echo
info "복구 완료: $DB_NAME (표 $TABLES 개, 판 $REVISION, 파일 $CHECKED 개 확인)"
kept_note
if [[ "$DB_NAME" != "$SOURCE_DB" ]]; then
    echo
    echo "  확인용 DB 입니다. 다 봤으면 지우세요:"
    echo "    dropdb --host=$DB_HOST --port=$DB_PORT --username=$DB_USER $DB_NAME"
fi
