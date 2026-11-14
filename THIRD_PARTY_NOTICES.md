# 제3자 구성 요소 고지 · Third-Party Notices

Standard Platform 자체는 [LICENSE](LICENSE)(무단 사용 금지)를 따른다. 이 소프트웨어가 쓰거나 함께 배포하는 제3자 구성
요소는 **각자의 라이선스**를 따르며, LICENSE 는 그것들에 적용되지 않는다. 이 문서는 그 구성 요소와 라이선스 전문이
있는 자리를 적는다(2026-11-08 점검 — 막는 라이선스는 없었다: GPL · AGPL · SSPL · 비상업 · 라이선스 없음이 앱이 쓰는
라이브러리에 없다). 2026-11-11 부터 이미지에 **따로 도는 프로그램** Neo4j 무료판(GPLv3)과 OpenJDK(GPLv2 + Classpath
Exception)가 든다 — 그래프 사본(ADR 0069). 앱이 링크하는 것이 아니라 127.0.0.1 의 포트로 말을 거는 별도 프로세스다.

## 어디에 전문이 있나

| 구성 요소 | 라이선스(대부분) | 전문 · 목록이 있는 자리 |
| --- | --- | --- |
| 화면 번들(React · Radix · TanStack · d3 · plotly.js · lucide · Geist 글꼴 …, 약 190개) | MIT · ISC · Apache-2.0 · BSD · 0BSD, 글꼴은 SIL OFL-1.1 | 빌드가 만드는 `frontend/dist/THIRD_PARTY_NOTICES.txt`(이미지 안 `/opt/app/frontend/dist/`) — 번들에 실제로 든 패키지마다 이름 · 판 · 전문 |
| API 문서 화면(`/api/docs`)의 Swagger UI 5.33.3 — 폐쇄망에서도 뜨게 서버가 직접 준다 | Apache-2.0, 스크립트에 묶인 것은 MIT 등 | `backend/app/static/swagger-ui/` 의 `LICENSE` · `NOTICE` · `swagger-ui-bundle.js.LICENSE.txt`(이미지 안 `/opt/app/backend/app/static/swagger-ui/`) |
| 백엔드 파이썬(FastAPI · SQLAlchemy · pydantic · numpy · scipy · rdflib · psycopg …, 약 46개) | MIT · BSD · Apache-2.0 · PSF, psycopg 는 LGPL-3.0, certifi 는 MPL-2.0 | 이미지 안 venv 의 `*.dist-info/licenses/` — 휠이 전문을 함께 싣는다 |
| 밤 그래프 계산의 CPU 엔진 NetworKit 11.3 · tabulate(ADR 0067) | MIT | 이미지 안 venv 의 `*.dist-info/licenses/` |
| **GPU 판 번들만** — 밤 그래프 계산의 GPU 엔진 pylibcugraph · rmm · raft · cuvs · kvikio(RAPIDS 26.10) · cupy · cuda-bindings | Apache-2.0 · MIT | 이미지 안 venv 의 `*.dist-info/licenses/` |
| **GPU 판 번들만** — CUDA 12.9 런타임 라이브러리(`nvidia-*` 휠: cudart · cublas · cusparse · cusolver · cufft · curand · nvrtc · nvjitlink · cufile · nccl) | **NVIDIA 독점(CUDA EULA)** — 무료, 앱에 담아 배포는 조건부 허용 | 각 휠의 `*.dist-info/licenses/License.txt`(EULA 전문 · 배포 가능한 파일은 그 Attachment A) |
| 그래프 사본(ADR 0069) — Neo4j 무료판 2026.10.1(이미지 안 `/opt/neo4j`, 고치지 않고 그대로) | **GPL-3.0**(Neo4j Community Edition) | `/opt/neo4j/LICENSE.txt` · `ThirdPartyLicenses.txt` |
| 그래프 사본의 APOC Core(Neo4j 압축본의 `labs/` 에 든 것 그대로) | Apache-2.0 | `/opt/neo4j/labs/` 의 `LICENSE` |
| 그래프 사본을 돌리는 OpenJDK 21 JRE(Ubuntu 패키지 `openjdk-21-jre-headless`) | GPL-2.0 + Classpath Exception | `/usr/share/doc/openjdk-21-jre-headless/copyright` |
| SPARQL 사본(ADR 0070)의 pyoxigraph 0.5.13(Oxigraph — 안에 RocksDB 를 묶었다) | MIT OR Apache-2.0(RocksDB 는 Apache-2.0) | 이미지 안 venv 의 `pyoxigraph-*.dist-info/METADATA`(License) · `sboms/`(묶은 Rust 부품과 그 라이선스 목록) |
| 그래프 사본의 파이썬 드라이버 neo4j 6.4.1 · pytz | Apache-2.0 · MIT | 이미지 안 venv 의 `*.dist-info/licenses/` |
| MCP 서버 · 정제 키트의 파이썬 휠(mcp · httpx · openpyxl …) | MIT · BSD · Apache-2.0 · PSF, certifi 는 MPL-2.0 | 각 휠의 `*.dist-info/licenses/` · 키트의 내장 CPython 은 PSF-2.0 |
| 이미지(SIF)의 OS 패키지(Ubuntu 24.04 · python3.12 · libpq5 …) | GPL · LGPL · MIT 등 패키지마다 | 릴리스 번들의 `os-packages.txt`(패키지 · 판) — 각 패키지의 `/usr/share/doc/<패키지>/copyright` |
| 번들에 함께 넣는 `apptainer_debs/` · `pgvector_debs/` | Apptainer BSD-3, fuse-overlayfs · squashfs-tools 등 GPL-2, pgvector PostgreSQL License | 각 .deb 안의 `copyright` |
| 뜻으로 찾기의 Ollama · bge-m3(사내 서버로만 옮기는 AI 꾸러미) | Ollama MIT(안의 CUDA 런타임은 NVIDIA CUDA EULA), bge-m3 MIT | 꾸러미 안의 각 라이선스 파일 |
| 리랭커의 llama.cpp `llama-server`(공식 릴리스 실행 파일, 같은 AI 꾸러미) · bge-reranker-v2-m3(원본 가중치를 GGUF 로 바꾼 것) | llama.cpp MIT(GPU 판에 함께 담는 CUDA 런타임 `cudart-*` 는 NVIDIA CUDA EULA), bge-reranker-v2-m3 Apache-2.0 | 꾸러미 `ai/rerank/` 의 각 묶음 안 `LICENSE` · 모델은 Hugging Face `BAAI/bge-reranker-v2-m3` 의 카드 |

## 조건이 붙는 것

- **psycopg(LGPL-3.0)** — 고치지 않고 파이썬 import 로만 쓴다(venv 에서 바꿔 끼울 수 있다). 고치면 고친 소스를 낸다.
- **certifi(MPL-2.0)** — 그 파일을 고치지 않는다.
- **numpy · scipy 휠의 libgfortran · libquadmath** — GPL-3.0 + GCC Runtime Library Exception · LGPL-2.1 — 예외 조항이 이런
  쓰임을 허락한다.
- **GPL · LGPL OS 패키지 · apptainer 부속** — 따로 도는 프로그램을 함께 담은 것(단순 집합)이다. 소스는 Ubuntu 저장소
  (<https://snapshot.ubuntu.com> · `apt-get source <패키지>`)와 Apptainer PPA(<https://launchpad.net/~apptainer>)에 있다 —
  판은 `os-packages.txt` 와 각 .deb 이름에 있다.
- **Geist 글꼴(OFL-1.1)** — 소프트웨어와 함께 묶는 것은 되고, 글꼴만 따로 팔지 않는다.
- **Neo4j 무료판(GPL-3.0)** — 고치지 않고 받은 압축본 그대로(판과 지문을 이미지 정의에 박는다) 따로 도는 프로그램으로
  담는다. 앱은 그것을 링크하지 않고 bolt 포트로 말을 건다(드라이버는 Apache-2.0). GPL-3.0 §6 에 따라 그 판의 소스를
  받을 수 있는 자리를 알린다: <https://github.com/neo4j/neo4j>(태그 `2026.10.1`) — 요청하면 같은 판의 소스를 함께
  건넨다. 상표 「Neo4j」 는 Neo4j, Inc. 의 것이다. 상용판(Enterprise) · GDS 는 담지 않는다(GDS 의 받은 jar 에 「평가
  목적 · 운영 금지」 조항이 함께 있었다).
- **OpenJDK(GPL-2.0 + Classpath Exception)** — Ubuntu 패키지 그대로. 소스는 `apt-get source openjdk-21`.
- **CUDA 런타임(NVIDIA CUDA EULA, GPU 판만)** — 공개 소프트웨어가 아니다. EULA 1.1.2 의 조건으로 앱에 담아 배포한다:
  앱이 그것 말고도 기능이 있고, 그 파일은 이 앱만 쓰며, 고치지 않고(리눅스 판은 그대로 다시 배포 가능), 이 앱의 사용
  조건이 EULA(NVIDIA 의 지식재산 보호 포함)와 어긋나지 않는다. CPU 판 번들에는 들어 있지 않다.

법률 자문이 아니다 — 라이선스 판단이 필요하면 사내 법무에 확인한다.
