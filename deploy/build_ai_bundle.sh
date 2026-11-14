#!/usr/bin/env bash
# 폐쇄망 서버용 **임베딩 꾸러미** — Ollama 런타임 + 임베딩 모델(bge-m3)(ADR 0050) + 리랭커(llama.cpp 서버 +
# bge-reranker-v2-m3, ADR 0072).
#
#   ./deploy/build_ai_bundle.sh           → release/ai-bundle-<Ollama 판>-<모델>.tar (약 5GB)
#   RERANK=0 ./deploy/build_ai_bundle.sh  → 리랭커 없이(약 2.7GB)
#   RERANK_CHECK=0 ./deploy/build_ai_bundle.sh → 바꾼 리랭커를 이 PC 에서 매겨 보지 않고(llama.cpp 를 못 띄우는 PC)
#
# 인터넷이 되는 PC 에서 만든다 — sudo 도 Ollama 도 필요 없다(모델은 Ollama 저장소에서 바로 받는다). 필요한 것:
# curl · zstd · python3(venv) · sha256sum. 리랭커 모델은 원본(BAAI 가중치, 커밋을 박아서)을 받아 llama.cpp 의
# 변환 스크립트(같은 판, 그 판이 밝힌 파이썬 패키지로 임시 환경)로 GGUF 로 바꿔 담는다. 서버에서는 릴리스 번들을 푼 폴더 안에 풀면(`ai/` 가 생긴다) `deploy.sh` 의
# install · update · ollama 가 인터넷 대신 이것을 쓴다.
#
#   tar xf ai-bundle-<…>.tar -C <번들 폴더>
#   (다른 자리에 풀었으면) sudo AI_BUNDLE=<풀어 둔 ai 폴더> ./deploy.sh ollama
#
# **앱 판과 무관하다** — 한 번 만들어 두고 서버를 새로 세울 때마다 쓴다(이미 깔린 서버는 다시 안 받는다).
# 릴리스에 붙이지 않는 것은 크기 때문이다(GitHub 릴리스 파일 하나 2GB 상한).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# **판은 deploy.sh 가 정한다** — 여기서 다시 적으면 인터넷으로 까는 서버와 꾸러미로 까는 서버의 판이 갈린다.
OLLAMA_VERSION="${OLLAMA_VERSION:-$(sed -n 's/^OLLAMA_VERSION="\${OLLAMA_VERSION:-\(v[0-9.]*\)}".*/\1/p' deploy/deploy.sh)}"
[[ -n "$OLLAMA_VERSION" ]] || { echo "오류: deploy.sh 에서 OLLAMA_VERSION 을 읽지 못했습니다" >&2; exit 1; }
MODEL="${EMBEDDING_MODEL:-bge-m3}"
REGISTRY="https://registry.ollama.ai"
# 리랭커(ADR 0072) — llama.cpp 판 · 모델 파일 이름도 deploy.sh 가 정한다.
RERANK="${RERANK:-1}"
RERANK_LLAMA_BUILD="${RERANK_LLAMA_BUILD:-$(sed -n 's/^RERANK_LLAMA_BUILD="\${RERANK_LLAMA_BUILD:-\(b[0-9]*\)}".*/\1/p' deploy/deploy.sh)}"
RERANK_MODEL_FILE="$(sed -n 's/^RERANK_MODEL_FILE="\(.*\)"$/\1/p' deploy/deploy.sh)"
# 바꾼 모델을 매겨 볼 짝 · 기준도 deploy.sh 의 것 — 서버의 배포가 보는 것과 같게.
RERANK_CHECK_BODY="$(sed -n "s/^RERANK_CHECK_BODY='\(.*\)'\$/\1/p" deploy/deploy.sh)"
RERANK_CHECK_MARGIN="$(sed -n 's/^RERANK_CHECK_MARGIN=\([0-9.]*\)$/\1/p' deploy/deploy.sh)"
[[ -n "$RERANK_LLAMA_BUILD" && -n "$RERANK_MODEL_FILE" && -n "$RERANK_CHECK_BODY" \
        && -n "$RERANK_CHECK_MARGIN" ]] \
    || { echo "오류: deploy.sh 에서 리랭커 판 · 모델 파일 · 확인 짝을 읽지 못했습니다" >&2; exit 1; }
# 이 PC 에서 llama.cpp 를 띄울 수 없을 때만 0 — 그러면 서버의 배포가 처음 매겨 본다.
RERANK_CHECK="${RERANK_CHECK:-1}"
RERANK_REPO="BAAI/bge-reranker-v2-m3"
RERANK_REV="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"   # 2026-11-14 받은 커밋 — 실측(ADR 0072)이 이 가중치

for tool in curl zstd python3 sha256sum; do
    command -v "$tool" >/dev/null || { echo "오류: $tool 이 없습니다" >&2; exit 1; }
done

# 모델 이름 — `bge-m3` 은 `library/bge-m3:latest` 다.
name="${MODEL%%:*}"; tag="${MODEL#*:}"
[[ "$tag" == "$MODEL" ]] && tag="latest"
[[ "$name" == */* ]] || name="library/$name"

STAGE="release/ai-bundle"
OUT="release/ai-bundle-${OLLAMA_VERSION}-${MODEL//[:\/]/-}.tar"
rm -rf "$STAGE"
mkdir -p "$STAGE/ai/models/blobs" "$STAGE/ai/models/manifests/registry.ollama.ai/$name"

echo "==> [1/4] Ollama $OLLAMA_VERSION (linux-amd64)"
# 받은 것은 .tar.zst 다 — 폐쇄망 서버에는 zstd 가 없을 수 있어 gzip 으로 다시 묶는다.
curl -fL --retry 3 -o "$STAGE/ollama.tar.zst" \
    "https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION/ollama-linux-amd64.tar.zst"
zstd -dc "$STAGE/ollama.tar.zst" | gzip -c > "$STAGE/ai/ollama-linux-amd64.tgz"
rm -f "$STAGE/ollama.tar.zst"
echo "    $(du -h "$STAGE/ai/ollama-linux-amd64.tgz" | cut -f1)"

echo "==> [2/4] 모델 $MODEL ($name:$tag)"
# Ollama 저장소의 모양 그대로 — manifests/registry.ollama.ai/<이름>/<태그> + blobs/sha256-<값>. 서버의
# 모델 폴더에 그대로 복사하면 Ollama 가 읽는다.
manifest="$STAGE/ai/models/manifests/registry.ollama.ai/$name/$tag"
curl -fsSL -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    -o "$manifest" "$REGISTRY/v2/$name/manifests/$tag"
python3 - "$manifest" <<'PY' | while read -r digest; do
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for layer in [manifest["config"], *manifest["layers"]]:
    print(layer["digest"])
PY
    blob="$STAGE/ai/models/blobs/${digest/:/-}"
    curl -fL --retry 3 -o "$blob" "$REGISTRY/v2/$name/blobs/$digest"
    [[ "sha256:$(sha256sum "$blob" | cut -d' ' -f1)" == "$digest" ]] \
        || { echo "오류: 받은 조각의 해시가 다릅니다: $digest" >&2; exit 1; }
    echo "    $digest $(du -h "$blob" | cut -f1)"
done

echo "==> [3/4] 리랭커 (llama.cpp $RERANK_LLAMA_BUILD · $RERANK_REPO@${RERANK_REV:0:12})"
if [[ "$RERANK" == "1" ]]; then
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    mkdir -p "$STAGE/ai/rerank"
    # llama.cpp 실행 파일 — GPU(CUDA 12.8) · CUDA 런타임 · CPU. 지문은 그 릴리스가 밝힌 sha256 과 견준다.
    curl -fsSL -o "$work/release.json" \
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$RERANK_LLAMA_BUILD"
    for file in "llama-$RERANK_LLAMA_BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz" \
                "cudart-llama-$RERANK_LLAMA_BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz" \
                "llama-$RERANK_LLAMA_BUILD-bin-ubuntu-x64.tar.gz"; do
        digest="$(python3 - "$work/release.json" "$file" <<'PY'
import json
import sys

for asset in json.load(open(sys.argv[1], encoding="utf-8"))["assets"]:
    if asset["name"] == sys.argv[2]:
        print(asset.get("digest") or "")
PY
)"
        [[ "$digest" == sha256:* ]] || { echo "오류: $file 의 지문을 릴리스에서 못 찾았습니다" >&2; exit 1; }
        curl -fL --retry 3 -o "$STAGE/ai/rerank/$file" \
            "https://github.com/ggml-org/llama.cpp/releases/download/$RERANK_LLAMA_BUILD/$file"
        [[ "sha256:$(sha256sum "$STAGE/ai/rerank/$file" | cut -d' ' -f1)" == "$digest" ]] \
            || { echo "오류: $file 의 해시가 다릅니다" >&2; exit 1; }
        echo "    $file $(du -h "$STAGE/ai/rerank/$file" | cut -f1)"
    done
    # 원본 모델 — 커밋을 박아서. 가중치의 지문은 Hugging Face 가 밝힌 sha256 과 견준다.
    mkdir -p "$work/model"
    curl -fsSL -o "$work/model.json" \
        "https://huggingface.co/api/models/$RERANK_REPO/revision/$RERANK_REV?blobs=true"
    for file in config.json model.safetensors sentencepiece.bpe.model special_tokens_map.json \
                tokenizer.json tokenizer_config.json; do
        curl -fL --retry 3 -o "$work/model/$file" \
            "https://huggingface.co/$RERANK_REPO/resolve/$RERANK_REV/$file"
    done
    want="$(python3 - "$work/model.json" <<'PY'
import json
import sys

for one in json.load(open(sys.argv[1], encoding="utf-8"))["siblings"]:
    if one["rfilename"] == "model.safetensors":
        print((one.get("lfs") or {}).get("sha256", ""))
PY
)"
    [[ -n "$want" && "$(sha256sum "$work/model/model.safetensors" | cut -d' ' -f1)" == "$want" ]] \
        || { echo "오류: 리랭커 가중치의 해시가 다릅니다" >&2; exit 1; }
    # 같은 판의 변환 스크립트로 — 그 판이 밝힌 파이썬 패키지로 임시 환경을 만든다(torch 는 CPU 판).
    # **sentencepiece 는 0.2.2 로 박는다** — 그 판의 목록(`sentencepiece<0.3.0` · `protobuf<5`)대로 받으면
    # 0.2.3 이 오는데, 0.2.3 은 protobuf 7 로 생성돼(`runtime_version`) protobuf 4 에서 변환이 떨어진다
    # (2026-11-14). 0.2.2 는 목록의 범위 안이고 Python 3.10 ~ 3.14 판이 있다.
    curl -fsSL -o "$work/src.tar.gz" \
        "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$RERANK_LLAMA_BUILD.tar.gz"
    tar -C "$work" -xzf "$work/src.tar.gz"
    src="$work/llama.cpp-$RERANK_LLAMA_BUILD"
    python3 -m venv "$work/venv"
    printf 'sentencepiece==0.2.2\n' > "$work/constraints.txt"
    "$work/venv/bin/pip" install -q --extra-index-url https://download.pytorch.org/whl/cpu \
        -r "$src/requirements/requirements-convert_hf_to_gguf.txt" -c "$work/constraints.txt"
    PYTHONPATH="$src/gguf-py" "$work/venv/bin/python" "$src/convert_hf_to_gguf.py" "$work/model" \
        --outtype f16 --outfile "$STAGE/ai/rerank/$RERANK_MODEL_FILE"
    # **바꾼 모델을 이 PC 에서 한 번 매겨 본다** — 꾸러미의 CPU 판으로, 서버 배포의 확인(`check_rerank`)과
    # 같은 짝 · 같은 기준. 변환이 잘못된 모델(분류 머리가 빠지면 점수가 다 0 근처)을 폐쇄망 서버에 들고
    # 간 뒤에야 알면 밖에서 다시 만들어 들고 가야 한다.
    if [[ "$RERANK_CHECK" == "1" ]]; then
        mkdir -p "$work/cpu"
        tar -C "$work/cpu" --strip-components=1 \
            -xzf "$STAGE/ai/rerank/llama-$RERANK_LLAMA_BUILD-bin-ubuntu-x64.tar.gz"
        port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
        LD_LIBRARY_PATH="$work/cpu" "$work/cpu/llama-server" -m "$STAGE/ai/rerank/$RERANK_MODEL_FILE" \
            --reranking --host 127.0.0.1 --port "$port" -c 4096 --parallel 1 -b 4096 -ub 4096 \
            > "$work/server.log" 2>&1 &
        server=$!
        trap 'kill "$server" 2>/dev/null || true; rm -rf "$work"' EXIT
        answer=""
        for _ in $(seq 1 120); do
            if curl -sf --max-time 2 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
                answer="$(curl -sf --max-time 120 "http://127.0.0.1:$port/v1/rerank" \
                    -H 'Content-Type: application/json' -d "$RERANK_CHECK_BODY" | python3 -c 'import json, sys
scores = {one["index"]: one["relevance_score"] for one in json.load(sys.stdin)["results"]}
ok = scores[0] - scores[1] >= float(sys.argv[1])
print("ok" if ok else "flat", round(scores[0], 2), round(scores[1], 2))' "$RERANK_CHECK_MARGIN")" \
                    || answer=""
                break
            fi
            kill -0 "$server" 2>/dev/null || break
            sleep 1
        done
        kill "$server" 2>/dev/null || true
        wait "$server" 2>/dev/null || true
        if [[ "$answer" != ok* ]]; then
            echo "오류: 바꾼 리랭커를 매겨 보지 못했거나 점수가 이상합니다(${answer:-답 없음}) — 맞는 짝이" \
                "엉뚱한 짝보다 날 점수로 $RERANK_CHECK_MARGIN 이상 높아야 합니다. llama-server 의 끝:" >&2
            tail -n 5 "$work/server.log" >&2 || true
            echo "  (이 PC 에서 llama.cpp 를 띄울 수 없으면 RERANK_CHECK=0 — 서버의 배포가 처음 매겨 본다)" >&2
            exit 1
        fi
        echo "    매겨 봄(CPU): 맞는 짝 ${answer#ok }(날 점수 · 앞이 맞는 짝)"
    fi
    (cd "$STAGE/ai/rerank" && sha256sum -- * > SHA256SUMS)
    echo "    $RERANK_MODEL_FILE $(du -h "$STAGE/ai/rerank/$RERANK_MODEL_FILE" | cut -f1)"
else
    echo "    건너뜀(RERANK=0)"
fi

echo "==> [4/4] 묶기"
cat > "$STAGE/ai/MANIFEST.txt" <<INFO
임베딩 꾸러미 (ADR 0050 · 0072)
ollama : $OLLAMA_VERSION linux-amd64
model  : $MODEL ($name:$tag)
rerank : $([[ "$RERANK" == "1" ]] && echo "llama.cpp $RERANK_LLAMA_BUILD · $RERANK_REPO@$RERANK_REV → $RERANK_MODEL_FILE" || echo "없음")
built  : $(date -u +%Y-%m-%dT%H:%M:%SZ)
INFO
# 내용이 이미 눌린 것(.tgz · 모델 가중치)이라 다시 누르지 않는다.
tar -C "$STAGE" -cf "$OUT" ai
rm -rf "$STAGE"
echo
echo "==> $OUT ($(du -h "$OUT" | cut -f1))"
echo "    서버에서: tar xf $(basename "$OUT") -C <릴리스 번들 폴더>  →  sudo ./deploy.sh update (또는 ollama)"
