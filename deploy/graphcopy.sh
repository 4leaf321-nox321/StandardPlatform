#!/usr/bin/env bash
# 그래프 사본 다시 싣기(4단계) — PostgreSQL → CSV → **쉬고 있는 쪽 폴더**에 통째로 싣고 바꿔 끼운다.
#
#   GC_DIR/active  → slot-a | slot-b   (지금 도는 쪽 — 그래프 사본 서비스가 읽는다)
#   GC_DIR/import  내보낸 CSV(끝나면 지운다)
#   GC_DIR/conf    neo4j.conf(deploy.sh 가 쓴다) · conf-next 는 쉬는 쪽을 가리키는 사본
#
# 싣는 동안 앞 판이 그대로 답한다. 바꿔 끼울 때만 서비스를 다시 띄운다(수십 초). 새 판이 안 뜨면 앞
# 판으로 되돌린다. root 로 돈다(systemd 의 그래프 사본 다시 싣기 유닛) — 내보내기 · 싣기는 운영 계정으로.
set -euo pipefail
: "${GC_USER:?}" "${GC_SIF:?}" "${GC_ENV_FILE:?}" "${GC_DIR:?}" "${GC_SERVICE:?}" "${GC_PORT:?}"
APPTAINER="${APPTAINER:-/usr/bin/apptainer}"
GC_WAIT_ROUNDS="${GC_WAIT_ROUNDS:-90}"   # 2초씩 — 새 판이 뜨기를 기다리는 횟수

log() { echo "[graphcopy] $*"; }
in_sif() {
    runuser -u "$GC_USER" -- "$APPTAINER" exec \
        --bind "$GC_ENV_FILE:/opt/app/backend/.env:ro" \
        --bind "$GC_DIR:/data/graphdb" "$@"
}
app() { in_sif "$GC_SIF" sh -c "cd /opt/app/backend && /opt/app/venv/bin/python -m app.modules.graph.copycli $*"; }
bolt_up() { (exec 3<>"/dev/tcp/127.0.0.1/$GC_PORT") 2>/dev/null; }
switch_to() {  # $1=slot-a|slot-b
    ln -sfn "$1" "$GC_DIR/active.new"
    chown -h "$GC_USER:$GC_USER" "$GC_DIR/active.new"
    mv -Tf "$GC_DIR/active.new" "$GC_DIR/active"
}

active="$(readlink "$GC_DIR/active" 2>/dev/null || true)"
case "$active" in
    slot-a) next=slot-b ;;
    *) next=slot-a ;;
esac
password="$(sed -n 's|^GRAPHDB_PASSWORD=||p' "$GC_ENV_FILE" | tail -n1)"
[[ -n "$password" ]] || { log "GRAPHDB_PASSWORD 가 $GC_ENV_FILE 에 없습니다 — sudo ./deploy.sh update"; exit 1; }
started=$SECONDS

log "1/4 내보내기 (PostgreSQL → $GC_DIR/import)"
rm -rf "$GC_DIR/import"
install -d -o "$GC_USER" -g "$GC_USER" "$GC_DIR/import" "$GC_DIR/logs" "$GC_DIR/run"
app export /data/graphdb/import

log "2/4 싣기 → $next (지금 도는 쪽: ${active:-없음})"
rm -rf "${GC_DIR:?}/$next" "$GC_DIR/conf-next"
install -d -o "$GC_USER" -g "$GC_USER" "$GC_DIR/$next" "$GC_DIR/conf-next"
sed "s|^server.directories.data=.*|server.directories.data=/data/graphdb/$next|" \
    "$GC_DIR/conf/neo4j.conf" > "$GC_DIR/conf-next/neo4j.conf"
chown "$GC_USER:$GC_USER" "$GC_DIR/conf-next/neo4j.conf"
mapfile -t files < "$GC_DIR/import/import.args"
in_sif --env NEO4J_CONF=/data/graphdb/conf-next "$GC_SIF" \
    /opt/neo4j/bin/neo4j-admin database import full neo4j \
    --overwrite-destination --array-delimiter=$'\x1f' --multiline-fields=true \
    --skip-bad-relationships=true --report-file=/data/graphdb/logs/import.report "${files[@]}"
# 첫 비밀번호 — 그 폴더의 사본이 처음 뜨기 전에만 먹는다(폴더마다 새로 만드니 늘 처음이다).
in_sif --env NEO4J_CONF=/data/graphdb/conf-next "$GC_SIF" \
    /opt/neo4j/bin/neo4j-admin dbms set-initial-password "$password" >/dev/null

log "3/4 바꿔 끼우기 — $GC_SERVICE 다시 띄움"
# 판 없이 한 번 떴던 사본이 `active` 를 폴더로 만들었으면 치운다(링크라야 바꿔 끼운다).
if [[ -d "$GC_DIR/active" && ! -L "$GC_DIR/active" ]]; then
    systemctl stop "$GC_SERVICE" 2>/dev/null || true
    rm -rf "${GC_DIR:?}/active"
fi
switch_to "$next"
systemctl restart "$GC_SERVICE"
for _ in $(seq 1 "$GC_WAIT_ROUNDS"); do bolt_up && break; sleep 2; done
if ! bolt_up; then
    log "새 판($next)이 안 떴습니다 — journalctl -u $GC_SERVICE"
    if [[ -n "$active" ]]; then
        log "앞 판($active)으로 되돌립니다"
        switch_to "$active"
        systemctl restart "$GC_SERVICE"
    fi
    exit 1
fi

log "4/4 색인"
for attempt in 1 2 3 4 5; do
    if app indexes /data/graphdb/import; then break; fi
    [[ $attempt -eq 5 ]] && { log "색인을 못 세웠습니다 — 사본은 돌지만 느릴 수 있습니다"; exit 1; }
    sleep 10
done
rm -rf "$GC_DIR/import" "$GC_DIR/conf-next"
log "끝: $next · $((SECONDS - started))초"
