# 운영 `.env` 설정 — 한 장 정리

> **이 문서는 정본이 아니라 정리본이다.** 정본은 `backend/app/config.py`(키 · 기본값 · 뜻) ·
> `deploy/deploy.sh`(설치 · 갱신이 `.env` 를 어떻게 쓰고 고치나) · `deploy/README_OPERATOR.md`(운영 절차)다.
> 둘이 어긋나면 정본이 맞고, 이 문서를 고친다.
>
> **기준 판**: v0.4.74 + 커밋 전 변경 `PUBLIC_URL`(ADR 0058, 2026-11-07) — 2026-11-08 정리.
> v0.4.74 서버에 `PUBLIC_URL` 을 적어도 아무 일이 없다(모르는 키는 조용히 무시된다 — 3.5). 먹는 판인지는
> `/api/health` 에 `public_url` 칸이 있나로 본다.
>
> **릴리스 번들에 함께 담긴다**(다음 판부터) — 번들 폴더의 `운영-env-설정.md` 와 점검 스크립트 `env-check.sh`
> (`build_bundle.sh` 가 이름으로 담는다). 그 전의 번들이면 저장소에서 연다.
>
> **저장소가 공개다.** 실제 서버 주소 · 계정 이름 · 비밀 값은 여기에도, 이 문서를 보고 만든 메모에도 적지 않는다.

| 자리 표시 | 뜻 |
| --- | --- |
| `<slug>` | 설치의 기계 이름(`APP_SLUG`) — 소문자 · 숫자 한 덩어리, 32자 이내 |
| `<id>` | HWAX 포털의 앱 id — **`<slug>` 와 같게** 둔다(README 9절) |
| `<이름>` | 화면 이름(`APP_NAME`) |
| `<포트>` · `<MCP 포트>` | 앱 포트(`PORT` · `APP_PORT`) · MCP 포트(= 앱 포트 +2) |
| `<계정>` | 운영 계정(`OPERATOR`, 보통 sudo 한 사람) |
| `<이 서버 IP>` · `<A 서버 IP>` · `<B 서버 IP>` · `<DB VIP>` | 서버 주소들 |
| `<공용 호스트>` | 이중화의 nginx 가 받는 호스트명(`PUBLIC_HOST`) |
| `<포털 호스트>` · `<포털 박스 주소>` · `<포털 JWKS 주소>` | 포털이 사람에게 보이는 호스트 · 포털 서버의 내부 주소 · 포털 담당이 주는 공개 키 주소 |
| `<.env>` | 그 설치의 `.env` 경로(0.1) |

---

## 0. 먼저 알 것 — `.env` 는 어디 있고, 누가 읽고, 누가 쓰나

### 0.1 자리

| 설치 | `.env` | 왜 |
| --- | --- | --- |
| 단독 · 공용 폴더 없는 이중화(리허설) | `/home/<계정>/apps/<slug>/.env` | `INSTALL_DIR` 아래 |
| 공용 폴더(`DATA_DIR`)를 준 이중화 | `/data/<공통폴더>/<slug>/.env` | **두 서버가 한 벌을 함께 읽는다** — JWT 비밀 · DB 주소가 같아야 한 사람이 두 서버를 오간다 |

컨테이너 안에서는 `/opt/app/backend/.env` 로 **읽기 전용** bind 된다(`app.service.template`). 권한은 `600 <계정>:<계정>` —
install 이 그렇게 만든다(`deploy.sh generate_env_if_missing`). 비밀 셋(JWT 비밀 · DB 비밀번호 · 위임 비밀)이 들어 있다.

### 0.2 읽는 쪽 — 둘이고, 읽는 방식이 다르다

| 읽는 쪽 | 어떻게 | 언제 |
| --- | --- | --- |
| 앱 · 작업 워커 · 동기화 · 지표 타이머(컨테이너 안) | pydantic-settings — `utf-8-sig`, 따옴표 · 줄 끝 ` #` 주석을 푼다, **모르는 키는 무시**(`extra="ignore"`) | **프로세스가 뜰 때 한 번.** 타이머는 돌 때마다 |
| `deploy.sh` · `backup.sh` · `restore.sh`(호스트) | `sed` 로 줄 첫머리가 정확히 `KEY=` 인 줄을 뽑아 **마지막 줄**(`tail -n1`)을 **글자 그대로** — 따옴표 · 주석 · `\r` 까지 | 명령을 칠 때마다 |

함정의 대부분(3장)은 이 둘의 차이에서 나온다 — 앱에는 멀쩡한데 배포 · 백업 스크립트가 다른 값을 읽는다.

**차례: 프로세스 환경 > `.env` > 코드 기본값.** 이미지가 `%environment` 로 `APP_ENV=production` · `LOG_DIR=/data/logs` ·
`FILESTORE_DIR=/data/filestore` 를 내보내므로(`apptainer.def`) 이 셋은 `.env` 에 무엇을 적어도 컨테이너 안에서는 이미지 값이다
(개발 PC 에서 `Settings` 로 확인: 환경의 `APP_ENV=production` 이 `.env` 의 `development` 를 이긴다).

### 0.3 쓰는 쪽 — `deploy.sh` 가 `.env` 에 손대는 것은 이것뿐이다

| 언제 | 무엇을 | 근거 |
| --- | --- | --- |
| `install` — `.env` 가 **없을 때만** | 틀(`.env.example`)에서 만든다: `APP_SLUG` · `APP_NAME` · `APP_TAGLINE` · `EXTENSIONS` · `PORT` · 난수 `JWT_SECRET` · `DATABASE_URL`(DB 비밀번호를 그 자리에서 돌린다). 백업 폴더를 알면 `BACKUP_DIR=/data/backup`, 이중화(`HA_ROLE`)면 `PUBLIC_PATH=/<slug>` · `TRUST_PROXY=true` | `generate_env_if_missing` |
| `install` · `update` — **매번** | `APP_SLUG` 를 견준다(다르면 멈춘다). `APP_NAME` · `APP_TAGLINE` · `EXTENSIONS` 를 **env > 설치 기록 > 번들 기본값** 으로 다시 적는다 | `sync_env_identity` |
| `install` · `update` — env 로 **준 것만** | `PORTAL_JWKS_URL` · `PORTAL_LAUNCH_URL` · `PORTAL_AUDIENCE` · `PORTAL_ISSUER` · `HEAX_SSO_ALLOWED_IPS`(빈 값을 주면 비운다). JWKS 가 있는데 `PUBLIC_PATH` · `TRUST_PROXY` 줄이 없으면 `/<slug>` · `true` 를 더한다 | `PORTAL_ENV_KEYS` · `sync_env_portal` |
| `hwax-request` | `HEAX_SSO_SECRET` 이 없으면 만들어 적는다(있으면 그대로). **앱을 다시 띄우지 않는다** | `cmd_hwax_request` |

**그 밖의 키는 배포가 영영 안 고친다** — `PUBLIC_URL` · `MCP_PUBLIC_URL` · `PUBLIC_PATH`(포털 경우 말고) · `TRUST_PROXY` · `BACKUP_DIR`
(처음 말고) · **`PORT`(처음 말고)** · `DATABASE_URL`(처음 말고) · 크기 · 시간 · 지표 · 뜻으로 찾기의 값. 손으로 고치고 `restart` 한다.

### 0.4 `.env` 밖에 남는 설치 설정

`deploy.sh` 에 env 로 준 값은 `.env` 가 아니라 아래에 남고, **다음 배포가 그것을 기억한다**(env 로 다시 주면 갱신).

| 파일 | 키 | 서버마다? |
| --- | --- | --- |
| `/etc/platform-instances/<slug>.conf`(644, 비밀 없음) | `APP_NAME` · `APP_TAGLINE` · `APP_PORT` · `MCP_PORT` · `EXTENSIONS` · `INSTALL_DIR` · `DATA_DIR` · `BACKUP_HOST_DIR` | **서버마다** — 이중화면 A · B 에 따로 있다 |
| `/etc/platform-ha.conf`(644) | `HA_ROLE` · `LB_MODE` · `PEER_IP` · `SELF_IP` · `WEB_VIP` · `DB_VIP` · `PUBLIC_HOST` · `VRRP_IFACE` · `TLS_CERT` · `TLS_KEY` | 서버마다(그 서버의 모든 설치가 함께) |
| `/etc/systemd/system/<slug>-mcp.service` 의 `Environment=` | `PLATFORM_API_BASE`(= `MCP_API_BASE`) · `MCP_HOST` · `MCP_PORT` · `MCP_ALLOWED_HOSTS` · `APP_SLUG` | 서버마다 |

**기억되지 않는 것**(줄 때마다 그 한 번만): `MCP_ENABLED` · `SYNC_ENABLED` · `METRICS_ENABLED`(기본 1 — 3.15) · `DB_NAME` · `DB_USER` ·
`DB_HOST_OVERRIDE` · `PG_VERSION` · `OPERATOR` · `AI_BUNDLE` · `OLLAMA_VERSION`.

`sudo ./deploy.sh setup` 이 묻는 것(`ask`): 역할(A · B · 1) · `APP_SLUG` · `APP_NAME` · `APP_PORT`(기본은 이 서버의 다음 10 자리) ·
`EXTENSIONS` · (이중화면) `PEER_IP` · `PUBLIC_HOST` · `DATA_DIR` · (공용 폴더 없는 B 면) A 의 계정 · ssh 포트. **포털 · `PUBLIC_URL` ·
`MCP_PUBLIC_URL` · 백업 폴더는 묻지 않는다** — setup 뒤에 1장대로 따로 맞춘다.

---

## 1. 한눈에 — 설치 형태별 값 세트

install 이 만든 줄(`APP_ENV` · `APP_SLUG` · `APP_NAME` · `PORT` · `JWT_SECRET` · `DATABASE_URL`)은 그대로 두고, 아래만 맞춘다.

| 키 | 단독 서버 | 이중화(nginx 뒤) | HWAX 포털 뒤 |
| --- | --- | --- | --- |
| `PUBLIC_PATH` | 적지 않는다(루트) | **`/<slug>`** — nginx 조각이 `/<slug>` 로 박혀 있다 | **`/<id>`**(= `/<slug>`) |
| `TRUST_PROXY` | `false` | `true` | `true` |
| `PUBLIC_URL` | 권함: `http://<서버 호스트>:<포트>` | 권함: `https://<공용 호스트>/<slug>` | **적는다**: `https://<포털 호스트>/<id>` |
| `MCP_PUBLIC_URL` | 적지 않는다 | 적지 않는다 | **`http://<이 서버 IP>:<MCP 포트>/mcp`** |
| `REFRESH_COOKIE_SECURE` | `false` | `false` | `false` |
| `DATABASE_URL` 의 호스트 | `localhost` | `<DB VIP>`(없으면 `<A 서버 IP>`) | 설치 형태대로 |
| `BACKUP_DIR` | `/data/backup`(백업 폴더를 줄 때만) | `/data/backup` | 설치 형태대로 |
| `PORTAL_JWKS_URL` · `PORTAL_LAUNCH_URL` | — | — | **적는다** |
| `HEAX_SSO_SECRET` | — | — | `hwax-request` 가 만든다 |
| `HEAX_SSO_ALLOWED_IPS` | — | — | 권함: `<포털 박스 주소>` |
| 설치 설정 `MCP_HOST` | 사람들의 Claude 가 붙으면 `0.0.0.0`(기본 `127.0.0.1`) | `0.0.0.0`(저절로) | **`0.0.0.0`** — 게이트웨이가 MCP 포트를 직접 부른다 |

### 1.1 단독 서버

사람이 `http://<서버 호스트>:<포트>/` 로 직접 들어오고, Claude 는 `http://<서버 호스트>:<MCP 포트>/mcp` 로 붙는다.

```dotenv
# APP_ENV ~ PORT 는 install 이 만든 그대로(JWT_SECRET · DATABASE_URL 은 생략) — 모양만 보이려고 적었다
APP_ENV=production
APP_SLUG=<slug>
APP_NAME=<이름>
PORT=<포트>
HOST=0.0.0.0
REFRESH_COOKIE_SECURE=false
TRUST_PROXY=false
PUBLIC_URL=http://<서버 호스트>:<포트>
# 백업 폴더(BACKUP_HOST_DIR)를 준 설치만 — 유닛이 그 폴더를 컨테이너의 /data/backup 에 건다
BACKUP_DIR=/data/backup
```

```bash
sudo -u <계정> mkdir -p /home/<계정>/backup/<slug>      # 백업 폴더 — 단독 서버에서는 deploy.sh 가 안 만든다(3.13)
sudo APP_SLUG=<slug> APP_NAME="<이름>" APP_PORT=<포트> BACKUP_HOST_DIR=/home/<계정>/backup/<slug> ./deploy.sh prepare
sudo APP_SLUG=<slug> ./deploy.sh install
sudo APP_SLUG=<slug> MCP_HOST=0.0.0.0 ./deploy.sh update   # 사람들의 Claude 가 붙게 — 한 번(유닛에 남는다)
```

`PUBLIC_URL` 은 안 적어도 대개 맞다 — MCP 가 들어온 주소(MCP 포트 −2)로 짐작한다(2.4). 적어 두면 MCP 를 IP 로 등록한 사람과
이름으로 등록한 사람이 같은 링크를 받는다.

### 1.2 이중화 — 메인 서버 nginx 뒤(`LB_MODE=external`) · A · B 자체 nginx(`LB_MODE=local`)

사람은 `https://<공용 호스트>/<slug>/` 로, Claude 는 `https://<공용 호스트>/<slug>/mcp` 로 들어온다. nginx 가 접두어를 떼고 A · B 로
나눈다.

```dotenv
PUBLIC_PATH=/<slug>
TRUST_PROXY=true
PUBLIC_URL=https://<공용 호스트>/<slug>
REFRESH_COOKIE_SECURE=false
BACKUP_DIR=/data/backup
DATABASE_URL=postgresql+psycopg://<slug>:<install 이 만든 비밀번호>@<DB VIP>:5432/<slug>
```

```bash
# A 에서. B 는 HA_ROLE=backup PEER_IP=<A 서버 IP> 로 prepare → db-standby → install(README 8.1). 처음이면 sudo ./deploy.sh setup 이 대신 묻는다
sudo APP_SLUG=<slug> APP_NAME="<이름>" APP_PORT=<포트> \
     HA_ROLE=master PEER_IP=<B 서버 IP> PUBLIC_HOST=<공용 호스트> DATA_DIR=/data/<공통폴더>/<slug> ./deploy.sh prepare
sudo APP_SLUG=<slug> ./deploy.sh db-primary
sudo APP_SLUG=<slug> ./deploy.sh install
```

`PUBLIC_PATH` · `TRUST_PROXY` · `BACKUP_DIR` 은 **이중화로 처음 install 할 때만** 저절로 들어간다. 단독으로 깔았던 설치를 이중화로
옮기면 `.env` 가 이미 있어 다시 안 만들어진다 — 위 셋을 손으로 적는다(3.13). `PUBLIC_URL` 은 nginx 경유로 붙은 MCP 에는 없어도
맞지만, MCP 를 서버 직접 주소로 등록한 사람의 링크가 nginx 를 건너뛴 `http://<A 서버 IP>:<포트>/<slug>` 가 된다 — 적어 둔다.

### 1.3 HWAX 포털 뒤 (단독이든 이중화든, 위 세트에 더한다)

사람은 `https://<포털 호스트>/<id>/` 로 들어오고(포털 nginx 가 `/<id>/` 를 벗겨 앱 포트로), 사람들의 Claude 는 포털의 게이트웨이로
붙는다 — **게이트웨이가 이 서버의 MCP 포트를 직접 부른다**(README 9절 · ADR 0028).

```dotenv
PUBLIC_PATH=/<id>
TRUST_PROXY=true
PUBLIC_URL=https://<포털 호스트>/<id>
PORTAL_JWKS_URL=<포털 JWKS 주소>
PORTAL_LAUNCH_URL=https://<포털 호스트>/launch/<id>
# PORTAL_AUDIENCE · PORTAL_ISSUER 는 적지 않는다 — 기본(APP_SLUG · PORTAL_LAUNCH_URL 의 앞부분 = 포털의 기본 iss)이 맞다. 포털 id 를 slug 와 다르게 지었을 때만
HEAX_SSO_SECRET=<hwax-request 가 만든 64자 — 손으로 짓지 않는다>
HEAX_SSO_ALLOWED_IPS=<포털 박스 주소>
MCP_PUBLIC_URL=http://<이 서버 IP>:<MCP 포트>/mcp
```

```bash
sudo APP_SLUG=<slug> ./deploy.sh hwax-request           # 위임 비밀(없으면 만든다) + 요청서 — 비밀은 따로 전한다
sudo APP_SLUG=<slug> MCP_HOST=0.0.0.0 ./deploy.sh update   # 게이트웨이가 MCP 에 닿게(이중화면 이미 0.0.0.0)
# 포털 담당에게 JWKS 주소를 받은 뒤 — 이 셋은 env 로 주면 deploy.sh 가 .env 에 적는다(PUBLIC_PATH · TRUST_PROXY 도 없으면 더한다)
sudo APP_SLUG=<slug> PORTAL_JWKS_URL=<포털 JWKS 주소> PORTAL_LAUNCH_URL=https://<포털 호스트>/launch/<id> \
     HEAX_SSO_ALLOWED_IPS=<포털 박스 주소> ./deploy.sh update
# PUBLIC_URL · MCP_PUBLIC_URL 은 deploy.sh 가 안 적는다 — .env 를 손으로 고치고
sudo APP_SLUG=<slug> ./deploy.sh restart
```

`update` 는 SIF 를 그 번들의 것으로 바꾼다 — env 를 적으려고 칠 때는 **지금 깔린 판의 번들 폴더**에서 친다(3.14). 판을 안 바꾸려면
`PORTAL_*` 도 손으로 적고 `restart` 하면 된다(그때는 `PUBLIC_PATH` · `TRUST_PROXY` 를 함께 적는다).

---

## 2. 키별 표

틀리면 생기는 일의 「앱이 안 뜬다」 는 그 키가 숫자 · 참거짓 · 정해진 낱말인데 읽지 못하는 값일 때다 — `journalctl -u <slug> -n 50` 에
`ValidationError` · `SettingsError` 가 남는다(3.4).

### 2.1 이름

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `APP_ENV` | `development` | `production` 으로 둔다. 이미지가 `production` 을 내보내 컨테이너 안에서는 늘 그것이다 | `.env` 쪽 값은 무시된다(0.2) — 그래도 맞춰 둔다(호스트에서 앱을 돌리는 날을 위해) | `config.py app_env` · `apptainer.def` |
| `APP_SLUG` | 번들 기본(틀의 이름) | 설치의 기계 이름. **DB 이름 · 리프레시 쿠키 · PAT 표식 · 화면 저장소 키 · 유닛 이름이 여기서 나온다.** 설치 뒤에는 **바꾸지 않는다** | 꼴이 틀리면 앱이 안 뜬다. 설치 기록과 다르면 `update` 가 멈춘다(「slug 는 설치 뒤 바꿀 수 없습니다」). 같은 서버의 다른 설치와 같으면 DB · 쿠키 · 토큰이 조용히 부딪힌다 | `config.py app_slug` · `deploy.sh sync_env_identity` |
| `APP_NAME` | 번들 기본 | 화면 제목 · `/api/health` 의 `app`. 바꿀 때는 **`sudo APP_NAME="…" ./deploy.sh update`**(손으로 고치면 다음 update 가 되돌린다 — 3.9) | — | `sync_env_identity` |
| `APP_TAGLINE` | 빈 값 | 로그인 · 사이드바의 한 줄. 바꾸는 길은 `APP_NAME` 과 같다 | — | 같음 |
| `REFRESH_COOKIE_NAME` | `<slug>_refresh` | **적지 않는다** — slug 에서 나온다 | 같은 서버의 두 설치가 같은 이름이면 번갈아 로그아웃된다 | `config.py refresh_cookie_name` |

### 2.2 포트 · 프로세스

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `PORT` | `8040` | 앱 포트 — **Apptainer 는 호스트 네트워크라 이것이 곧 호스트 포트다.** 플랫폼마다 **10씩** 벌린다(MCP 가 +2, 개발이 +1 을 쓴다). install 이 `APP_PORT` 로 적는다 | 겹치면 나중에 뜬 쪽이 못 뜨고 이유는 journal 에만 남는다. **설치 뒤 `APP_PORT` 를 바꿔도 `.env` 의 `PORT` 는 그대로다**(3.10) | `config.py port` · `deploy.sh check_port_clash` |
| `HOST` | `0.0.0.0` | 바인딩. 그대로 둔다 | `127.0.0.1` 이면 nginx · 포털 · 상대 서버가 못 닿는다 | `run.py` |
| `UVICORN_WORKERS` | `4` | 앱 작업자 수. 메모리가 모자라면 줄인다 | 빈 값이면 앱이 안 뜬다 | `config.py uvicorn_workers` |
| (설치 설정) `APP_PORT` · `MCP_PORT` | 번들의 `port` · 앱 +2 | `deploy.sh` 가 기억하는 포트 — 설치 기록 · MCP 유닛 · nginx 조각 · 요청서가 이것을 쓴다 | 다른 설치와 겹치면 deploy 가 **멈춘다**(물어보지 않는다) | `check_port_clash` |

### 2.3 DB · 비밀

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `DATABASE_URL` | 빈 값(→ `…@localhost:5432/<slug>`) | install 이 통째로 적는다: `postgresql+psycopg://<slug>:<난수>@<호스트>:5432/<slug>`. 바꾸는 때는 **호스트뿐**(DB VIP 를 받았을 때 · 승격 뒤 VIP 없이) | DB 이름이 남의 것이면 **오류 없이 남의 계정 표를 읽고 마이그레이션한다.** `backup.sh` 가 못 쪼개는 꼴이면 백업이 멈춘다(3.16) | `config.py database_url` · `backup.sh` 의 `DSN` |
| `JWT_SECRET` | 개발용 고정값 | install 이 난수(64자 남짓)로 만든다. **설치마다 따로.** 바꾸면 전원이 다시 로그인한다 | 기본값이면 운영에서 **앱이 기동을 거부한다**. 두 설치가 같은 값이면 서로의 토큰 서명을 받아 준다(3.8) | `main.py _guard_production_secrets` · `auth/security.py` |
| `REFRESH_COOKIE_SECURE` | `false` | `false` 면 요청이 https 일 때만 Secure 를 붙인다(프록시 뒤는 `TRUST_PROXY` 가 판정). **보통 `false`** | `true` 인데 http 로 들어오면 새로고침마다 로그인 화면 | `config.py refresh_cookie_secure` |

### 2.4 주소와 접두어

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `PUBLIC_PATH` | 빈 값(루트) | 브라우저가 보는 **접두어**(`/<slug>`). 앞의 nginx · 포털이 그 접두어를 떼고 넘길 때. 화면 주소 · 쿠키 path · `/api/docs` 가 이것을 안다. 앞뒤 `/` 는 정리된다 | 없으면 이중화에서 `/<slug>/` 밑의 화면이 API 404, 포털에서는 로그인 뒤 포털 첫 화면으로 간다. 이중화는 nginx 조각이 `/<slug>` 로 박혀 있어 **다른 값이면 어긋난다**(`ha.sh` 의 `prefix="/$APP_SLUG"`) | `config.py public_path` · `main.py PrefixMiddleware` |
| `PUBLIC_URL` | 빈 값(짐작) | **사람이 브라우저로 여는 화면의 바탕 주소 — 접두어까지.** 아래 2.4.1 | 틀린 주소면 AI 답에 **열리지 않는 링크**가 붙는다. 사람은 그 링크를 같은 화면으로 믿는다 | `config.py public_url` · `shared/public_url.py` · ADR 0058 |
| `TRUST_PROXY` | `false` | 앞의 프록시가 준 `X-Forwarded-For` · `X-Forwarded-Proto` 를 믿는다 — **프록시 · 포털 뒤에서만 `true`** | 프록시 뒤에서 꺼 두면 https 를 http 로 보고 쿠키에 Secure 를 안 붙인다. 프록시 없이 켜면 앱 포트에 닿는 누구나 그 머리를 꾸민다(uvicorn 이 `forwarded_allow_ips="*"`). 빈 값이면 앱이 안 뜬다 | `run.py` · `config.py trust_proxy` |
| `CORS_ORIGINS` | 개발 서버 둘 | 배포는 같은 출처라 **적지 않는다**. 적으면 JSON 목록(`["https://…"]`) | JSON 이 아니거나 **빈 값이면 앱이 안 뜬다**(개발 PC 에서 확인) | `config.py cors_origins` |

#### 2.4.1 `PUBLIC_URL` — 새 키, 자세히

**무엇에 쓰나.** 바깥 AI 가 MCP 로 받은 목록 · 통계 · 지표 · 분석에 「같은 조건의 화면」 주소(`url`)가 붙는다 — 사람이 눌러 AI 의
답을 그 화면에서 확인한다(ADR 0058). `url` = 바탕 주소 + 백엔드가 지은 상대 `link`. 바탕 주소는 `/api/health` 의 `public_url` 이고,
MCP 가 그것을 읽는다. **`PUBLIC_URL` 이 바꾸는 것은 이 링크뿐이다** — 화면의 접두어(`PUBLIC_PATH`), 화면이 안내하는 MCP 주소
(`MCP_PUBLIC_URL`), MCP 의 사진 업로드 주소는 따로다.

**왜 요청에서 정하지 않나.** 앱은 자기 바깥 주소를 모른다. 프록시는 `Host` 에서 포트를 떼고 넘기고, 포털의 게이트웨이는 MCP 포트를
직접 부른다 — 그 요청의 주소로 지은 링크는 사람의 브라우저에서 안 열린다.

**고르는 차례**(`shared/public_url.screen_base` → MCP `_screen_base`):

| 차례 | 어디서 | 바탕 주소 |
| --- | --- | --- |
| 1 | `PUBLIC_URL` | 적은 그대로(끝 `/` 는 뗀다) |
| 2 | `PORTAL_LAUNCH_URL` 의 `scheme://호스트` + `PUBLIC_PATH` | `https://<포털 호스트>/<id>` |
| 3 | `MCP_PUBLIC_URL` 이 `<앱 주소>/mcp` 꼴이면 그 앞(접두어 뺌) + `PUBLIC_PATH`. **MCP 포트를 바로 적은 값이면 건너뛴다** | `https://<호스트>:<프록시 포트>/<slug>` |
| 4 | (백엔드는 모름 — `public_url: null`) MCP 가 들어온 요청의 `X-Forwarded-Host`/`Host` · `X-Forwarded-Proto`(MCP 포트로 왔으면 −2) + `base_path` | 들어온 주소 |
| — | 넷 다 모름(MCP 가 백엔드에 못 닿음) | `url` 을 안 붙인다 — 상대 `link` 만 |

**형태별로 무엇이 나오나**:

| 형태 | 비워 두면 | 적을까 |
| --- | --- | --- |
| 단독, MCP 를 `http://<서버>:<MCP 포트>/mcp` 로 등록 | 4번 → `http://<서버>:<포트>` — 맞다 | 권함(등록한 주소마다 링크가 갈리지 않게) |
| 이중화, MCP 를 `https://<공용 호스트>/<slug>/mcp` 로 등록 | 4번 → `https://<공용 호스트>/<slug>` — 맞다 | 권함 — 서버 직접 주소로 등록한 사람은 `http://<A 서버 IP>:<포트>/<slug>`(nginx 를 건너뛴 http)를 받는다 |
| **포털 뒤, 게이트웨이 경유** | 2번이 있으면 맞다. **2번이 없으면 4번** — 들어온 호스트는 게이트웨이가 부른 `<이 서버 IP>:<MCP 포트>` 라 링크가 `http://<이 서버 IP>:<포트>/<id>/…` 가 된다. 사람 PC 가 그 포트에 못 닿으면 안 열리고, 닿아도 포털 로그인을 거치지 않는 http 주소다 | **적는다** — 2번은 `PORTAL_LAUNCH_URL` 한 칸에 기댄다. 단추를 끄려고 그 칸을 비우거나, launch 주소를 사람이 여는 호스트와 다른 이름으로 받은 설치에서는 4번으로 떨어진다(README 5b 는 「적지 않아도 맞다」 고 적는다 — 이 문서는 적는 쪽을 권한다) |
| 표준이 아닌 포트의 프록시 뒤(`https://<호스트>:8443/<slug>/`) | `MCP_PUBLIC_URL=https://<호스트>:8443/<slug>/mcp` 를 적었으면 3번으로 맞다. 아니면 틀린다 | 적는다 |

**꼴**: `http(s)://<호스트>[:<포트>]<PUBLIC_PATH>` — 경로는 **`PUBLIC_PATH` 와 글자까지 같아야** 한다(접두어를 빼먹으면 링크가 포털 첫
화면이나 옆 플랫폼을 연다). 끝 `/` 는 있어도 된다. 쿼리 · `#` 은 넣지 않는다. 5장의 점검 스크립트가 경로 일치를 본다.

**적용 · 확인**: 앱은 뜰 때 읽고, MCP 는 `/api/health` 를 **300초** 붙들어 둔다(`server.py SCREEN_TTL`) — `sudo ./deploy.sh restart` 가
앱 · 워커 · MCP 를 함께 다시 띄우므로 바로 먹는다. `curl -s http://127.0.0.1:<포트>/api/health` 의 `public_url` 이 적은 값인지,
Claude 에서 목록 하나를 물어 답의 `url` 이 그 값으로 시작하고 **눌러서 열리는지** 본다.

**확인 필요**: 포털 뒤에서 사람이 포털 주소의 깊은 링크(`…/<id>/o/<타입>/<id>` 같은)를 처음 열 때 포털 로그인 뒤 그 화면으로 돌아오는지는
운영에서 아직 안 봤다(ADR 0058 「다음」). 안 돌아오면 `PUBLIC_URL` 만으로는 부족하다.

### 2.5 포털 로그인 (ADR 0028)

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `PORTAL_JWKS_URL` | 빈 값 | 포털의 공개 키 주소 — 포털 담당에게서 받는다. **비우면 포털 로그인이 꺼진다** | 비면 콜백이 404 JSON(포털 진단표의 「SSO 꺼짐」). 이 서버에서 안 닿으면 로그인 화면에 「공개 키를 받지 못해」 | `config.py portal_jwks_url` · `portal.py status` |
| `PORTAL_LAUNCH_URL` | 빈 값 | 로그인 화면의 「HWAX 포털로 로그인」 이 가는 곳(`https://<포털 호스트>/launch/<id>`). `PUBLIC_URL` 짐작의 2번이기도 하다 | 비면 단추가 없다. JWKS 없이 이것만 있으면 단추를 안 그린다 | `main.py` 의 `app-portal-launch` |
| `PORTAL_AUDIENCE` | 빈 값(→ `APP_SLUG`) | 토큰의 `aud` — 포털 id 를 slug 와 다르게 지었을 때만 | 다르면 서명이 맞아도 「확인 실패」. 서버 화면이 접두어와 다르다고 말한다 | `config.py portal_audience` |
| `PORTAL_ISSUER` | 빈 값(→ `PORTAL_LAUNCH_URL`, 없으면 `PUBLIC_URL` 의 앞부분 `https://<포털 호스트>` — 포털의 기본 iss 가 포털 자기 주소다. 둘 다 없으면 포털 로그인이 「확인 실패」 이고 서버 화면이 말한다) | 포털 `JWT_ISSUER` 를 바꿔 쓰는 박스만 | `PORTAL_ISSUER=` 빈 줄은 기본으로 읽는다(그 함정은 막아 두었다) | `config.py portal_issuer` |

### 2.6 위임 인증 — 게이트웨이의 사람별 토큰 (ADR 0028)

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `HEAX_SSO_SECRET` | 빈 값 | 게이트웨이와 이 설치만 아는 비밀. **설치마다 따로**, `hwax-request` 가 만든다(64자). **이미 포털에 전한 값은 바꾸지 않는다** — 바꿀 때는 포털 담당과 같이 | 비면 위임이 꺼진다(`/api/auth/sso` 404 JSON — 포털의 Claude 가 이 설치를 못 부른다). 다르면 401. 32자 아래면 서버 화면이 경고 | `delegation.py` · `SECRET_MIN_CHARS = 32` |
| `HEAX_SSO_ALLOWED_IPS` | 빈 값(주소 안 봄) | 위임 발급을 받아 줄 원격 주소, 쉼표 · CIDR(`<포털 박스 주소>`). **TCP 원격 주소로만** 본다. 이중화면 게이트웨이가 부르는 쪽 주소 | **못 읽는 값이 하나라도 있으면 전부 거절**(오타로 제한이 풀리지 않게). 목록이 있으면 `X-Forwarded-For` 가 붙은 요청은 받지 않는다 | `delegation.py _check_origin` |

비밀은 `deploy.sh` 에 env 로 주지 않는다 — `info` 가 화면에 찍고 셸 기록에 남는다. 포털 담당에게는 요청서와 **따로**, 메신저 평문
말고 전한다.

### 2.7 MCP

| 키 | 어디 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 |
| --- | --- | --- | --- | --- |
| `MCP_PUBLIC_URL` | `.env` | 빈 값(화면이 짐작) | 화면(「내 정보」)이 **사람에게 안내할 MCP 주소**. 포털 뒤면 `http://<이 서버 IP>:<MCP 포트>/mcp`, 표준이 아닌 포트의 프록시 뒤면 `https://<호스트>:<포트>/<slug>/mcp` | 포털 뒤에서 비우면 화면이 `https://<포털 호스트>/<id>/mcp` 를 안내하는데 **그 길은 없다**(포털은 MCP 경로를 열지 않는다) |
| `MCP_HOST` | MCP 유닛 | `127.0.0.1`(이중화면 `0.0.0.0`) | `sudo MCP_HOST=0.0.0.0 ./deploy.sh update` 한 번 — 기억된다 | `127.0.0.1` 이면 다른 PC · 게이트웨이 · 상대 nginx 가 못 닿는다 |
| `MCP_PORT` | MCP 유닛 · 설치 기록 | 앱 +2 | 보통 안 준다 | 다른 설치의 앱 · MCP 포트와 겹치면 deploy 가 멈춘다 |
| `MCP_ALLOWED_HOSTS` | MCP 유닛 | 빈 값(사내망 가정, 보호 끔) | 허용할 `Host`(포트까지). 이중화 `LB_MODE=local` 은 저절로 채운다 | 적었는데 게이트웨이 · nginx 가 부르는 주소가 없으면 **421** |
| `MCP_API_BASE` | MCP 유닛(`PLATFORM_API_BASE`) | `http://127.0.0.1:<포트>` | MCP 가 부르는 백엔드. **기억되므로 앱 포트를 바꾸면 함께 준다**(3.10) | 옛 포트를 부르면 모든 도구가 연결 실패 |
| `MCP_ENABLED` | (기억 안 됨) | `1` | `0` 이면 그 update 에서 MCP 를 건너뛴다 | 다음 update 가 다시 깐다(3.15) |

### 2.8 뜻으로 찾기 (ADR 0050)

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `EMBEDDING_BACKEND` | `ollama` | `off` 면 뜻으로 찾기를 끈 설치 — 배포도 Ollama 를 안 깐다 | `ollama` · `off` 밖이면 앱이 안 뜬다 | `config.py embedding_backend` · `deploy.sh setup_embedding` |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 주소. **이 서버(127.0.0.1 · localhost)일 때만** 배포가 Ollama 를 깐다. 서버의 플랫폼들이 하나를 함께 쓴다 | 다른 서버 주소면 여기 깔지 않는다 — 그 서버에 있어야 한다 | 같음 |
| `EMBEDDING_MODEL` | `bge-m3` | 담는 칸이 1,024차원이라 **차원이 같은 모델만** 된다. 바꾸면 담아 둔 것을 다시 만든다 | 차원이 다르면 서버 화면의 「뜻으로 찾기」 가 말한다 | `config.py embedding_model` |

`deploy.sh ollama` 는 이 셋을 `.env` 에서 읽어 Ollama 만 갖춘다(앱을 다시 띄우지 않는다). 앱의 값을 바꿨으면 `restart`.

### 2.9 파일

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `FILESTORE_DIR` · `LOG_DIR` | 이미지가 `/data/filestore` · `/data/logs` | **적지 않는다** — 이미지 값이 이긴다(0.2). 유닛의 `--bind` 대상과 같아야 한다 | bind 밖이면 기동 가드가 「쓸 수 없습니다」 로 멈춘다 | `apptainer.def` · `main.py _guard_writable_paths` |
| `BACKUP_DIR` | 없음 | 앱이 마지막 덤프 시각을 읽는 **컨테이너 안** 경로 — 백업 폴더를 deploy 가 알면 `/data/backup` | 안 적으면 홈 「남은 일」 이 「설정이 없다」. **빈 값(`BACKUP_DIR=`)은 `.` 으로 읽힌다**(3.4) | `config.py backup_dir` · `shared/ops.py` |
| `DATASOURCE_DIR` | 없음(URL 로만) | 데이터 소스가 파일을 읽어도 되는 폴더 — bind 아래(`/data/filestore/incoming`) | **빈 값은 `.`(앱의 작업 폴더 `/opt/app/backend` — `.env` 가 bind 된 자리)으로 읽힌다**(3.4) | `config.py datasource_dir` · `datasources/fetchers.py` |
| `ATTACHMENT_MAX_BYTES` | 500MB | 첨부 한 파일. 이중화 nginx `client_max_body_size 512m` · 포털 nginx 상한 안 | 넘겨 올리면 nginx 가 먼저 413 | `config.py attachment_max_bytes` |
| `JOB_FILE_MAX_BYTES` | 100MB | 표 파일(일괄 입력 · 묶음) — 통째로 메모리에 올려 푼다 | — | `config.py` |
| `JOB_PHOTOS_MAX_BYTES` | 2GB | 사진 zip. 올리면 이중화 nginx 의 사진 zip 경로(2100m)도 | nginx 413 | `config.py` · README 5b-2 |
| `JOB_FILE_INLINE_MAX_BYTES` | 32MB | 넘는 작업 파일은 DB 가 아니라 `FILESTORE_DIR/_jobs/`. 공용 폴더 없는 두 대는 `JOB_PHOTOS_MAX_BYTES` 이상으로 | 두 서버의 `FILESTORE_DIR` 이 다르면 `JOBS-0030` | `config.py` |
| `JOB_OUTPUT_MAX_BYTES` | 512MB | 내보내기 결과 — 만드는 동안 메모리에 든다 | — | `config.py` |
| `FILESTORE_GC_HOURS` | 24 | 고아 첨부 정리 주기(0 이면 끔) | 지운 파일은 되살릴 수 없다 | `config.py` |

### 2.10 보는 범위 (ADR 0034)

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `VIEW_SCOPE` | `all` | `all` — 가입자 누구나 모든 자료를 본다. `department` — 전역 + 내 부서만. 고치는 권한은 어느 쪽이든 소유 부서의 관리자 | 다른 낱말이면 앱이 안 뜬다. `all` → `department` 로 좁히면 코어 API 의 받는 쪽에서 받던 것이 **표식 없이 빠진다** — 「처음부터 다시 받기」 를 알린다 | `config.py view_scope` · ADR 0034 |

### 2.11 확장

| 키 | 기본 | 뜻 · 언제 바꾸나 | 틀리면 | 근거 |
| --- | --- | --- | --- | --- |
| `EXTENSIONS` | 빈 값(코어만) | **아무도 켜고 끈 적 없는 확장의 기본값**일 뿐이다. 켜고 끄는 곳은 화면(시스템 관리자 › 서버 › 「확장 모듈」)이고 화면이 이긴다. 기본값을 바꾸려면 `sudo EXTENSIONS=<a,b> ./deploy.sh update` | 이 번들에 없는 이름(오타)은 서버 화면이 「.env 에 적혔는데 없는 이름」 으로 말한다. 손으로 고친 값은 다음 update 가 되돌린다(3.9) | `config.py extensions` · `server/schemas.py extensions_unknown` |

### 2.12 그 밖에 — 거의 안 바꾸는 것

기본값이 실측으로 정해진 값들이다. 바꾸면 `restart`. 근거는 모두 `config.py` 의 같은 이름 칸(소문자).

| 묶음 | 키 = 기본 | 바꿀 때 |
| --- | --- | --- |
| 작업 · 워커 | `JOB_FILE_TTL_DAYS=7` · `JOB_TTL_DAYS=30` · `JOB_NOTIFY_AFTER_SECONDS=20` · `JOB_MAX_ROWS=100000` · `BUNDLE_EXPORT_MAX_ROWS=500000` · `WORKER_POLL_SECONDS=2.0` · `WORKER_STALE_SECONDS=300` · `WORKER_MAX_ATTEMPTS=3` | `WORKER_STALE_SECONDS` 를 짧게 잡으면 잠깐 느린 워커의 작업을 남이 뺏는다 |
| 보관 | `LOG_RETENTION_DAYS=30` · `ACCESS_LOG_RETENTION_DAYS=365`(0 이면 안 지움) · `TOMBSTONE_TTL_DAYS=90` · `UNDO_JOURNAL_TTL_DAYS=30` · `CORE_WATERMARK_FLOOR_SECONDS=600` | 감사 기록(변경 이력)은 어느 값과도 상관없이 안 지운다 |
| 지표 | `METRICS_MAX_CELLS=10000000` · `METRICS_NIGHT_HOUR=2` · `METRICS_FULL_EVERY_DAYS=7` · `METRICS_INCREMENTAL_MAX_SHARE=0.2` · `METRICS_WORK_MEM=256MB` · `METRICS_MAX_READ_CELLS=20000` · `METRICS_MAX_FRAME_CELLS=200000` | 밤 시간은 백업(03:00) · 지도(04:00)와 겹치지 않게. **예시 파일의 주석 `METRICS_MAX_CELLS=2000000` 은 코드 기본(1,000만)과 다르다** — 주석을 그대로 풀면 상한이 1/5 로 준다 |
| 읽기 안전장치 | `READ_QUERY_SECONDS=60` · `READ_WORK_MEM=64MB` · `READ_PARALLEL_WORKERS=4` · `READ_TEMP_LIMIT=5GB` · `LONG_READ_SECONDS=120` · `WHOLE_SLOTS=2` | `READ_QUERY_SECONDS` 는 **MCP 의 읽기 대기(90초)보다 짧게** — 길면 AI 는 안내 대신 끊긴 연결을 받는다. `READ_WORK_MEM` · `READ_TEMP_LIMIT` 은 비우면 서버 설정을 따른다(이 둘은 빈 값이 뜻이 있다) |
| 느린 요청 | `SLOW_REQUEST_P95_SECONDS=3` · `SLOW_REQUEST_MIN_CALLS=5` · `LARGE_RESPONSE_MB=5` | 홈 「남은 일」 이 잡음이 되면 올린다 |
| 그래프 지도 | `GRAPH_MAP_NIGHT_HOUR=4` · `GRAPH_MAP_AUTO=true` · `GRAPH_MAP_MAX_RECORDS=50000000` · `GRAPH_MAP_TEMP_LIMIT=20GB` | 지도 계산이 서버를 버겁게 하면 `GRAPH_MAP_AUTO=false` |
| 밤 그래프 계산(ADR 0067) | `GRAPH_CALC_NIGHT_HOUR=5` · `GRAPH_CALC_AUTO=true` · `GRAPH_CALC_ENGINE=auto` · `GRAPH_CALC_THREADS=0` · `GRAPH_CALC_WHOLE_RELATIONS=`(비움) | `ENGINE` 은 `auto`(GPU 가 보이면 GPU) · `cpu` · `gpu`(못 쓰면 실패 — 말없이 CPU 로 돌지 않는다). GPU 는 **GPU 판 번들**이어야 한다(워커가 `--nv` 로 뜬다). `THREADS=0` 은 코어 전부 — DB · 웹이 버거우면 줄인다. `WHOLE_RELATIONS` 는 무리 · 다리 역할을 지을 선(쉼표 — 관계 slug, 참조 칸은 `<타입>.<칸>`, 비우면 전부). 전부면 공급사 중심, 구성만이면 제품군으로 묶인다. 모르는 이름이면 계산이 실패하고 아는 이름을 말한다 |
| 로그인 · 토큰 | `ACCESS_TOKEN_MINUTES=720` · `REFRESH_TOKEN_DAYS=30` · `LOGIN_DELAY_AFTER=5` · `LOGIN_DELAY_STEP_SECONDS=2` · `LOGIN_DELAY_MAX_SECONDS=30` · `LOGIN_FAILURE_WINDOW_MINUTES=15` | 잠그지 않고 늦추기만 한다(관리자 복구가 서버 콘솔뿐이라) |
| **적지 않는 것** | `REFRESH_COOKIE_NAME` · `FRONTEND_DIST` · `PIPELINE_KIT` · `LOG_DIR` · `FILESTORE_DIR` · `CORS_ORIGINS` | slug 에서 나오거나 이미지가 정한다 |

---

## 3. 꼭 지킬 것

**3.1 BOM 없이 저장한다.** 앱은 `utf-8-sig` 로 읽어 BOM 을 흡수하지만(`config.py model_config`), `deploy.sh` · `backup.sh` 는
`sed '^KEY='` 로 읽어 **BOM 이 붙은 첫 줄의 키를 못 본다.** 첫 줄이 `APP_SLUG=` 이면 update 가 slug 를 「없음」 으로 보고 같은 키를
한 줄 더 붙인다. 틀이 첫 줄을 주석으로 두지만, 메모장 · 원격 편집기로 새로 저장하면 BOM 이 붙고 첫 줄이 키가 될 수 있다. AGENTS.md ·
README 7장의 「운영이 development 로 뜬다」 는 지금 판의 앱에서는 안 난다(앱이 BOM 을 흡수하고, 이미지가 `APP_ENV=production` 을
내보낸다 — 0.2) — 남은 위험은 배포 · 백업 스크립트 쪽이다.

**3.2 줄바꿈은 LF.** 앱은 CRLF 를 푼다. `deploy.sh` 는 값 끝의 `\r` 까지 읽는다 — `APP_SLUG=<slug>\r` 은 설치 기록의 `<slug>` 와
달라 **update 가 「slug 는 설치 뒤 바꿀 수 없습니다」 로 멈추고**, `backup.sh` 는 DB 이름 끝에 `\r` 이 붙어 덤프가 실패한다(개발 PC 에서
`sed` 가 `\r` 을 넘기는 것을 확인). 고치기: `sed -i 's/\r$//' <.env>`. 편집은 서버에서 `nano` · `vim` 으로 한다.

**3.3 줄 끝 주석 · 따옴표를 달지 않는다.** 앱은 `KEY=값 # 메모` 의 주석과 `"…"` 를 풀지만, 배포 · 백업 스크립트는 **주석 · 따옴표까지
값으로 읽는다**(`APP_SLUG=<slug> # 메모` → update 가 멈춘다). 설명은 따로 한 줄 주석으로. 이름에 빈칸이 있어도 따옴표 없이 적는다.

**3.4 빈 값의 뜻은 칸마다 다르다 — 안 쓰는 키는 줄을 지우거나 `#` 으로 막는다.** 개발 PC 에서 `Settings` 로 확인한 것:

| 빈 값(`KEY=`) | 읽히는 것 |
| --- | --- |
| 숫자 · 참거짓 · 목록(`PORT` · `UVICORN_WORKERS` · `TRUST_PROXY` · `REFRESH_COOKIE_SECURE` · `CORS_ORIGINS` …) | **앱이 안 뜬다** |
| 경로(`BACKUP_DIR` · `DATASOURCE_DIR`) | **`.`** — 「안 적음」 이 아니다. 앱의 작업 폴더(`/opt/app/backend`)다 |
| 글자(`PUBLIC_URL` · `PORTAL_*` · `HEAX_*` · `READ_WORK_MEM` …) | 빈 글자 — 대개 「끔」 · 「짐작」 · 「서버 설정」 |

`DATASOURCE_DIR=` 가 특히 위험하다 — `.` 이 허용 폴더가 되면 파일 데이터 소스가 `/opt/app/backend` 아래를 읽을 수 있고, 거기에 `.env` 가
bind 돼 있다(`fetchers.py` 는 허용 폴더 아래의 파일이면 읽는다). 데이터 소스를 만드는 것은 앱의 시스템 관리자뿐이지만, 서버 계정이
없는 그 사람이 서버의 DB 비밀번호 · JWT 비밀을 읽는 길이 생긴다(개발 PC 에서 확인, 2026-11-08 — 빈 값이 `.` 으로 읽히고, `.env` 가
허용 폴더 검사를 통과한다).

**3.5 키 이름의 오타는 조용히 무시된다**(`extra="ignore"`). `PUBLIC_URLS=` · `PORTAL_JWKS=` 는 아무 오류 없이 기본값으로 돈다. 그
판이 모르는 키(예: v0.4.74 의 `PUBLIC_URL`)도 같다. 5.2 의 한 줄로 「이 판이 모르는 키」 를 뽑는다. 확장 이름의 오타만은 서버 화면이 말한다.

**3.6 env 는 `sudo` 뒤에 쓴다** — `sudo X=… ./deploy.sh update`. `X=… sudo ./deploy.sh` 는 우분투 기본 sudo(`env_reset`)가 값을
**조용히 버려** 스크립트는 「안 줬다」 로 보고 기억한 값 · 기본값으로 진행한다(24.04 운영 서버에서 확인, 2026-10-05 — AGENTS.md).

**3.7 `.env` 를 이미지에 굽지 않고, 고친 뒤에는 `restart` 한다.** 설치마다 다른 값이라 bind-mount 로 넣는다(ADR 0004). 프로세스는 뜰 때
한 번만 읽으므로 파일만 고치면 옛 값으로 계속 돌고, 그 사실은 아무 데도 안 뜬다. `systemctl restart <slug>` 는 앱만 — 워커 · MCP 는 옛
값으로 남아 둘이 갈라진다. **`sudo APP_SLUG=<slug> ./deploy.sh restart`** 가 셋을 함께 다시 띄운다.

**3.8 `APP_SLUG` 는 설치 뒤 바꾸지 않고, 다른 설치의 `.env` 를 복사해 새 설치를 만들지 않는다.** DB 이름 · 쿠키 · PAT 표식 · 유닛이 slug
에서 나온다. 복사해 고치다 하나를 빠뜨리면 조용한 사고다 — `DATABASE_URL` 의 DB 이름 · 사용자가 남으면 **새 설치가 옆 설치의 DB 를
마이그레이션한다.** `JWT_SECRET` 은 `install` 이 설치마다 난수로 만들어, 따로 설치하면 저절로 다르다. **이중화 A · B 가 한 값을 쓰는
것은 같은 설치라서 정상이고, 꼭 그래야 한다**(0.1). 걸리는 것은 다른 설치의 `.env` 를 복사했을 때뿐이다 — 토큰에 설치 표식(`aud` ·
`iss`)이 없어(`auth/security.py`) 같은 비밀을 쓰는 두 설치는 서로의 토큰 서명을 받아 준다. 사용자 id(UUID)도 설치마다 달라 그것만으로는
로그인되지 않지만, 백업으로 DB 까지 복제한 설치라면 한쪽의 토큰으로 다른 쪽에 로그인된다(코드로 읽은 것). `HEAX_SSO_SECRET` 도 같다.
새 설치는 `install` 이 `.env` 를 만들게 둔다.

**3.9 이름 · 소개 · 확장 기본값은 `update` 로 바꾼다 — 손으로 고치면 되돌아간다.** 이 셋(`APP_NAME` · `APP_TAGLINE` · `EXTENSIONS`)은
설치 기록(`/etc/platform-instances/<slug>.conf`)이 정본이고 `.env` 의 줄은 그 사본이다. `install` · `update` 가 매번 env > 설치 기록 >
번들 기본값으로 다시 적는다(`sync_env_identity`) — 의도한 동작이다. `APP_SLUG` 는 견주기만 한다. **`.env` 의 다른 줄은 `update` 가
건드리지 않는다**(env 로 준 `PORTAL_*` · `HEAX_SSO_ALLOWED_IPS` 만 더 적는다 — 0.3). 바꾸려면 `sudo APP_NAME="…" ./deploy.sh update` —
설치 기록에도 남는다. **이중화는 설치
기록이 서버마다 따로**라, 한 서버에서만 바꾸면 다른 서버의 다음 update 가 공용 `.env` 를 옛 이름으로 되돌린다 — 두 서버의 update 에 같은
값을 준다(코드로 읽은 것).

**3.10 포트는 플랫폼마다 10씩, 설치 뒤에 바꾸면 네 군데다.** MCP 가 +2 를 쓰므로 8040 · 8042 를 나란히 고르면 겹친다 — deploy 가 설치
기록과 견줘 멈춘다. 설치 뒤 `APP_PORT` 를 env 로 줘도 **`.env` 의 `PORT` 는 안 바뀌고**(그 줄은 `.env` 를 처음 만들 때만 적힌다), MCP
유닛은 기억한 `PLATFORM_API_BASE` · `MCP_PORT` 를 그대로 쓴다. 바꿀 때는 한 번에:

```bash
sudo -u <계정> sed -i 's/^PORT=.*/PORT=<새 포트>/' <.env>
sudo APP_SLUG=<slug> APP_PORT=<새 포트> MCP_PORT=<새 포트+2> MCP_API_BASE=http://127.0.0.1:<새 포트> ./deploy.sh update
# 그리고 nginx 조각(main-server-nginx.conf) · 포털의 라우트 · 방화벽 · MCP_PUBLIC_URL · PUBLIC_URL(포트를 적었으면)
```

(코드로 읽은 순서다 — 운영에서 포트를 옮겨 본 적은 없다. 확인 필요.)

**3.11 실행 비트 · 줄바꿈은 스크립트에도.** Windows 쪽(`\\wsl.localhost\…`)에서 고친 셸 스크립트는 모드가 644 로 떨어져 다음 배포에서
`Permission denied`, CRLF 면 `bad interpreter: /usr/bin/env bash^M` 로 죽는다 — 파일도 실행 비트도 있고 눈으로 보면 멀쩡하다. 번들에서 푼
것을 그대로 쓴다(`.gitattributes` · `tests/architecture/test_deploy.py`). 5장의 점검 스크립트를 옮겨 쓸 때도 같다.

**3.12 비밀의 자리를 안다.** 비밀은 `.env` 밖에도 있다.

| 자리 | 무엇 | 권한 |
| --- | --- | --- |
| `<.env>` | JWT 비밀 · DB 비밀번호 · 위임 비밀 | 600(install) |
| `<백업 폴더>/env/.env` | 위의 사본(복구하면 전원 재로그인을 피하려고) | 600(`backup.sh`) |
| `<백업 폴더>/db/db-*.dump` | DB 전부 — **데이터 소스의 인증 비밀(`data_sources.auth`)과 웹훅 서명 비밀(`webhooks.secret`)이 평문으로 든다**(암호화하는 코드가 없다). 계정의 비밀번호 해시 · 토큰 해시도 | **다른 계정이 읽을 수 있다**(개발 PC 에서 재현, 2026-11-08 — 덤프 644 · 폴더 755). `backup.sh` · 백업 유닛에 `umask` · `UMask` 가 없고, `deploy.sh ensure_dirs` 도 폴더를 좁히지 않는다. 고쳐지기 전까지는 `chmod 700 <백업 폴더>` · `chmod 600 <백업 폴더>/db/*.dump` |
| `<DATA_DIR>/filestore/` | 첨부 원본(문서 · 그림) | 위와 같다 — 755 로 만들어진다. 서버에 다른 계정이 있으면 `chmod 700` |
| `/home/<계정>/apps/<slug>/setup.log` | 관리자 임시 비밀번호 | 600 |
| `/home/<계정>/apps/<slug>/handoff/`(이중화 A, 공용 폴더 없을 때) | `.env` · 복제 비밀번호 사본 — B 의 setup 이 가져간 뒤에도 **남는다** | 700 · 600. B 가 선 뒤에는 지운다(남겨 두면 A 의 `.env` 를 고쳐도 옛 사본이 남는다) |

비밀을 `deploy.sh` 의 env 로 주지 않는다(화면에 찍히고 셸 기록에 남는다). 화면은 DB 접속 문자열의 비밀번호를 가려서 보인다.

**3.13 형태를 옮기면 `.env` 를 손으로 맞춘다.** `.env` 는 **없을 때만** 만들어진다. 단독 → 이중화로 옮기면 `PUBLIC_PATH` · `TRUST_PROXY` ·
`BACKUP_DIR` 을, 나중에 `BACKUP_HOST_DIR` 을 주면 `BACKUP_DIR=/data/backup` 을 손으로 적는다. 단독 서버에서 준 `BACKUP_HOST_DIR` 폴더는
deploy 가 만들지 않는다 — 유닛이 그 폴더를 bind 하므로 없으면 앱이 못 뜬다(apptainer 가 `mount source … doesn't exist` 로 거부한다 —
개발 PC 에서 확인, 2026-11-08). 먼저 `mkdir`.

**3.14 `update` 는 그 번들의 판으로 바꾼다 — 옛 번들 폴더에서 치지 않는다.** `update` 는 그 번들의 SIF 로 바꾸고 마이그레이션을
돌린다. **서비스를 내리기 전에** 네 가지를 본다(셋은 v0.4.76, 마지막은 v0.4.77 부터 — README_OPERATOR 「갱신」). 갱신
**전에** 번들의 `CHANGES.md`(판 기록 — 바뀐 것 · 마이그레이션 · 새로 생긴 `.env` 값)를 먼저 읽는다.

- 판이 내려가면 멈춘다 — 「판이 내려갑니다: 지금 v0.4.75 → 이 번들 v0.4.74 — 옛 번들 폴더에서 친 것 아닌가요?」. 정말 내리려면
  `ALLOW_DOWNGRADE=1`. 예전에는 DB 구조가 같으면 「갱신 완료」 라며 조용히 판이 내려갔다.
- 새 SIF 를 옆(`app.sif.new`)에 놓는다 — 디스크가 차면 여기서 멈춘다.
- 새 이미지가 지금 DB 의 리비전을 아나 — DB 가 더 앞서 있으면 `Can't locate revision` 을 보이고 멈춘다. 예전에는 서비스를 내린 뒤
  마이그레이션에서 실패해 **내려간 채** 멈췄다.
- 이번 갱신의 DB 마이그레이션 — 지금 DB 부터 세어 보인다. 데이터를 지우는 것(⚠)이 있으면 최근 백업을 보이고 묻는다(터미널이
  아니면 멈춘다 — 백업을 확인했으면 `CONFIRM_DROPS=1`).

내린 뒤에 실패하면 직전 SIF 로 되돌리고 다시 켠다(마이그레이션은 한 트랜잭션이라 DB 는 그대로). 같은 판을 다시 칠 때는
`app.sif.prev` 를 덮지 않는다. 그래도 `.env` 만 바꿀 때는 손으로 고치고 `restart`(SIF 를 안 바꾼다)가 가장 가볍다.

**3.15 끈 타이머 · MCP 는 다음 `update` 가 다시 켠다.** `SYNC_ENABLED` · `METRICS_ENABLED` · `MCP_ENABLED` 는 기억되지 않고 기본이 1 이다 —
끈 채로 두려면 update 마다 `=0` 을 준다. 작업 워커만은 운영자가 `disable` 한 것을 지킨다(ADR 0049).

**3.16 `DATABASE_URL` 은 `backup.sh` 가 쪼갤 수 있는 꼴로.** `backup.sh` · `restore.sh` 는 `://<사용자>:<비밀번호>@<호스트>:<포트>/<DB>`
정규식으로 읽고 URL 디코딩을 하지 않는다. 포트를 생략하거나, 비밀번호에 `@` 가 있거나, `?sslmode=…` 를 붙이면 백업이 멈추거나 엉뚱한 DB
이름을 받는다. 비밀번호를 손으로 바꿀 때는 영문 · 숫자 · `-` · `_` 만(install 이 그렇게 만든다).

**3.17 `TRUST_PROXY=true` 인 설치는 앱 포트를 방화벽으로 좁힌다** — 프록시 · 포털 박스 · 상대 서버만. 머리를 믿는 설정이라 앱 포트에
직접 닿는 누구나 접속 주소 · https 판정을 꾸밀 수 있다(위임 발급은 그런 요청을 따로 거절한다).

---

## 4. 일괄 적용 · 확인

### 4.1 어느 명령이 `.env` 를 쓰고, 다시 읽나

| 명령 | `.env` 를 쓰나 | 다시 읽나 |
| --- | --- | --- |
| `sudo ./deploy.sh restart`(· `start` · `stop`) | 안 쓴다 | 앱 · 작업 워커 · MCP 를 함께 다시 띄운다 — **`.env` 를 고친 뒤 이것.** 꺼 둔 워커는 안 띄운다. SIF 를 안 바꾸므로 어느 판의 번들 폴더에서 쳐도 된다 |
| `sudo ./deploy.sh update` | 정체 넷을 다시 적고, env 로 준 `PORTAL_*` · `HEAX_SSO_ALLOWED_IPS` 를 적는다(0.3) | SIF 교체 · 마이그레이션 · 전부 다시 띄운다(3.14) |
| `sudo ./deploy.sh install` | 없으면 만든다 + update 와 같은 동기 | 띄운다 |
| `./deploy.sh hwax-request` | 위임 비밀이 없으면 만든다 | **안 읽는다** — 만들었으면 `restart`(이중화면 두 서버) |
| `sudo ./deploy.sh ollama` | 안 쓴다 | 뜻으로 찾기 셋을 읽어 Ollama 만 갖춘다(앱은 그대로) |
| `status` · `units` · `render` · `lb` · `pgvector` | 안 쓴다 | 안 읽는다(`status` 는 `/api/health` 를 부를 뿐) |
| 동기화 · 지표 · 백업 타이머 | — | **돌 때마다** 새로 읽는다(앱 재시작과 상관없다) |

### 4.2 한 설치 맞추기

```bash
E=<.env>                                                   # 0.1
sudo cp -p "$E" "$E.bak-$(date +%F)"                       # 사본 — -p 가 600 을 지킨다. 사본도 비밀이다, 끝나면 지운다
sudo -u <계정> nano "$E"                                   # 서버에서 편집(메모장 · 원격 편집기는 BOM · CRLF)
sudo ./env-check.sh "$E"                                   # 5장 — 오류가 없을 때까지
sudo APP_SLUG=<slug> ./deploy.sh restart                   # 정체 넷 · 포트 · PORTAL_* 를 env 로 바꿀 때만 update(3.9 · 3.10 · 3.14)
```

### 4.3 확인

**`/api/health`** — 로그인 없이 받는다. 접두어가 있는 설치도 앱 포트에는 접두어 없이 물어도 된다(`PrefixMiddleware`).

```bash
curl -s http://127.0.0.1:<포트>/api/health; echo
# {"status":"ok","version":"<판>","app":"<이름>","slug":"<slug>","base_path":"/<slug>","public_url":"https://<포털 호스트>/<slug>"}
curl -s https://<공용 호스트>/<slug>/api/health; echo     # 이중화 — nginx 경유
curl -s https://<포털 호스트>/<id>/api/health; echo       # 포털 경유
```

| 칸 | 보는 것 |
| --- | --- |
| `version` | 깐 번들의 판(`BUILD_INFO`). 이중화면 두 서버가 같나 |
| `slug` · `app` | 그 포트에 그 설치가 떠 있나 — 포트가 엇갈리면 여기서 드러난다 |
| `base_path` | 단독 `""`, 이중화 · 포털 `"/<slug>"` |
| `public_url` | 적은 `PUBLIC_URL`(또는 2 · 3번의 짐작). **`null`** 이면 백엔드가 모른다 — MCP 가 들어온 주소로 짐작한다(2.4.1 의 4번). **칸이 없으면** `PUBLIC_URL` 이전 판이다 |

**서버 화면**(관리 › 서버, 시스템 관리자):

- 「환경」 = `production`, **「기동 시각」 = 방금**(restart 가 먹었나), 「데이터베이스」(비밀번호를 가린 접속 문자열) = 자기 slug 의 DB 인가.
- 스키마가 코드보다 뒤처졌다는 줄이 없나 · 「마지막 백업」 이 하루 안인가(`BACKUP_DIR`).
- 「확장 모듈」 의 「.env 에 적혔는데 없는 이름」 · 「뜻으로 찾기」 가 준비인가.
- (포털) 「HWAX 포털 로그인」 — 켜짐 · 공개 키가 닿나 · 문제 목록(접두어 없음 · 접두어와 audience 다름 · `TRUST_PROXY` 꺼짐 · `MCP_PUBLIC_URL` ·
  위임 비밀이 짧음 · 허용 주소를 못 읽음), 「게이트웨이 위임」 의 마지막 발급 · 거절.

**MCP**:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<MCP 포트>/mcp    # 406 · 400 이면 산다(스트리밍 규약). 404 면 앱에 물었다
sudo APP_SLUG=<slug> ./deploy.sh status                                      # MCP 줄 · 주소
```

그다음 Claude 에서 목록 하나를 묻고, 답의 `url` 이 `public_url` 로 시작하는지 · **눌러서 같은 화면이 열리는지** 본다. 포털이면 위임 점검
`POST /api/auth/sso/verify` 가 204 인지(README 9.3).

### 4.4 여러 설치(slug)를 한 번에

1. **목록을 본다** — `ls /etc/platform-instances/`. 설치마다 `.env` 가 따로다(0.1).
2. **지금 상태를 남긴다** — `sudo ./env-check.sh`(인자 없이 — 이 서버의 설치 전부) 출력 · 각 `/api/health` 를 적어 둔다.
3. **사본** — 설치마다 4.2 의 `cp -p`.
4. **하나 먼저** — 가장 덜 쓰는 설치에서 고치고 → 점검 → `restart` → 4.3 의 확인까지 끝낸다. 같은 틀의 실수를 열 번 하지 않으려고.
5. **나머지는 하나씩** — 같은 서버의 설치들은 PostgreSQL · Ollama 를 함께 쓴다. 한꺼번에 내리면 어느 것이 왜 안 떴는지 가르기 어렵다.
   하나가 안 뜨면 거기서 멈춘다:

   ```bash
   for s in $(ls /etc/platform-instances | sed 's/\.conf$//'); do
       sudo APP_SLUG="$s" ./deploy.sh restart || { echo "$s 에서 멈춤 — journalctl -u $s -n 50"; break; }
   done
   ```

6. **끝에 다시** — `sudo ./env-check.sh` 와 각 `/api/health`. 사본을 지운다.

`.env` 맞추기와 판 갱신(`update`)은 섞지 않는다 — 함께 하면 무엇 때문에 안 뜨는지 가를 수 없다.

### 4.5 이중화에서

- **공용 폴더(`DATA_DIR`)가 있으면** `.env` 는 한 벌이다 — 한 서버에서 고치고, B `restart` → 확인 → A `restart`. 한 대씩이라 서비스는
  안 끊긴다(nginx 가 멈춘 쪽을 뺀다). 정체 넷 · 포트를 env 로 바꿀 때는 **두 서버의 update 에 같은 값**을(3.9).
- **공용 폴더가 없으면(리허설)** `.env` 가 두 벌이다 — 둘을 같게 고친다. `JWT_SECRET` · `DATABASE_URL` 이 다르면 한 사람이 두 서버를 오가며
  로그인이 풀린다. 양쪽에서 `sudo sha256sum <.env>` 를 견준다.
- `/data` 가 안 보이는 동안은 `restart` 하지 않는다 — 앱이 `.env` 를 못 읽어 안 뜬다(떠 있는 앱은 계속 돈다, README 8.3).

---

## 5. 점검 스크립트

### 5.1 `env-check.sh`

`.env` 를 **읽기만** 한다. 정본은 `deploy/env-check.sh` 이고 번들 폴더에 함께 담긴다 — 번들 폴더에서 `sudo ./env-check.sh`
로 돌린다(아래 블록은 같은 내용의 사본 — 다르면 파일이 맞다). 인자 없이 돌리면 `/etc/platform-instances/*.conf` 가 가리키는 `.env` 를 전부(deploy.sh 와 같은 규칙 —
`DATA_DIR` 이 있으면 거기, 없으면 설치 폴더), 인자로 주면 그 파일만 본다. 「오류」 가 하나라도 있으면 종료 코드 1.

개발 PC 에서 가짜 설치 셋(정상 · BOM+CRLF+빈 값+틀린 꼴 · 줄 끝 주석+포트 겹침)으로 돌려 기대한 줄이 나오는 것만 봤다 — **운영 서버에서는
아직 안 돌렸다.**

```bash
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
```

**보는 것**: BOM · CRLF · `REPLACE_` 자리 · 꼭 있어야 할 여섯 키 · 두 번 적힌 키 · 빈 값이면 앱이 안 뜨거나 `.` 이 되는 키 · 배포가 읽는 키의 줄 끝
주석 · slug 의 꼴과 설치 기록 · JWT 비밀(기본값 · 길이) · `DATABASE_URL` 의 꼴(backup.sh 기준)과 DB 이름 · `VIEW_SCOPE` · `EMBEDDING_BACKEND` 의 낱말 ·
`.env` 의 `PORT` 와 설치 기록의 `APP_PORT` · 이 서버의 설치끼리 앱 · MCP 포트 겹침 · `PUBLIC_URL` 의 경로와 `PUBLIC_PATH` · 포털 뒤의
`PUBLIC_PATH` · `TRUST_PROXY` · `PUBLIC_URL` · `MCP_PUBLIC_URL` · 위임 비밀 길이.

**안 보는 것**: 값이 실제로 닿나(JWKS · DB · Ollama — 서버 화면과 4.3 의 몫), `HEAX_SSO_ALLOWED_IPS` 의 꼴(서버 화면이 말한다), 다른 서버와의
겹침, 키 이름의 오타(5.2), 이 서버가 아닌 곳에서 쓰는 포트(`ss -ltnp` 로 본다). `ETC=<폴더>` 는 시험용 접두어다(deploy.sh 의 `render` 와 같다).

### 5.2 이 판이 모르는 키 — 오타 찾기

앱은 모르는 키를 조용히 버리므로(3.5), 그 설치의 SIF 에게 아는 키를 묻고 `.env` 와 견준다. SIF 를 읽기만 하므로 운영 계정으로 된다.

```bash
D=/home/<계정>/apps/<slug>
apptainer exec "$D/app.sif" sh -c 'cd /opt/app/backend && /opt/app/venv/bin/python -c "from app.config import Settings; print(\"\n\".join(k.upper() for k in Settings.model_fields))"' > /tmp/known-keys
sudo grep -oE '^[A-Z][A-Z0-9_]*' <.env> | LC_ALL=C sort -u | LC_ALL=C comm -23 - <(LC_ALL=C sort /tmp/known-keys)
```

나오는 이름은 오타이거나 **그 판이 아직 모르는 키**다(v0.4.74 SIF 면 `PUBLIC_URL` 이 나온다). 개발 PC 의 옛 SIF 로 명령이 도는 것까지 봤다.
