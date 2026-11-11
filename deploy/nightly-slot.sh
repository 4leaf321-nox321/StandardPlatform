#!/usr/bin/env bash
# 밤 작업 자리표 — 한 서버의 **모든 설치**가 함께 쓰는 자리 N개(잠금 파일) 중 하나를 잡고 명령을 돌린다.
#
#   nightly-slot.sh <설명> <명령 …>
#
# 한 서버에 SP 설치가 여럿이면 밤의 무거운 일(그래프 사본 · SPARQL 사본 다시 싣기)이 같은 때 몰린다.
# 시각을 나눠 두면 데이터가 커지는 날 겹친다 — 그래서 **빈 자리를 잡은 것만 돌고, 나머지는 기다린다**
# (자리 수 = 동시에 도는 수). 자리는 커널 잠금(flock)이라 작업이 죽어도 저절로 풀린다.
#
#   NIGHTLY_DIR      자리 폴더(기본 /var/lib/platform-nightly — deploy.sh 가 slot-1.lock … 을 만든다)
#   NIGHTLY_WAIT     기다리는 상한(초, 기본 8시간) — 넘으면 이번 밤은 건너뛴다(앞 판이 그대로 답한다)
#   NIGHTLY_POLL     빈 자리를 다시 보는 간격(초, 기본 30)
set -euo pipefail
what="${1:?설명}"; shift
dir="${NIGHTLY_DIR:-/var/lib/platform-nightly}"
wait_max="${NIGHTLY_WAIT:-28800}"
poll="${NIGHTLY_POLL:-30}"

shopt -s nullglob
slots=("$dir"/slot-*.lock)
if [[ ${#slots[@]} -eq 0 ]]; then
    # 자리표가 없으면(옛 배포) 기다리지 않고 그대로 — 막는 것보다 낫다.
    echo "[밤 자리] $dir 에 자리가 없어 바로 돕니다: $what"
    exec "$@"
fi
started=$SECONDS
said=0
while true; do
    for slot in "${slots[@]}"; do
        exec {fd}<"$slot" || continue
        if flock -n "$fd"; then
            echo "[밤 자리] $(basename "$slot") 를 잡았습니다(${#slots[@]}자리 · 기다림 $((SECONDS - started))초): $what"
            # 자리는 이 프로세스가 끝날 때 풀린다(명령도 그 잠금을 물려받아, 다 끝나야 풀린다).
            code=0
            "$@" || code=$?
            exit "$code"
        fi
        exec {fd}<&-
    done
    if (( SECONDS - started >= wait_max )); then
        echo "[밤 자리] ${wait_max}초를 기다려도 빈 자리가 없어 이번에는 건너뜁니다: $what"
        exit 75
    fi
    if (( said == 0 )); then
        echo "[밤 자리] ${#slots[@]}자리가 다 찼습니다 — 빌 때까지 기다립니다: $what"
        said=1
    fi
    sleep "$poll"
done
