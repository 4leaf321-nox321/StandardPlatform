#!/usr/bin/env bash
# 임베딩 꾸러미(`build_ai_bundle.sh` 가 만든 것)를 **AI 꾸러미 저장소의 릴리스**로 올린다 — WSL · 리눅스가 없는 PC 도
# 받게(ADR 0072). 꾸러미 하나(약 4.5GB)가 GitHub 첨부 파일 상한(2GB)보다 커서 조각으로 나눠 올리고, 조각마다 ·
# 전체의 지문(`SHA256SUMS`)과 `MANIFEST.txt` 를 함께 올린다. 받는 쪽은 그 저장소의 `fetch-ai-bundle.bat`(.sh)이
# 받고 · 맞추고 · 합친다.
#
#   GITHUB_TOKEN=<repo 권한 토큰> ./deploy/publish_ai_bundle.sh [꾸러미.tar] [태그]
#     꾸러미 — 기본은 release/ai-bundle-*.tar 하나(둘 이상이면 고르라고 멈춘다)
#     태그   — 기본은 ai-<꾸러미를 만든 날, 한국 시각>(MANIFEST 의 built)
#
# **초안으로 만들어 다 올린 뒤에 게시한다** — 올리다 끊겨도 「최신」 이 반쪽 릴리스를 가리키지 않는다. 같은 태그가
# 이미 있으면 멈춘다(같은 이름으로 다른 꾸러미를 올리지 않는다). 필요한 것: curl · python3 · split · sha256sum.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# AI 꾸러미 저장소 — 기본은 이 저장소(origin) 이름에 `-ai-bundle` 을 붙인 것(포크하면 그 포크의 것).
if [[ -z "${AI_REPO:-}" ]]; then
    origin="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
    AI_REPO="$(printf '%s' "$origin" | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
    [[ "$AI_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
        || { echo "오류: origin($origin)에서 GitHub 저장소를 읽지 못했습니다 — AI_REPO=<주인>/<저장소> 로 주세요" >&2; exit 1; }
    AI_REPO="$AI_REPO-ai-bundle"
fi
API="${GITHUB_API:-https://api.github.com}"
UPLOADS="${GITHUB_UPLOADS:-https://uploads.github.com}"
PART_SIZE="${PART_SIZE:-1900M}"   # GitHub 첨부 파일 상한 2GiB 아래

[[ -n "${GITHUB_TOKEN:-}" ]] || { echo "오류: GITHUB_TOKEN 이 없습니다(repo 권한)" >&2; exit 1; }
for tool in curl python3 split sha256sum; do
    command -v "$tool" >/dev/null || { echo "오류: $tool 이 없습니다" >&2; exit 1; }
done

bundle="${1:-}"
if [[ -z "$bundle" ]]; then
    shopt -s nullglob
    found=("$REPO_ROOT"/release/ai-bundle-*.tar)
    shopt -u nullglob
    [[ ${#found[@]} -eq 1 ]] || { echo "오류: release/ai-bundle-*.tar 가 ${#found[@]}개 — 꾸러미를 인자로 주세요" >&2; exit 1; }
    bundle="${found[0]}"
fi
[[ -f "$bundle" ]] || { echo "오류: 꾸러미가 없습니다: $bundle" >&2; exit 1; }
name="$(basename "$bundle")"

work="$(mktemp -d "$(dirname "$bundle")/ai-publish.XXXXXX")"   # 꾸러미 옆 — 조각만큼 빈자리가 든다
trap 'rm -rf "$work"' EXIT
tar -xOf "$bundle" ai/MANIFEST.txt > "$work/MANIFEST.txt" \
    || { echo "오류: 꾸러미에 ai/MANIFEST.txt 가 없습니다 — build_ai_bundle.sh 가 만든 것인가요" >&2; exit 1; }
tag="${2:-}"
if [[ -z "$tag" ]]; then
    tag="ai-$(python3 - "$work/MANIFEST.txt" <<'PY'
import sys
from datetime import datetime, timedelta, timezone

for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("built"):
        built = datetime.strptime(line.split(":", 1)[1].strip(), "%Y-%m-%dT%H:%M:%SZ")
        print((built.replace(tzinfo=timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d"))
        break
PY
)"
    [[ "$tag" != "ai-" ]] || { echo "오류: MANIFEST 에 built 가 없습니다 — 태그를 인자로 주세요" >&2; exit 1; }
fi

gh_api() {  # $1=메서드 $2=경로 [$3=JSON 몸] — 몸을 내고, 실패면 상태와 함께 멈춘다
    local out status
    out="$(curl -sS -X "$1" -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json" \
        ${3:+-H "Content-Type: application/json" --data "$3"} -w '\n%{http_code}' "$API$2")"
    status="${out##*$'\n'}"
    out="${out%$'\n'*}"
    [[ "$status" == 2* ]] || { echo "오류: GitHub $1 $2 → $status: ${out:0:300}" >&2; return 1; }
    printf '%s' "$out"
}
json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }

status="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $GITHUB_TOKEN" \
    "$API/repos/$AI_REPO/releases/tags/$tag")"
[[ "$status" == "404" ]] || { echo "오류: $AI_REPO 에 태그 $tag 의 릴리스가 이미 있습니다(또는 저장소에 닿지 않음: $status)" >&2; exit 1; }

echo "==> $name → $AI_REPO $tag"
split -b "$PART_SIZE" -d -a 2 "$bundle" "$work/$name.part-"
(cd "$work" && sha256sum -- "$name".part-* > SHA256SUMS)
whole="$(sha256sum "$bundle" | cut -d' ' -f1)"
printf '%s  %s\n' "$whole" "$name" >> "$work/SHA256SUMS"
parts=("$work/$name".part-*)
echo "    조각 ${#parts[@]}개 · 전체 지문 $whole"

body="$(python3 - "$work/MANIFEST.txt" "$name" "$whole" <<'PY'
import sys

manifest, name, whole = open(sys.argv[1], encoding="utf-8").read().strip(), sys.argv[2], sys.argv[3]
print(f"""서버의 뜻으로 찾기 AI 부품(Ollama · bge-m3 · llama.cpp 리랭커 · bge-reranker-v2-m3).

**받기** — 저장소의 `fetch-ai-bundle.bat`(Windows) · `fetch-ai-bundle.sh` 가 조각을 받아 지문을 맞추고 하나로 합친다.
합친 `{name}` 의 sha256: `{whole}`

**서버에서** — 앱 번들을 푼 폴더 안에 `tar xf {name} -C <앱 번들 폴더>` → `sudo ./deploy.sh update`.

```
{manifest}
```""")
PY
)"
payload="$(python3 -c 'import json, sys; print(json.dumps({"tag_name": sys.argv[1], "target_commitish": "main", "name": sys.argv[2], "body": sys.argv[3], "draft": True}))' \
    "$tag" "AI 꾸러미 $tag" "$body")"
release="$(gh_api POST "/repos/$AI_REPO/releases" "$payload")"
id="$(printf '%s' "$release" | json '["id"]')"
echo "    초안 릴리스 $id"

for file in "${parts[@]}" "$work/SHA256SUMS" "$work/MANIFEST.txt"; do
    size="$(stat -c %s "$file")"
    echo "    올리는 중: $(basename "$file") ($size 바이트)"
    # -T 는 파일을 흘려 보낸다(--data-binary @ 는 통째로 메모리에 싣는다).
    answer="$(curl -sS -X POST -T "$file" -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Content-Type: application/octet-stream" -w '\n%{http_code}' \
        "$UPLOADS/repos/$AI_REPO/releases/$id/assets?name=$(basename "$file")")"
    [[ "${answer##*$'\n'}" == 201 ]] || { echo "오류: 올리지 못했습니다(${answer##*$'\n'}) — 초안 릴리스 $id 를 지우고 다시: ${answer:0:300}" >&2; exit 1; }
    got="$(printf '%s' "${answer%$'\n'*}" | json '["size"]')"
    [[ "$got" == "$size" ]] || { echo "오류: $(basename "$file") 의 크기가 다릅니다($got ≠ $size)" >&2; exit 1; }
done

published="$(gh_api PATCH "/repos/$AI_REPO/releases/$id" '{"draft": false, "make_latest": "true"}')"
echo
echo "==> 게시: $(printf '%s' "$published" | json '["html_url"]')"
echo "    받는 PC 에서: $AI_REPO 저장소의 fetch-ai-bundle.bat(.sh)"
