#!/usr/bin/env bash
# 운영 .env 점검 — 읽기만 한다. 문제마다 한 줄, 「오류」 가 하나라도 있으면 종료 코드 1.
#   sudo ./env-check.sh              이 서버의 설치 전부(/etc/platform-instances/*.conf 가 가리키는 .env)
#   sudo ./env-check.sh <.env> …     파일을 직접(설치 기록과 견주는 검사는 빠진다)
set -euo pipefail

INSTANCES="${ETC:-}/etc/platform-instances"
bad=0
declare -A owner=()                      # 포트 → 그 포트를 쓰는 slug(앱 · MCP)

say()  { printf '  %-4s %s\n' "$1" "$2"; if [[ "$1" == 오류 ]]; then bad=1; fi; }
# deploy.sh · backup.sh 와 같은 읽기 — `KEY=` 로 시작하는 마지막 줄. \r 은 따로 본다.
get()  { sed -n "s|^$2=||p" "$1" | tail -n1 | tr -d '\r'; }
conf() { if [[ -f "$INSTANCES/$1.conf" ]]; then sed -n "s|^$2=||p" "$INSTANCES/$1.conf" | tail -n1; fi; }
on()   { [[ "${1,,}" =~ ^(1|true|yes|on|t|y)$ ]]; }

check() {  # $1=.env  $2=설치 기록의 slug(직접 준 파일이면 빈 값)
    local f="$1" want="$2" key v slug port mcp p prefix url who
    echo "== $f${want:+  ($want)}"
    [[ -r "$f" ]] || { say 오류 "읽을 수 없다 — 운영 계정으로, 또는 sudo 로"; return 0; }
    [[ "$(head -c3 "$f" | od -An -tx1 | tr -d ' \n')" == efbbbf ]] \
        && say 오류 "BOM — deploy.sh · backup.sh 가 첫 줄의 키를 못 읽는다(BOM 없는 UTF-8 로 다시 저장)"
    grep -q $'\r' "$f" && say 오류 "CRLF — deploy.sh 가 읽는 값 끝에 \\r 이 붙는다: sed -i 's/\\r\$//' $f"
    grep -q 'REPLACE_' "$f" && say 오류 "REPLACE_ 자리가 남았다(손으로 만든 .env)"
    for key in APP_ENV APP_SLUG APP_NAME PORT DATABASE_URL JWT_SECRET; do
        [[ -n "$(get "$f" "$key")" ]] || say 오류 "$key 가 비었다"
    done
    v="$(grep -oE '^[A-Z][A-Z0-9_]*=' "$f" | sort | uniq -d | tr -d '=' | xargs || true)"
    [[ -z "$v" ]] || say 경고 "두 번 적힌 키(마지막 줄이 이긴다): $v"
    for key in UVICORN_WORKERS TRUST_PROXY REFRESH_COOKIE_SECURE CORS_ORIGINS DATASOURCE_DIR BACKUP_DIR; do
        grep -qE "^$key=[[:space:]]*$" "$f" && say 오류 "$key= 가 빈 값 — 앱이 안 뜨거나(숫자 · 참거짓 · 목록) '.' 로 읽힌다(경로). 줄을 지운다"
    done
    grep -qE '^(APP_SLUG|APP_NAME|PORT|DATABASE_URL|BACKUP_DIR|PUBLIC_PATH|PUBLIC_URL)=.*[[:space:]]#' "$f" \
        && say 경고 "줄 끝 주석 — deploy.sh · backup.sh 는 주석까지 값으로 읽는다"

    [[ "$(get "$f" APP_ENV)" == production ]] || say 경고 "APP_ENV 가 production 이 아니다(이미지가 production 으로 덮지만 .env 도 맞춰 둔다)"
    slug="$(get "$f" APP_SLUG)"
    [[ "$slug" =~ ^[a-z][a-z0-9]{0,31}$ ]] || say 오류 "APP_SLUG 꼴이 틀렸다: '$slug'"
    [[ -z "$want" || "$slug" == "$want" ]] || say 오류 "APP_SLUG=$slug 인데 설치 기록은 $want — slug 는 설치 뒤 못 바꾼다"
    v="$(get "$f" JWT_SECRET)"
    [[ "$v" == dev-only-insecure-secret-change-me ]] && say 오류 "JWT_SECRET 이 기본값 — 앱이 기동을 거부한다"
    (( ${#v} >= 32 )) || say 경고 "JWT_SECRET 이 ${#v}자 — install 은 64자 남짓을 만든다"
    v="$(get "$f" DATABASE_URL)"
    if [[ "$v" =~ ://([^:]+):([^@]*)@([^:/]+):([0-9]+)/(.+)$ ]]; then   # backup.sh 의 정규식 그대로
        [[ "${BASH_REMATCH[5]}" == "$slug" ]] || say 경고 "DB 이름 '${BASH_REMATCH[5]}' 가 slug 와 다르다 — 남의 DB 가 아닌지"
    else
        say 오류 "DATABASE_URL 을 backup.sh 가 못 쪼갠다 — <사용자>:<비밀번호>@<호스트>:<포트>/<DB> 꼴로(비밀번호에 @ 없이)"
    fi
    for key in VIEW_SCOPE:all\|department EMBEDDING_BACKEND:ollama\|off; do
        v="$(get "$f" "${key%%:*}")"
        [[ -z "$v" || "$v" =~ ^(${key#*:})$ ]] || say 오류 "${key%%:*}=$v — ${key#*:} 만 된다(틀리면 앱이 안 뜬다)"
    done

    # 포트 — 앱과 MCP(설치 기록 · 없으면 +2)가 이 서버의 다른 설치와 겹치나, .env 와 설치 기록이 같은가.
    port="$(get "$f" PORT)" who="${want:-$slug}"
    if [[ "$port" =~ ^[0-9]+$ ]]; then
        v="$(conf "$who" APP_PORT)"
        [[ -z "$want" || -z "$v" || "$v" == "$port" ]] \
            || say 오류 ".env PORT=$port · 설치 기록 APP_PORT=$v — update 는 .env 의 PORT 를 고치지 않는다"
        mcp="$(conf "$who" MCP_PORT)"; mcp="${mcp:-$(( ${v:-$port} + 2 ))}"
        for p in "$port" "$mcp"; do
            [[ -n "${owner[$p]:-}" && "${owner[$p]}" != "$who" ]] \
                && say 오류 "포트 $p 를 ${owner[$p]} 도 쓴다(앱 · MCP) — 앱 포트를 플랫폼마다 10씩 벌린다"
            owner[$p]="$who"
        done
    else
        say 오류 "PORT 가 숫자가 아니다: '$port'"
    fi

    # 주소와 접두어 — PUBLIC_URL 의 경로는 PUBLIC_PATH 와 같아야 한다(접두어까지 적는 칸).
    prefix="$(get "$f" PUBLIC_PATH | sed 's|^ *||; s| *$||; s|^/*||; s|/*$||')"; prefix="${prefix:+/$prefix}"
    url="$(get "$f" PUBLIC_URL)"; url="${url%/}"
    if [[ -n "$url" ]]; then
        if [[ "$url" =~ ^https?://[^/?#]+(/[^?#]*)?$ ]]; then
            v="${BASH_REMATCH[1]:-}"
            [[ "$v" == "$prefix" ]] || say 오류 "PUBLIC_URL 의 경로 '${v:-/}' ≠ PUBLIC_PATH '${prefix:-/}' — AI 답의 링크가 다른 화면을 연다"
        else
            say 오류 "PUBLIC_URL 이 http(s)://호스트[:포트][/접두어] 꼴이 아니다: $url"
        fi
    fi
    if [[ -n "$(get "$f" PORTAL_JWKS_URL)" ]]; then
        [[ -n "$prefix" ]] || say 오류 "포털 로그인이 켜졌는데 PUBLIC_PATH 가 없다 — 로그인 뒤 포털 첫 화면으로 간다"
        on "$(get "$f" TRUST_PROXY)" || say 경고 "포털 뒤인데 TRUST_PROXY 가 꺼졌다 — 쿠키에 Secure 가 안 붙는다"
        [[ -n "$url" ]] || say 경고 "포털 뒤인데 PUBLIC_URL 이 없다 — 링크가 PORTAL_LAUNCH_URL 의 호스트 짐작에 기댄다"
        [[ -n "$(get "$f" MCP_PUBLIC_URL)" ]] || say 경고 "포털 뒤인데 MCP_PUBLIC_URL 이 없다 — 화면이 없는 길(<포털>/<id>/mcp)을 안내한다"
        # 발급자(iss)는 코드에 사내 주소를 두지 않아(v0.4.75) PORTAL_ISSUER → PORTAL_LAUNCH_URL → PUBLIC_URL 의 앞부분으로 읽는다.
        if [[ -z "$(get "$f" PORTAL_ISSUER)$(get "$f" PORTAL_LAUNCH_URL)$url" ]]; then
            say 오류 "포털 로그인이 켜졌는데 PORTAL_ISSUER · PORTAL_LAUNCH_URL · PUBLIC_URL 이 모두 없다 — 발급자(iss)를 못 정해 포털 로그인이 전부 「확인 실패」 다. PORTAL_LAUNCH_URL=https://<포털 호스트>/launch/<id> 를 적는다"
        fi
    fi
    v="$(get "$f" HEAX_SSO_SECRET)"
    [[ -z "$v" || ${#v} -ge 32 ]] || say 경고 "HEAX_SSO_SECRET 이 ${#v}자 — 32자 이상(openssl rand -hex 32)"
    if [[ -n "$prefix" && -z "$(get "$f" PORTAL_JWKS_URL)" ]] && ! on "$(get "$f" TRUST_PROXY)"; then
        say 경고 "PUBLIC_PATH 가 있는데 TRUST_PROXY 가 꺼졌다 — 프록시 뒤가 아니면 접두어를 비운다"
    fi
    return 0
}

if (( $# > 0 )); then
    for one in "$@"; do check "$one" ""; done
else
    shopt -s nullglob
    files=("$INSTANCES"/*.conf)
    (( ${#files[@]} > 0 )) || { echo "설치 기록이 없다: $INSTANCES — .env 경로를 인자로 준다" >&2; exit 2; }
    for one in "${files[@]}"; do
        slug="$(basename "$one" .conf)"
        dir="$(conf "$slug" DATA_DIR)"; dir="${dir:-$(conf "$slug" INSTALL_DIR)}"
        check "$dir/.env" "$slug"           # deploy.sh 와 같은 규칙 — DATA_DIR 이 있으면 거기, 없으면 설치 폴더
    done
fi
(( bad == 0 )) && echo "오류 없음" || { echo "오류가 있다 — 위의 줄을 고친 뒤 다시"; exit 1; }
