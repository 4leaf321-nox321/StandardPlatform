# StandardPlatform

**사내 플랫폼의 공통 틀.** 도메인은 비어 있고, 도메인이 무엇이든 매번 다시 만들게
되는 것만 들어 있다.

    로그인 · 가입 승인 · 부서(조직도) · 권한 · 사이드바 · 공지 · 알림 · 감사 · 서버 상태

포크해서 도메인을 얹는다. **이 저장소에 도메인을 넣지 않는다** — 넣는 순간 다음
플랫폼은 그것을 지우면서 시작하고, 지우다 보면 공통이던 것까지 함께 갈라진다.

개발 규칙은 [AGENTS.md](AGENTS.md) 가 정본이다.

---

## 지금 되는 것

| 영역 | 들어 있는 것 |
| --- | --- |
| 인증 | 로그인, 세션 회전(refresh httpOnly 쿠키), 강제 비밀번호 변경, 개인 액세스 토큰(PAT) |
| 계정 | 셀프 가입 → 관리자 승인/거절, 정지·활성, 임시 비밀번호 발급, 시스템 관리자 지정 |
| 부서 | 트리형 조직도(상위/순서/보관), 멤버와 역할, 삭제 전 참조 확인, CSV 내보내기 |
| 권한 | 시스템 역할 × 부서 역할 두 축, 부서 소유 자산 판정 헬퍼 |
| 화면 | 사이드바·헤더·테마(라이트/다크)·부서 전환·shadcn 프리미티브 |
| 첨부 | 파일 올리기·내려받기, 내용 해시로 중복 제거, 부서 단위 권한 |
| 운영 | 공지(팝업 포함), 알림, 감사 로그, 접근 로그, 서버 상태 화면 |
| 배포 | Apptainer 이미지·systemd 유닛·설치/갱신/롤백/백업/복구 스크립트, GitHub Actions |
| 온톨로지 | 타입·속성·관계를 **데이터로 정의**하면 사이드바·목록·상세·트리가 생긴다. 데이터는 표에 붙여넣거나(엑셀 범위 그대로) 파일로 — 계획을 보고 적용 |
| MCP | 기계가 정의를 읽고 채우는 길 — 스키마·찾기·해소·가져오기(작업 → 사람 확정)·객체·관계·통계·묶음·SPARQL |
| 정제 파이프라인 | 원천을 AI(Claude Code · Gemini CLI)로 정제한 결과물 묶음을 검증 → 한 번에 미리 보기 → 전부 아니면 무로 적재 — [pipeline/](pipeline/README.md) |
| 웹훅 | 커밋된 변경을 바깥에 알린다 — 감사 기록이 곧 이벤트, 서명·재시도·보낸 기록. 보내는 것은 작업 워커라 재시작에도 밀린 것이 살아남는다 |
| 데이터 소스 | 바깥 시스템(OData v4/v2 · REST JSON · CSV/Excel/JSON 파일)에서 읽어 온톨로지를 채운다 — 칸·값 대응, 별칭으로 같은 객체 다시 찾기, 계획 먼저, 타이머 |
| 작업 워커 | 일괄 입력 · 내보내기 · 묶음 · 동기화처럼 오래 걸리는 일을 요청 밖에서 — 계획 → 사람 확정 → 적용, 진행률 · 취소 · 멎은 작업 되살리기. 두 서버에 하나씩 떠도 같은 것을 두 번 안 집는다 |
| 규약 | 오류 봉투 + 요청 ID, 로그, 페이지네이션(서버 상한 + 화면), 구조 시험 |

## 안 들어 있는 것 (일부러)

- **도메인 표와 화면.** 그것이 각 플랫폼이 만드는 것이다.
- **휴지통.** soft delete 는 하지만 복구 화면은 없다.
- **런타임 설정 화면.** `config.py` 에 3단 fallback 의 DB 단 자리만 잡혀 있다.
- **로그 보존 정책.** 접근 로그·감사 로그를 지우는 쪽이 없다 — 운영 들어가기 전에 정한다.
  (작업은 워커가 치운다 — 파일 7일 · 끝난 기록 30일.)

위 셋은 **필요해진 플랫폼에서 만들고 되가져오는** 편이 낫다. 안 쓰는 곳에서는
지워야 할 코드가 되기 때문이다([ADR 0001](docs/adr/0001-공통-틀과-도메인의-경계.md)).

> **작업 큐·워커는 여기 있다가 올라왔다.** 「필요한 플랫폼에서 얹는다」 로 뒀다가, 파일을 올려
> 바꾸는 것이 이 틀의 일반적인 쓰임이 되면서 코어로 들였다 — 5,000행 상한과 「같은 파일을 두 번
> 올리기」 가 그 경계 때문에 남아 있던 것이었다([docs/작업-워커-설계.md](docs/작업-워커-설계.md)).

---

## 시작하기

### 준비물

- Python 3.12 (`.python-version`) — 배포 이미지가 ubuntu:24.04 의 `python3.12` 를 쓴다
- Node 20 (`.node-version`)
- PostgreSQL
- Apptainer — 배포 이미지를 만들 때만 필요하다

### 백엔드

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# DB 를 만든다. 이름은 branding.py 의 APP_SLUG 다 — 이 틀에서는 standardplatform.
# 시험용(_test)까지 둘을 만든다.
createdb -U postgres standardplatform
createdb -U postgres standardplatform_test

# .env 를 만들고 접속 정보(사용자·비밀번호)를 자기 것으로 고친다.
# **BOM 없이 UTF-8** 로 저장한다 — BOM 이 붙으면 첫 줄 키가 조용히 무시된다.
cp .env.example .env

.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_install.py --email admin --name 관리자
```

시드가 **임시 비밀번호를 화면에 한 번만** 찍는다. 그 계정은 첫 로그인에서 비밀번호
변경이 강제된다.

```bash
.venv/bin/python run.py                  # 8041 (개발). 운영은 8040. mcp_server/venv 가 있으면 MCP(8042)도 함께
```

포트가 잡혀 있으면 개발 모드가 그것을 말하며 멈춘다. 누가 쥐고 있는지는
`ss -ltnp 'sport = :8041'` 로 본다. **운영에서는 이 검사를 하지 않는다** — 그 자리는
systemd 의 `Restart` 가 맡는다.

### 프론트엔드

```bash
cd frontend
npm install
npm run dev                              # 5210
```

### API 타입을 생성으로 갈아탄다 (첫 설치에서 한 번)

`src/shared/api/types.ts` 는 **손으로 적힌 임시 타입**이다. 저장소를 막 클론한
사람도 빌드가 되게 하려고 둔 것이고, 정본은 서버다.

```bash
(cd backend  && .venv/bin/python scripts/export_openapi.py)
(cd frontend && npm run api:types)
```

그러면 `src/shared/api/schema.d.ts` 가 생긴다. 그다음 `types.ts` 의 각 타입을
이렇게 바꾸고 손 타입을 지운다:

```ts
import type { components } from '@/shared/api/schema'
export type CurrentUser = components['schemas']['UserOut']
```

**두 벌을 남기지 않는다.** 남기면 어느 쪽이 정본인지 알 수 없어지고, 그때부터
갈린다.

---

## 도메인을 데이터로 얹는다 (온톨로지)

**[ADR 0005](docs/adr/0005-온톨로지-메타모델.md)** 가 결정이고
**[설계 문서](docs/온톨로지-메타모델-설계.md)** 가 절차다.

    묶음을 정의하면   -> 사이드바에 묶음이 생기고
    타입을 정의하면   -> 그 묶음 안에 목록·상세 화면이 생기고
    속성·관계를 정의하면 -> 그 화면의 폼과 관계 편집기·트리가 생긴다

관리 → 온톨로지에서 사람이 채우고, [mcp_server](mcp_server/README.md) 로 기계가
채운다. 쓰는 사람을 위한 안내는 [docs/사용자-안내서.md](docs/사용자-안내서.md). **정의를 바꾸는 일은 배포가 아니다** — 그래서 포크는 특수 기능에만 남는다.

## 새 플랫폼 만들기

**[docs/새-플랫폼-만들기.md](docs/새-플랫폼-만들기.md) 가 절차의 정본이다.**
복사부터 도메인 모듈 하나를 붙이는 데까지 순서대로 적혀 있다.

요약하면 이렇다.

### 이름은 네 자리에서만 바꾼다

```python
# backend/app/branding.py   (frontend/src/shared/branding.ts 도 같은 값)
APP_NAME = "PartTrace"      # 화면에 보이는 이름
APP_SLUG = "parttrace"      # 기계가 읽는 이름 — 소문자·숫자 한 덩어리
APP_TAGLINE = "부품 이력 추적"
ERROR_PREFIX = "PTR"        # 오류 코드가 PTR-AUTH-0001 이 된다
```

나머지 둘은 `frontend/index.html` 의 `<title>` 과 `backend/app/config.py` 의
`port` 다. **포트만 손으로 정한다** — 조직 전체에서 안 겹치게 배정하는 값이라
이름에서 나올 수 없다(10씩 벌린다: MatNexus 8010 · TestScope 8020 · CrossAXTF 8030 · 이 틀 8040).

Apptainer 는 **호스트 네트워크**를 쓴다. 컨테이너 안의 포트가 곧 호스트의 포트라
겹치면 나중에 뜬 쪽이 그냥 못 뜬다 — 포트 매핑으로 덮을 자리가 없다.

### APP_SLUG 하나가 다섯을 만든다

DB 이름 · refresh 쿠키 이름 · 개인 토큰 표식 · systemd 유닛 이름 · 설치 경로는
**따로 안 적는다.**

| `APP_SLUG = "parttrace"` | 결과 |
| --- | --- |
| DB | `parttrace` (시험은 `parttrace_test` 로 자동 파생) |
| refresh 쿠키 | `parttrace_refresh` |
| 개인 토큰 | `parttrace_pat_...` |
| systemd 유닛 | `parttrace.service` |
| 설치 경로 | `/home/<운영계정>/apps/parttrace` |

따로 적으면 언젠가 하나가 안 바뀌는데, **전부 조용히 망가진다**: 같은 DB 를 보면
오류 없이 남의 `users` 표를 읽고(공통 틀에서 나온 표라 이름이 같다), 같은 쿠키
이름이면 두 플랫폼이 번갈아 로그아웃되고, 같은 토큰 표식이면 「형식은 맞는데
인증이 안 되는」 상태가 오타와 구별되지 않는다.

**배포 스크립트에는 제품 이름이 한 글자도 없다.** `build_bundle.sh` 가 `branding.py`
를 읽어 번들의 `BUILD_INFO` 에 적고, `deploy.sh` 가 그것을 읽는다. 그래서 포크한
사람이 배포 쪽에서 고칠 자리가 없다 — 고칠 자리가 있으면 언젠가 빠뜨리고, 빠뜨린
날 새 플랫폼이 **옆 플랫폼의 DB 를 마이그레이션한다.**

브랜딩을 한쪽만 바꿨으면 **서버 화면이 말해 준다**(`/admin/server`).

### 도메인은 세 자리에 끼운다

`app/main.py` 의 `_register_extensions()` 한 곳에서 등록한다. **등록하지 않으면
안 뜬다** — 특히 부서 참조가 그렇다: 안 걸면 부서를 지울 때 그 표가 목록에 안
나타나고, 사람은 아무것도 안 걸린 줄 안다.

```python
extensions.register_stats(parts_services.stats)                     # 서버의 「쌓인 것」
extensions.register_maintenance(parts_services.maintenance)         # 홈의 「남은 일」
extensions.register_workspace_reference(parts_services.references)  # 부서 삭제 확인
scopes.register_write_scope("/api/parts", "parts:write")            # 기계 자격의 쓰기
```

부서가 소유하는 표에 `owner_workspace_id`(nullable) 한 칸을 두면 권한 헬퍼가
그대로 붙는다. **NULL 은 전역**이고, 여러 부서가 함께 쓰므로 고치는 것은 시스템
관리자뿐이다.

---

## 배포

운영자가 읽는 정본은 **[deploy/README_OPERATOR.md](deploy/README_OPERATOR.md)** 다.
결정의 배경은 [ADR 0004](docs/adr/0004-리눅스-Apptainer-배포.md) 에 있다.

리눅스 · Apptainer 단일 이미지다. 폐쇄망 서버에 **`tar.gz` 하나**를 옮긴다.

### 번들은 태그에서 나온다

```bash
git tag v1.0.0 && git push origin v1.0.0
```

Actions 가 **검증을 먼저 돌리고**(`ci.yml` 전체) 통과하면 번들을 만들어 릴리스
자산으로 올린다 — `<slug>-v1.0.0.tar.gz` 와 `.sha256`.

**손으로 만들어 올리지 않는다.** `build_bundle.sh` 는 리눅스 · apptainer · npm 을
요구하는데, **저장소를 받는 기계가 Windows 인 경우가 많다** — Apptainer 는 Windows
네이티브 빌드가 없다. 만드는 자리를 사람 PC 에 두면 그 PC 마다 갖춰야 하고,
갖춘 정도가 다르면 **같은 태그에서 다른 번들이 나온다.**

로컬에서 만드는 것은 **번들 자체를 고칠 때**만 쓴다:

```bash
./deploy/build_bundle.sh v1.0.0-local    # release/<slug>-v1.0.0-local.tar.gz
```

### 서버로 옮겨 설치한다

```bash
# 받는 PC 에서 (Windows PowerShell 도 같다)
curl -LO https://github.com/<소유자>/<저장소>/releases/download/v1.0.0/<slug>-v1.0.0.tar.gz
scp <slug>-v1.0.0.tar.gz <계정>@서버:~/

# 서버에서
ssh <계정>@서버
tar xzf <slug>-v1.0.0.tar.gz && cd <slug>-v1.0.0
# 번들 하나로 여러 플랫폼 — 어느 플랫폼인지는 설치가 정한다(처음 한 번, 그 뒤엔 기억한다)
APP_SLUG=plmhub APP_NAME="PLM 기준정보" APP_PORT=8040 EXTENSIONS=hub \
  sudo ./deploy.sh prepare               # 최초 1회 — apt·postgres·DB 역할
APP_SLUG=plmhub sudo ./deploy.sh install # 첫 설치 — .env·systemd 유닛·시드까지
sudo ./deploy.sh update                  # 그다음부터 (인스턴스가 여럿이면 APP_SLUG=… 를 붙인다)
sudo ./deploy.sh status
```

원격에서만 걸리는 것들(root 로그인·임시 비밀번호·`ssh -t`)은
[README_OPERATOR](deploy/README_OPERATOR.md) 의 §0 에 표로 있다.

### 서버 두 대 · 메인 서버 뒤 · 경로 접두어

같은 번들로 **두 대에 활성-활성**으로 올린다. 앞에는 메인 서버(포탈 + nginx)가 있어 TLS 와
`/<slug>/` 접두어 벗기기 · A/B 분배를 하고, PostgreSQL 은 주/대기(스트리밍 복제 · DB VIP 로 자동
승격). 첨부 · 백업 · `.env` 는 공용 스토리지(`DATA_DIR=/data/<slug>`), 코드와 로그는 각 서버.
`deploy.sh` 가 메인 서버에 넣어 달라고 할 nginx 조각을 만들어 준다. 순서와 장애 대응은
[README_OPERATOR §8](deploy/README_OPERATOR.md), 결정은
[docs/이중화-배포-설계.md](docs/이중화-배포-설계.md).

```bash
APP_SLUG=<slug> APP_NAME=<이름> APP_PORT=<포트> EXTENSIONS=<확장> \
HA_ROLE=master PEER_IP=<B> PUBLIC_HOST=<호스트명> DATA_DIR=/data/<slug> \
  sudo ./deploy.sh prepare && sudo ./deploy.sh db-primary && sudo ./deploy.sh install   # A
APP_SLUG=<slug> APP_NAME=<이름> APP_PORT=<포트> EXTENSIONS=<확장> \
HA_ROLE=backup PEER_IP=<A> PUBLIC_HOST=<호스트명> DATA_DIR=/data/<slug> \
  sudo ./deploy.sh prepare && sudo ./deploy.sh db-standby && sudo ./deploy.sh install   # B
cat ~/apps/<slug>/main-server-nginx.conf     # → 메인 서버 쪽에
```

`.env` 는 이미지에 굽지 않고 **bind-mount 로 넣는다.** 굽으면 비밀번호를 바꾸는 데
이미지를 다시 만들어야 하고, 그러면 아무도 안 바꾼다.

이미지 루트는 읽기 전용이라 앱이 쓰는 곳은 셋뿐이다 — `.env`(ro) · `filestore` ·
`logs`. 이 경로가 어긋나면 **기동 가드가 무엇을 고쳐야 하는지 말하며** 죽는다
(`[Errno 30] Read-only file system` 트레이스백을 읽을 일이 없다).

## 검증

```bash
cd backend
.venv/bin/ruff format . ../mcp_server ../pipeline --config pyproject.toml
.venv/bin/ruff check . ../mcp_server ../pipeline --config pyproject.toml
.venv/bin/mypy
.venv/bin/python -m pytest
.venv/bin/python -m alembic check
cd ../frontend && npm run build && npm test && npm run lint
```

**`alembic check` 를 빼먹지 않는다.** 시험은 모델로 표를 만들기 때문에 마이그레이션이
모델과 어긋난 것을 못 잡는다 — 그 어긋남은 배포하고 나서 500 으로 드러난다. 이
저장소의 첫 마이그레이션도 `created_at` 의 NOT NULL 을 빠뜨렸고, 잡은 것이 이것이다.

테스트는 개발 `.env` 의 접속 정보에서 `<이름>_test` 를 파생해 쓴다.
**개발 DB 를 건드리는 테스트는 쓰지 않는다.**

셸 스크립트를 **Windows 쪽에서** 고쳤다면 실행 비트가 떨어졌을 수 있다. 구조 시험이
잡아 주지만, 걸렸으면 `chmod +x deploy/*.sh`.

---

## 구조

```
backend/
  app/
    branding.py        제품 이름·오류 접두사 — **바꾸는 자리는 여기 하나**
    config.py          DB행 -> 환경변수 -> 기본값 3단 fallback
    main.py            **조립 지점.** 라우터·확장·PAT 범위가 전부 여기를 거친다
    all_models.py      Alembic 이 보는 모델 목록. 빠뜨리면 표가 지워진다
    shared/
      auth.py          current_user — 사람 JWT 와 기계 PAT 을 같은 지점에서
      errors.py        오류 봉투 + 코드 조립 + 반드시 로그
      permissions.py   부서 스코프 판정 — **한 곳에서**
      extensions.py    도메인이 공통 화면에 끼는 레지스트리 셋
      scopes.py        기계 자격의 범위 레지스트리
      ops.py           백업이 살아 있나 — 홈의 「남은 일」에 올린다
      audit.py         감사 기록 — 되돌릴 수 없는 것만
      access_log.py    접근 로그 — 상태를 바꾼 요청만
    modules/
      accounts/ auth/ workspaces/ notices/ notifications/ audit/ server/
  scripts/
    export_openapi.py  프론트 타입의 입력을 만든다
    seed_install.py    첫 관리자 — 임시 비밀번호를 한 번만 찍는다
    set_admin.py       콘솔 복구 도구 (비밀번호를 잊었을 때)
  tests/architecture/  규칙을 문서가 아니라 **시험**으로 둔 자리
frontend/
  src/
    shared/
      branding.ts      제품 이름 — 백엔드의 짝
      api/client.ts    상대경로 /api, access 토큰은 메모리에만
      auth/roles.ts    역할 판정 — **한 곳에서**. 표시일 뿐 권한이 아니다
      layout/navigation.ts  **화면 목록의 정본**
      components/ui/   shadcn 프리미티브
    modules/           백엔드와 **같은 이름**
    routes/router.tsx  가드 밖은 셋뿐
deploy/                **배포의 정본** — 산출물이 아니다
  apptainer.def        이미지. venv 를 빌드 시점에 얼린다
  app.service.template systemd 유닛 (@@USER@@ 자리를 deploy.sh 가 채운다)
  build_bundle.sh      프론트 빌드 -> SIF -> 스크립트 동봉 -> tar.gz
  deploy.sh            prepare|install|update|reset|status|auto
  backup.sh restore.sh DB 덤프와 파일 — 복구는 카탈로그로 검증한다
  README_OPERATOR.md   **운영자가 읽는 정본**
.github/workflows/     CI (백엔드 · 프론트 · 이미지 빌드)
pipeline/              로컬 정제 도구 — AI 결과물 묶음을 검증 · 미리 보기 · 적재 (AGENTS.md 가 절차의 정본)
docs/
  adr/                 판단이 갈렸던 결정
  새-플랫폼-만들기.md    포크 절차의 정본
  리눅스-이전-계획.md    Windows -> 리눅스 이전의 기록과 밟은 함정
  데이터-온톨로지화-계획.md  원천 데이터를 AI 로 정제해 넣는 길 — 만들 것과 순서(계획)
  외부-시스템-연동-지침서.md 상대 시스템 개발자에게 그대로 건네는 연동 명세(초안)
```

## 참고

이 틀은 [68_TestScope](../68_TestScope) 와 [66_MatNexus](../66_MatNexus) 에서
반복되던 부분을 떼어 낸 것이다. 그 둘의 도메인 결정은 각자의 `AGENTS.md` 와
`docs/adr` 에 있다.

배포 지형은 같은 WSL 안의 `ReportArchive` 와 **같은 방식**이다. 운영하는 사람이
같은데 배포가 서로 다르면 헷갈릴 자리만 는다.
