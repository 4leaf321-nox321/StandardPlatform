#!/usr/bin/env bash
# 백업 — 데이터베이스와 첨부를 **같은 시각에 함께** 받는다.
#
#   ./backup.sh                                  .env 를 읽어 알아서
#   ./backup.sh -i ~/apps/<slug> -o ~/backup/<slug>
#   ./backup.sh -e /data/<공통>/<slug>/.env -f /data/<공통>/<slug>/filestore -o /data/<공통>/<slug>/backup
#                                                공용 스토리지를 쓸 때(이중화) — deploy.sh 가 타이머에 이렇게 건다
#
# **둘 중 하나만 받으면 복구되지 않는다.** DB 에는 첨부의 경로와 해시가,
# filestore 에는 그 실제 내용이 있다. 시점이 어긋나면 「DB 에는 있는데 파일이 없는」
# 행이 생기고, 그 상태로 앱이 뜬다 — 화면은 멀쩡하고 그 첨부를 열 때만 터진다.
#
# ## 배치
#
#   <BackupRoot>/db/db-<시각>.dump   pg_dump 커스텀 포맷 — 일 7벌 + 일요일분 4벌
#                                   (작업 파일 내용 job_files.data 는 뺀다 — 임시물)
#   <BackupRoot>/filestore/          첨부 미러 1벌(_jobs/ · _incoming/ 은 임시물이라 뺀다)
#   <BackupRoot>/filestore-deleted/<YYYYmmdd>/   그날 미러에서 빠진 파일 — 같은 경로로 14일
#   <BackupRoot>/env/.env            접속 정보·JWT 비밀키
#   <BackupRoot>/LAST_BACKUP.txt     무엇을 언제 받았는지 · 경고
#
# 첨부는 **불변 파일**이라 세대 대신 미러 한 벌이다 — 다만 바로 지우지 않는다(아래 「첨부」).
# 되살리기: rsync -a <BackupRoot>/filestore-deleted/<날짜>/ <첨부 폴더>/
#
# ## 매일 받게 하기 (systemd 타이머)
#
# **앱 프로세스에 넣지 않는다** — 앱이 죽은 날 백업도 조용히 죽는다.
#
#   sudo tee /etc/systemd/system/<slug>-backup.service <<'EOF'
#   [Unit]
#   Description=<slug> backup
#   [Service]
#   Type=oneshot
#   User=<operator>
#   ExecStart=/home/<operator>/apps/<slug>/backup.sh -i /home/<operator>/apps/<slug> -o /home/<operator>/backup/<slug>
#   EOF
#   sudo tee /etc/systemd/system/<slug>-backup.timer <<'EOF'
#   [Unit]
#   Description=<slug> backup daily
#   [Timer]
#   OnCalendar=*-*-* 03:00:00
#   Persistent=true
#   [Install]
#   WantedBy=timers.target
#   EOF
#   sudo systemctl enable --now <slug>-backup.timer
#
# `Persistent=true` 를 빼지 않는다. 서버가 03시에 꺼져 있었으면 그날 백업이
# 통째로 없어지는데, 그 사실은 아무 데도 안 적힌다.
#
# 그리고 `.env` 에 `BACKUP_DIR` 를 적어 두면 **앱이 오래된 백업을 홈에 띄운다.**
#
# 서버 두 대가 같은 백업 폴더에 받을 때는 둘 다 타이머를 걸되 SKIP_IF_FRESH_HOURS=20 을 준다 —
# 그 시간 안의 덤프가 이미 있으면 건너뛴다. 한 대가 죽어도 다른 대가 받고, 둘 다 살아 있어도
# 하루 한 벌이다.

set -euo pipefail

err()  { echo "오류: $*" >&2; exit 1; }
info() { echo "==> $*"; }

INSTALL_DIR=""
BACKUP_ROOT=""
ENV_FILE=""
FILESTORE=""
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
SKIP_IF_FRESH_HOURS="${SKIP_IF_FRESH_HOURS:-0}"
KEEP_DELETED_DAYS="${KEEP_DELETED_DAYS:-14}"
# 한 번에 지울 첨부가 미러의 이 비율(%)을 넘고 이 수도 넘으면 이번에는 안 지운다.
MASS_DELETE_PERCENT="${MASS_DELETE_PERCENT:-50}"
MASS_DELETE_MIN="${MASS_DELETE_MIN:-100}"
ALLOW_MASS_DELETE="${ALLOW_MASS_DELETE:-0}"

while getopts ":i:o:e:f:h" opt; do
    case "$opt" in
        i) INSTALL_DIR="$OPTARG" ;;
        o) BACKUP_ROOT="$OPTARG" ;;
        e) ENV_FILE="$OPTARG" ;;
        f) FILESTORE="$OPTARG" ;;
        h) sed -n '2,/^$/p' "$0"; exit 0 ;;
        *) err "쓰지 않는 옵션입니다. -h 로 사용법을 보세요." ;;
    esac
done

[[ -n "$INSTALL_DIR" ]] || INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -n "$ENV_FILE" ]]  || ENV_FILE="$INSTALL_DIR/.env"
[[ -n "$FILESTORE" ]] || FILESTORE="$INSTALL_DIR/filestore"
[[ -f "$ENV_FILE" ]] || err "$ENV_FILE 가 없습니다. -i 로 설치 폴더를, 또는 -e 로 .env 를 주세요."

# **접속 정보는 .env 에서 읽는다.** 백업이 자기 설정을 따로 갖게 하면 앱과 다른
# DB 를 받는 사고가 나고, 그때 받은 것이 무엇인지 알 방법이 없다.
DSN="$(sed -n 's|^DATABASE_URL=||p' "$ENV_FILE" | tail -n1)"
[[ -n "$DSN" ]] || err ".env 에 DATABASE_URL 이 없습니다."
[[ "$DSN" =~ ://([^:]+):([^@]*)@([^:/]+):([0-9]+)/(.+)$ ]] || err "DATABASE_URL 을 해석하지 못했습니다."
DB_USER="${BASH_REMATCH[1]}"; DB_PW="${BASH_REMATCH[2]}"
DB_HOST="${BASH_REMATCH[3]}"; DB_PORT="${BASH_REMATCH[4]}"; DB_NAME="${BASH_REMATCH[5]}"

[[ -n "$BACKUP_ROOT" ]] || BACKUP_ROOT="$(sed -n 's|^BACKUP_DIR=||p' "$ENV_FILE" | tail -n1)"
[[ -n "$BACKUP_ROOT" ]] || err "받을 곳을 모릅니다. -o 를 주거나 .env 에 BACKUP_DIR 을 적으세요."

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_ROOT"/{db,env}

# 다른 서버가 방금 받았으면 건너뛴다 — 파일 이름의 시각으로 본다(mtime 은 복사하면 바뀐다).
if [[ "$SKIP_IF_FRESH_HOURS" -gt 0 ]]; then
    latest="$(find "$BACKUP_ROOT/db" -maxdepth 1 -name 'db-*.dump' -printf '%f\n' | sort | tail -n1)"
    if [[ "$latest" =~ ^db-([0-9]{8})-([0-9]{6})\.dump$ ]]; then
        then_s="$(date -d "${BASH_REMATCH[1]} ${BASH_REMATCH[2]:0:2}:${BASH_REMATCH[2]:2:2}:${BASH_REMATCH[2]:4:2}" +%s)"
        if (( $(date +%s) - then_s < SKIP_IF_FRESH_HOURS * 3600 )); then
            info "최근 ${SKIP_IF_FRESH_HOURS}시간 안의 덤프가 있습니다($latest) — 건너뜁니다."
            exit 0
        fi
    fi
fi

# ── 데이터베이스 ──────────────────────────────────────────────────────────────
# **`.part` 로 쓰다가 끝나면 이름을 바꾼다.** 도중에 죽으면 반쪽 덤프가 정상
# 이름으로 남고, 그것을 「마지막 백업」 으로 읽게 된다.
info "데이터베이스 백업: $DB_NAME"
DUMP="$BACKUP_ROOT/db/db-$STAMP.dump"
# **작업 파일의 내용(job_files.data)은 안 담는다.** 올린 CSV·만든 파일이라 7일 뒤 지워지는
# 임시물이고, 100MB 짜리가 몇 개만 있어도 덤프가 그것으로 찬다. 표는 담기고 행만 빠지므로
# 복구한 서버에서 「그 작업의 파일이 없다」 가 되고, 그때는 다시 올리면 된다.
PGPASSWORD="$DB_PW" pg_dump --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
    --format=custom --exclude-table-data=job_files --file="$DUMP.part" "$DB_NAME"
mv "$DUMP.part" "$DUMP"

# ── 첨부 ──────────────────────────────────────────────────────────────────────
FILES=0
MIRROR="$BACKUP_ROOT/filestore"
DELETED_ROOT="$BACKUP_ROOT/filestore-deleted"
DELETED_DIR="$DELETED_ROOT/$(date +%Y%m%d)"
MIRROR_NOTE="빠진 것 없음"
WARNING=""
if [[ -d "$FILESTORE" ]]; then
    info "첨부 미러: $FILESTORE -> $MIRROR"
    # **미러에서 바로 지우지 않는다.** 원본에서 사라진 파일(첨부 정리 · 실수로 지움 · 빈 폴더로 다시 마운트)을
    # 그날 밤 미러에서도 지우면, 한 벌뿐인 백업까지 원본과 같은 날 잃는다(2026-11-28 점검 — 예전에는
    # `rsync --delete` 그대로였다). 빠지는 파일은 filestore-deleted/<그날>/ 로 옮기고 KEEP_DELETED_DAYS 일 뒤
    # 지운다. 미러의 절반 넘게(그리고 100개 넘게) 지우게 되면 그날은 안 지우고 「경고」 를 남긴다 — 정말 지운
    # 것이면 한 번만 ALLOW_MASS_DELETE=1.
    mkdir -p "$MIRROR"
    # **작업 파일(_jobs/)과 받다 만 업로드(_incoming/)는 담지 않는다** — 7일 · 하루 뒤 지워지는 임시물이고, 큰
    # 사진 zip 몇 개가 미러를 채웠다. 앞 판이 담아 둔 것은 빠진 파일 칸으로 옮긴다(14일 뒤 지워진다).
    for temp in _jobs _incoming; do
        if [[ -e "$MIRROR/$temp" ]]; then
            mkdir -p "$DELETED_DIR"
            target="$DELETED_DIR/$temp"
            if [[ -e "$target" ]]; then target="$DELETED_DIR/$temp.$STAMP"; fi
            mv "$MIRROR/$temp" "$target"
            info "미러에서 임시 폴더를 뺌: $temp → $target"
        fi
    done
    # 빠지는 파일(지워졌거나 덮인 것)은 지우지 않고 그날의 칸으로 옮긴다(--backup-dir).
    RSYNC=(rsync -a --exclude=/_jobs/ --exclude=/_incoming/ --backup "--backup-dir=$DELETED_DIR")
    MIRRORED=$(find "$MIRROR" -type f | wc -l)
    # 지울 것을 먼저 센다(파일만 — 폴더 줄은 / 로 끝난다).
    GOING=$("${RSYNC[@]}" --delete --dry-run --itemize-changes "$FILESTORE/" "$MIRROR/" \
        | grep -c '^\*deleting .*[^/]$' || true)
    if (( GOING > MASS_DELETE_MIN && GOING * 100 > MIRRORED * MASS_DELETE_PERCENT )) \
        && [[ "$ALLOW_MASS_DELETE" != "1" ]]; then
        WARNING="첨부 미러의 삭제를 건너뜀 — 지울 파일 ${GOING}개가 미러 ${MIRRORED}개의 ${MASS_DELETE_PERCENT}% 를 넘습니다. 원본($FILESTORE)이 비었거나 다른 곳을 가리키는지 확인하세요. 새 파일만 복사했습니다. 정말 지운 것이면 한 번만: ALLOW_MASS_DELETE=1 로 다시 돌립니다."
        echo "경고: $WARNING" >&2
        "${RSYNC[@]}" "$FILESTORE/" "$MIRROR/"
        MIRROR_NOTE="삭제 건너뜀(지울 것 ${GOING}개)"
    else
        "${RSYNC[@]}" --delete "$FILESTORE/" "$MIRROR/"
        if (( GOING > 0 )); then MIRROR_NOTE="${GOING}개를 $DELETED_DIR 로 옮김"; fi
    fi
    FILES=$(find "$MIRROR" -type f | wc -l)
else
    echo "경고: filestore 가 없습니다 — 아직 첨부가 없다면 정상입니다." >&2
fi

# 빠진 파일의 칸은 KEEP_DELETED_DAYS 일 — **폴더 이름의 날짜로 판정한다**(mtime 은 복사하면 바뀐다).
if [[ -d "$DELETED_ROOT" ]]; then
    CUTOFF_DAY="$(date -d "-$KEEP_DELETED_DAYS days" +%Y%m%d)"
    for dir in "$DELETED_ROOT"/*/; do
        [[ -d "$dir" ]] || continue
        day="$(basename "$dir")"
        [[ "$day" =~ ^[0-9]{8}$ ]] || continue
        if [[ "$day" < "$CUTOFF_DAY" ]]; then
            info "오래된 빠진 첨부 삭제: filestore-deleted/$day"
            rm -rf "${DELETED_ROOT:?}/$day"
        fi
    done
fi

# .env 에 JWT 비밀키가 있다. **이게 없으면 복구해도 전원이 다시 로그인한다.**
install -m 600 "$ENV_FILE" "$BACKUP_ROOT/env/.env"

# ── 세대 정리 ─────────────────────────────────────────────────────────────────
# 안 지우면 백업이 디스크를 채운다. **파일 이름의 시각으로 판정한다** — mtime 은
# 복사하면 바뀐다.
mapfile -t DUMPS < <(find "$BACKUP_ROOT/db" -maxdepth 1 -name 'db-*.dump' -printf '%f\n' | sort -r)
KEEP=()
for name in "${DUMPS[@]:0:$KEEP_DAILY}"; do KEEP+=("$name"); done
weekly=0
for name in "${DUMPS[@]}"; do
    [[ "$name" =~ ^db-([0-9]{8})- ]] || continue
    # 일요일분만 따로 남긴다.
    if [[ "$(date -d "${BASH_REMATCH[1]}" +%u 2>/dev/null)" == "7" ]]; then
        KEEP+=("$name"); weekly=$((weekly + 1))
        [[ $weekly -ge $KEEP_WEEKLY ]] && break
    fi
done
for name in "${DUMPS[@]}"; do
    if [[ ! " ${KEEP[*]} " =~ \ ${name}\  ]]; then
        info "오래된 덤프 삭제: $name"
        rm -f "$BACKUP_ROOT/db/$name"
    fi
done
rm -f "$BACKUP_ROOT"/db/*.part 2>/dev/null || true

# ── 기록 ──────────────────────────────────────────────────────────────────────
MB=$(du -m "$DUMP" | cut -f1)
cat > "$BACKUP_ROOT/LAST_BACKUP.txt" <<TXT
받은 시각    : $(date -Is)
설치 경로    : $INSTALL_DIR  (.env $ENV_FILE · 첨부 $FILESTORE)
데이터베이스 : $DB_NAME @ $DB_HOST:$DB_PORT  ($(basename "$DUMP"), ${MB}MB)
첨부         : $FILES 개 (미러: $MIRROR)
첨부 미러    : $MIRROR_NOTE (빠진 파일은 $DELETED_ROOT/<날짜>/ 에 ${KEEP_DELETED_DAYS}일)
보관         : 일 ${KEEP_DAILY}벌 + 일요일분 ${KEEP_WEEKLY}벌
${WARNING:+경고         : $WARNING
}
복구:
  sudo ./restore.sh -b '$BACKUP_ROOT' -d ${DB_NAME}_restore_check          # 확인만
  sudo ./restore.sh -b '$BACKUP_ROOT' -d $DB_NAME -F '$FILESTORE' -f       # 실제 복구(서비스를 멈춘 뒤)
  DB 가 있는 서버에서 sudo 로 — DB 만들기 · 이름 바꾸기 · pgvector 는 그 서버의 postgres 계정이 한다.
  실제 복구는 임시 DB 에 풀어 검사한 뒤 이름을 바꾸고, 지금 DB 는 ${DB_NAME}__before_restore_<시각> 으로 남긴다.
  지워진 첨부 몇 개만 되살릴 때: rsync -a '$DELETED_ROOT/<날짜>/' '$FILESTORE/'   (그날 미러에서 빠진 것)

주의: DB 와 첨부는 같은 시점의 것이어야 한다. 첨부는 미러 한 벌이라 옛 덤프로
      되돌리면 그 뒤에 올린 파일이 「DB 에는 없는데 파일은 있는」 상태가 된다 —
      그것은 무해하다. 반대는 아니다.
TXT

info "백업 완료: $DUMP (DB ${MB}MB, 첨부 $FILES 개 · 미러: $MIRROR_NOTE)"
[[ -z "$WARNING" ]] || echo "경고: $WARNING" >&2
echo
echo "  **한 번은 실제로 복구해 보세요.** 받아만 두고 복구를 해 본 적이 없는"
echo "  백업은 백업이 아닙니다 — restore.sh 가 확인용 DB 로 그것을 해 봅니다."
