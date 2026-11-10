# 제3자 구성 요소 고지 · Third-Party Notices

Standard Platform 자체는 [LICENSE](LICENSE)(무단 사용 금지)를 따른다. 이 소프트웨어가 쓰거나 함께 배포하는 제3자 구성
요소는 **각자의 라이선스**를 따르며, LICENSE 는 그것들에 적용되지 않는다. 이 문서는 그 구성 요소와 라이선스 전문이
있는 자리를 적는다(2026-11-08 점검 — 막는 라이선스는 없었다: GPL · AGPL · SSPL · 비상업 · 라이선스 없음이 앱이 쓰는
라이브러리에 없다).

## 어디에 전문이 있나

| 구성 요소 | 라이선스(대부분) | 전문 · 목록이 있는 자리 |
| --- | --- | --- |
| 화면 번들(React · Radix · TanStack · d3 · plotly.js · lucide · Geist 글꼴 …, 약 190개) | MIT · ISC · Apache-2.0 · BSD · 0BSD, 글꼴은 SIL OFL-1.1 | 빌드가 만드는 `frontend/dist/THIRD_PARTY_NOTICES.txt`(이미지 안 `/opt/app/frontend/dist/`) — 번들에 실제로 든 패키지마다 이름 · 판 · 전문 |
| API 문서 화면(`/api/docs`)의 Swagger UI 5.33.3 — 폐쇄망에서도 뜨게 서버가 직접 준다 | Apache-2.0, 스크립트에 묶인 것은 MIT 등 | `backend/app/static/swagger-ui/` 의 `LICENSE` · `NOTICE` · `swagger-ui-bundle.js.LICENSE.txt`(이미지 안 `/opt/app/backend/app/static/swagger-ui/`) |
| 백엔드 파이썬(FastAPI · SQLAlchemy · pydantic · numpy · scipy · rdflib · psycopg …, 약 46개) | MIT · BSD · Apache-2.0 · PSF, psycopg 는 LGPL-3.0, certifi 는 MPL-2.0 | 이미지 안 venv 의 `*.dist-info/licenses/` — 휠이 전문을 함께 싣는다 |
| 밤 그래프 계산의 CPU 엔진 NetworKit 11.3 · tabulate(ADR 0067) | MIT | 이미지 안 venv 의 `*.dist-info/licenses/` |
| **GPU 판 번들만** — 밤 그래프 계산의 GPU 엔진 pylibcugraph · rmm · raft · cuvs · kvikio(RAPIDS 26.10) · cupy · cuda-bindings | Apache-2.0 · MIT | 이미지 안 venv 의 `*.dist-info/licenses/` |
| **GPU 판 번들만** — CUDA 12.9 런타임 라이브러리(`nvidia-*` 휠: cudart · cublas · cusparse · cusolver · cufft · curand · nvrtc · nvjitlink · cufile · nccl) | **NVIDIA 독점(CUDA EULA)** — 무료, 앱에 담아 배포는 조건부 허용 | 각 휠의 `*.dist-info/licenses/License.txt`(EULA 전문 · 배포 가능한 파일은 그 Attachment A) |
| MCP 서버 · 정제 키트의 파이썬 휠(mcp · httpx · openpyxl …) | MIT · BSD · Apache-2.0 · PSF, certifi 는 MPL-2.0 | 각 휠의 `*.dist-info/licenses/` · 키트의 내장 CPython 은 PSF-2.0 |
| 이미지(SIF)의 OS 패키지(Ubuntu 24.04 · python3.12 · libpq5 …) | GPL · LGPL · MIT 등 패키지마다 | 릴리스 번들의 `os-packages.txt`(패키지 · 판) — 각 패키지의 `/usr/share/doc/<패키지>/copyright` |
| 번들에 함께 넣는 `apptainer_debs/` · `pgvector_debs/` | Apptainer BSD-3, fuse-overlayfs · squashfs-tools 등 GPL-2, pgvector PostgreSQL License | 각 .deb 안의 `copyright` |
| 뜻으로 찾기의 Ollama · bge-m3(사내 서버로만 옮기는 AI 꾸러미) | Ollama MIT(안의 CUDA 런타임은 NVIDIA CUDA EULA), bge-m3 MIT | 꾸러미 안의 각 라이선스 파일 |

## 조건이 붙는 것

- **psycopg(LGPL-3.0)** — 고치지 않고 파이썬 import 로만 쓴다(venv 에서 바꿔 끼울 수 있다). 고치면 고친 소스를 낸다.
- **certifi(MPL-2.0)** — 그 파일을 고치지 않는다.
- **numpy · scipy 휠의 libgfortran · libquadmath** — GPL-3.0 + GCC Runtime Library Exception · LGPL-2.1 — 예외 조항이 이런
  쓰임을 허락한다.
- **GPL · LGPL OS 패키지 · apptainer 부속** — 따로 도는 프로그램을 함께 담은 것(단순 집합)이다. 소스는 Ubuntu 저장소
  (<https://snapshot.ubuntu.com> · `apt-get source <패키지>`)와 Apptainer PPA(<https://launchpad.net/~apptainer>)에 있다 —
  판은 `os-packages.txt` 와 각 .deb 이름에 있다.
- **Geist 글꼴(OFL-1.1)** — 소프트웨어와 함께 묶는 것은 되고, 글꼴만 따로 팔지 않는다.
- **CUDA 런타임(NVIDIA CUDA EULA, GPU 판만)** — 공개 소프트웨어가 아니다. EULA 1.1.2 의 조건으로 앱에 담아 배포한다:
  앱이 그것 말고도 기능이 있고, 그 파일은 이 앱만 쓰며, 고치지 않고(리눅스 판은 그대로 다시 배포 가능), 이 앱의 사용
  조건이 EULA(NVIDIA 의 지식재산 보호 포함)와 어긋나지 않는다. CPU 판 번들에는 들어 있지 않다.

법률 자문이 아니다 — 라이선스 판단이 필요하면 사내 법무에 확인한다.
