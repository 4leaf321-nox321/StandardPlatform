#!/usr/bin/env bash
# 개발 PC 의 DB 에 pgvector 확장을 만든다(ADR 0050) — sudo 로 한 번.
#
#   sudo ./scripts/dev_pgvector.sh
#
# **확장은 superuser 만 만든다.** pgvector 는 pg_trgm · btree_gist 와 달리 「신뢰된 확장」 이 아니라
# 앱 계정(DB 주인)이 못 만든다. 운영은 deploy.sh 가 postgres 계정으로 만들고, 개발 PC 는 이것이 한다.
#
# - 앱 계정이 가진 DB 전부(개발 · 시험 · 작업자별 시험 · 규모 실측)에 **`extensions` 스키마로** 만든다.
#   시험은 public 스키마를 통째로 지웠다 세우므로 public 에 두면 함께 사라지고, 다시 만들 수 없다.
# - 새로 만드는 DB 의 틀 `<개발 DB>_template` 도 만든다 — 시험의 새 작업자 DB 와 before_push 의 빈 DB
#   마이그레이션 검사가 이것에서 만든다. **template1 은 건드리지 않는다** — 이 PC 의 다른 프로젝트가 새
#   DB 를 만들 때 따라가, 그쪽의 `CREATE EXTENSION IF NOT EXISTS vector`(public)가 조용히 건너뛰어진다.
# - 패키지(postgresql-<판>-pgvector)가 없으면 apt 로 깐다.
#
# 여러 번 돌려도 된다.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "오류: sudo 로 실행하세요 — sudo $0" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/backend/.env"
[[ -f "$ENV_FILE" ]] || { echo "오류: $ENV_FILE 이 없습니다" >&2; exit 1; }
# postgresql+psycopg://<계정>:<비밀번호>@<호스트>:<포트>/<DB>
url="$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE" | tail -n1 | tr -d '\r')"
role="$(sed -E 's#^[^:]+://([^:@/]+).*#\1#' <<<"$url")"
name="$(sed -E 's#^.*/([^/?]+)(\?.*)?$#\1#' <<<"$url")"
[[ -n "$role" && -n "$name" && "$role" != "$url" ]] || { echo "오류: DATABASE_URL 을 읽지 못했습니다" >&2; exit 1; }

# postgres 계정은 이 저장소 폴더에 못 들어간다 — psql 의 「could not change directory」 를 피한다.
cd /
pg() { sudo -u postgres psql -X -v ON_ERROR_STOP=1 -qtA "$@"; }

major="$(( $(pg -c 'SHOW server_version_num') / 10000 ))"
if [[ ! -f "/usr/share/postgresql/$major/extension/vector.control" ]]; then
    echo "==> postgresql-$major-pgvector 설치"
    apt-get install -y --no-install-recommends "postgresql-$major-pgvector"
fi

template="${name}_template"
if [[ -z "$(pg -c "SELECT 1 FROM pg_database WHERE datname = '$template'")" ]]; then
    echo "==> 새 DB 의 틀: $template"
    pg -c "CREATE DATABASE \"$template\" OWNER \"$role\" ENCODING 'UTF8'"
fi

mapfile -t dbs < <(pg -c "SELECT datname FROM pg_database
                          WHERE datdba = '$role'::regrole AND datallowconn ORDER BY 1")
for db in "${dbs[@]}"; do
    pg -d "$db" <<'SQL'
SET client_min_messages = warning;
CREATE SCHEMA IF NOT EXISTS extensions;
GRANT USAGE ON SCHEMA extensions TO PUBLIC;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        CREATE EXTENSION vector SCHEMA extensions;
    ELSIF (SELECT extnamespace::regnamespace::text FROM pg_extension
           WHERE extname = 'vector') <> 'extensions' THEN
        ALTER EXTENSION vector SET SCHEMA extensions;
    END IF;
END $$;
ALTER EXTENSION vector UPDATE;
SQL
    echo "    $db: vector $(pg -d "$db" -c "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
done
echo "[OK] pgvector — 계정 $role 의 DB ${#dbs[@]}개"
