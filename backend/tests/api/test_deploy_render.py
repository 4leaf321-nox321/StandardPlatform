"""배포 스크립트가 만드는 유닛 · nginx · keepalived 설정 — **root 없이 렌더만** 해서 본다.

서버 두 대에 올리는 설정은 서버에 가서야 틀린 것이 드러난다(그리고 그때는 SSH 로 붙어 있는
자리다). `deploy.sh render` 가 아무것도 바꾸지 않고 결과만 쓰므로, 여기서 모양을 잠근다."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"

# 기본 — 메인 서버(포탈 + nginx)가 로드밸런서. A · B 에는 앱 · MCP · DB 만.
HA_ENV = {
    "HA_ROLE": "master",
    "PEER_IP": "10.0.0.2",
    "SELF_IP": "10.0.0.1",
    "DB_VIP": "10.0.0.11",
    "PUBLIC_HOST": "portal.example.local",
    "VRRP_IFACE": "eth0",
    "DATA_DIR": "/data/common/testplatform",
}
# 메인 서버가 없을 때 — A · B 자체에 nginx + 웹 VIP.
LOCAL_ENV = {**HA_ENV, "LB_MODE": "local", "WEB_VIP": "10.0.0.10"}


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """릴리스 번들의 모양 — build_bundle.sh 가 담는 것 중 렌더에 필요한 것만."""
    stage = tmp_path / "bundle"
    stage.mkdir()
    for name in ["deploy.sh", "ha.sh", "pg-ha.sh", "backup.sh", "restore.sh"]:
        shutil.copy(DEPLOY / name, stage / name)
    for tpl in DEPLOY.glob("*.template"):
        shutil.copy(tpl, stage / tpl.name)
    (stage / "BUILD_INFO").write_text(
        "app_name=Test Platform\napp_slug=testplatform\nport=8040\nmcp_port=8042\nversion=t\n",
        encoding="utf-8",
    )
    return stage


def read(etc: Path, rel: str) -> str:
    return (etc / "etc" / rel).read_text(encoding="utf-8")


def render(bundle: Path, etc: Path, **env: str) -> subprocess.CompletedProcess[str]:
    full = {
        **os.environ,
        "OPERATOR": "ops",
        "INSTALL_DIR": "/opt/testplatform",
        "ETC": str(etc),
        **env,
    }
    return subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "render"],
        cwd=bundle,
        env=full,
        capture_output=True,
        text=True,
        check=True,
    )


def test_단독_서버는_설치_폴더_하나에_전부(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, INSTALL_DIR="/home/ops/apps/testplatform")
    unit = (etc / "etc/systemd/system/testplatform.service").read_text(encoding="utf-8")
    assert "--bind /home/ops/apps/testplatform/.env:/opt/app/backend/.env:ro" in unit
    assert "--bind /home/ops/apps/testplatform/filestore:/data/filestore" in unit
    assert "--bind /home/ops/apps/testplatform/logs:/data/logs" in unit
    # 백업 폴더를 모르면 그 --bind 줄이 **아예 없다** — 빈 줄이면 ExecStart 가 거기서 끊긴다.
    assert ":/data/backup" not in unit
    assert "@@" not in unit
    # 로컬 PostgreSQL 에 묶지 않는다 — 이중화에서는 DB 가 상대 서버에 있다.
    assert not [x for x in unit.splitlines() if x.startswith("Requires=")]
    assert not (etc / "etc/nginx").exists()
    assert not (etc / "etc/keepalived").exists()
    # 단독 서버에도 워커는 선다 — 가져오기가 도는 곳이다.
    worker = read(etc, "systemd/system/testplatform-worker.service")
    assert "--bind /home/ops/apps/testplatform/.env:/opt/app/backend/.env:ro" in worker


def test_이중화는_공용_폴더와_로컬을_가른다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **HA_ENV)
    unit = (etc / "etc/systemd/system/testplatform.service").read_text(encoding="utf-8")
    assert "--bind /data/common/testplatform/.env:/opt/app/backend/.env:ro" in unit
    assert "--bind /data/common/testplatform/filestore:/data/filestore" in unit
    assert "--bind /opt/testplatform/logs:/data/logs" in unit
    assert "--bind /data/common/testplatform/backup:/data/backup \\" in unit
    lines = unit.splitlines()
    exec_start = lines.index(next(x for x in lines if x.startswith("ExecStart=")))
    # ExecStart 부터 app.sif 까지 빈 줄 없이 이어진다.
    tail = lines[exec_start:]
    end = tail.index(next(x for x in tail if x.strip().endswith("app.sif")))
    assert all(x.strip() for x in tail[: end + 1])

    # **워커가 유닛으로 서지 않으면 일괄 입력이 영영 대기다.** 앱과 같은 SIF · 같은 .env.
    worker = read(etc, "systemd/system/testplatform-worker.service")
    assert "--bind /data/common/testplatform/.env:/opt/app/backend/.env:ro" in worker
    assert "python -m app.worker" in worker
    assert "@@" not in worker

    sync = read(etc, "systemd/system/testplatform-sync.service")
    assert "--bind /data/common/testplatform/.env" in sync
    backup = read(etc, "systemd/system/testplatform-backup.service")
    assert (
        "backup.sh -e /data/common/testplatform/.env -f /data/common/testplatform/filestore"
        " -o /data/common/testplatform/backup" in backup
    )


def test_메인_서버에_넘길_nginx_조각(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **HA_ENV)
    # A · B 에는 nginx 가 없다 — 메인 서버 쪽에 넣을 조각만.
    assert not (etc / "etc/nginx").exists()
    snippet = (etc / "main-server-nginx.conf").read_text(encoding="utf-8")
    assert "server 10.0.0.1:8040" in snippet and "server 10.0.0.2:8040" in snippet
    assert "server 10.0.0.1:8042" in snippet and "server 10.0.0.2:8042" in snippet
    # 접두어를 벗기는 것(끝의 '/')과 https 를 알리는 것 — 이 둘이 없으면 앱이 못 맞춘다.
    assert "location /testplatform/ {" in snippet
    assert "proxy_pass http://testplatform_app/;" in snippet
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in snippet
    assert "location /testplatform/mcp {" in snippet
    assert "proxy_pass http://testplatform_mcp/mcp;" in snippet
    assert "proxy_buffering off;" in snippet
    # keepalived 는 DB VIP 만.
    conf = read(etc, "keepalived/keepalived.conf")
    assert "VI_DB" in conf and "VI_WEB" not in conf and "chk_nginx" not in conf
    dropin = read(etc, "systemd/system/keepalived.service.d/platform-ha.conf")
    assert "After=postgresql.service\n" in dropin


def test_로컬_LB_는_접두어를_떼고_두_앱에_나눈다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **LOCAL_ENV)
    upstream = read(etc, "nginx/conf.d/testplatform-upstream.conf")
    assert "server 10.0.0.1:8040" in upstream and "server 10.0.0.2:8040" in upstream
    assert "server 10.0.0.1:8042" in upstream and "server 10.0.0.2:8042" in upstream

    site = (etc / "etc/nginx/platforms.d/testplatform.conf").read_text(encoding="utf-8")
    # 접두어를 떼고 넘긴다 — 앱은 PUBLIC_PATH 로 화면 · 쿠키만 맞춘다(FastAPI root_path).
    assert "location /testplatform/ {" in site
    assert "proxy_pass http://testplatform_app/;" in site
    assert "location /testplatform/mcp {" in site
    assert "proxy_pass http://testplatform_mcp/mcp;" in site
    assert "proxy_buffering off;" in site
    assert "X-Forwarded-Proto $scheme" in site

    host = (etc / "etc/nginx/sites-available/platform-ha").read_text(encoding="utf-8")
    assert "server_name portal.example.local 10.0.0.10 10.0.0.1 10.0.0.2 _;" in host
    assert "return 301 https://$host$request_uri;" in host
    assert "include /etc/nginx/platforms.d/*.conf;" in host
    assert "listen 443 ssl http2 default_server;" in host
    assert (etc / "etc/nginx/sites-enabled/platform-ha").is_symlink()


def test_keepalived_는_주_DB_가_항상_이긴다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **LOCAL_ENV)
    conf = (etc / "etc/keepalived/keepalived.conf").read_text(encoding="utf-8")
    assert "virtual_router_id 51" in conf and "virtual_ipaddress { 10.0.0.10 }" in conf
    assert "virtual_router_id 52" in conf and "virtual_ipaddress { 10.0.0.11 }" in conf
    assert "unicast_src_ip 10.0.0.1" in conf and "unicast_peer { 10.0.0.2 }" in conf
    # 웹 VIP 는 master 역할이 150, DB VIP 는 101 + 주(check-primary) 50.
    assert "priority 150" in conf and "priority 101" in conf
    assert 'script "/usr/local/sbin/pg-ha check-primary"' in conf and "weight 50" in conf
    assert 'notify_master "/usr/local/sbin/pg-ha on-master"' in conf
    # keepalived 는 DB · nginx 뒤에 뜬다 — 재부팅이 승격이 되지 않게.
    dropin = etc / "etc/systemd/system/keepalived.service.d/platform-ha.conf"
    assert "After=postgresql.service nginx.service" in dropin.read_text(encoding="utf-8")

    other = tmp_path / "etc-b"
    swapped = {**LOCAL_ENV, "HA_ROLE": "backup", "SELF_IP": "10.0.0.2", "PEER_IP": "10.0.0.1"}
    render(bundle, other, **swapped)
    conf_b = read(other, "keepalived/keepalived.conf")
    assert "state BACKUP" in conf_b and "priority 100" in conf_b
    assert "priority 150" not in conf_b


def test_DB_VIP_가_없으면_자동_승격_인스턴스가_없다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **{**LOCAL_ENV, "DB_VIP": ""})
    conf = (etc / "etc/keepalived/keepalived.conf").read_text(encoding="utf-8")
    assert "VI_WEB" in conf and "VI_DB" not in conf and "on-master" not in conf
    # 메인 서버가 LB 이고 DB VIP 도 없으면 keepalived 자체가 없다.
    plain = tmp_path / "etc-plain"
    render(bundle, plain, **{**HA_ENV, "DB_VIP": ""})
    assert not (plain / "etc/keepalived").exists()
    assert (plain / "main-server-nginx.conf").exists()


def test_스크립트_문법(bundle: Path) -> None:
    for name in ["deploy.sh", "ha.sh", "pg-ha.sh", "backup.sh", "restore.sh"]:
        subprocess.run(["bash", "-n", str(bundle / name)], check=True)


def test_번들_하나로_인스턴스_여럿(bundle: Path, tmp_path: Path) -> None:
    """slug · 이름 · 포트 · 확장은 번들이 아니라 설치가 준다. 같은 번들에서 두 인스턴스를
    렌더하면 유닛 · 경로 · 포트 · 메인 서버 조각이 전부 갈리고, 준 값은 인스턴스 파일에 남아
    다음 배포가 기억한다."""
    etc = tmp_path / "etc"
    one = {**HA_ENV, "APP_SLUG": "plmhub", "APP_NAME": "PLM & 기준정보", "APP_PORT": "8050"}
    render(bundle, etc, **one, EXTENSIONS="sample")
    two = {**HA_ENV, "APP_SLUG": "simtools", "APP_NAME": "시뮬레이션", "APP_PORT": "8060"}
    render(bundle, etc, **{**two, "DATA_DIR": "/data/simtools"})

    units = etc / "etc/systemd/system"
    assert (units / "plmhub.service").exists() and (units / "simtools.service").exists()
    assert "/opt/testplatform/app.sif" in read(etc, "systemd/system/plmhub.service")
    snippet = (etc / "main-server-nginx.conf").read_text(encoding="utf-8")
    # 마지막에 렌더한 인스턴스의 조각 — 포트 +2 가 MCP.
    assert "server 10.0.0.1:8060" in snippet and "server 10.0.0.1:8062" in snippet
    assert "location /simtools/ {" in snippet

    saved = read(etc, "platform-instances/plmhub.conf")
    assert "APP_NAME=PLM & 기준정보" in saved and "APP_PORT=8050" in saved
    assert "EXTENSIONS=sample" in saved
    assert "DATA_DIR=/data/simtools" in read(etc, "platform-instances/simtools.conf")

    # 두 인스턴스가 있으면 slug 없이는 못 고른다.
    with pytest.raises(subprocess.CalledProcessError) as failed:
        render(bundle, etc)
    assert "APP_SLUG=<slug>" in failed.value.stderr


def test_setup_은_물어보고_계획을_보여준다(bundle: Path, tmp_path: Path) -> None:
    """운영자가 env 이름과 순서를 외우지 않게 — 답한 것으로 계획을 만들고, --plan 은
    거기서 멈춘다."""
    etc = tmp_path / "etc"
    env = {**os.environ, "OPERATOR": "ops", "ETC": str(etc), "SELF_IP": "10.0.0.1"}
    answers = "A\nplmhub\nPLM 기준정보\n8050\nsample\n10.0.0.2\n\n\n"
    out = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "setup", "--plan"],
        cwd=bundle,
        env=env,
        input=answers,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "PLM 기준정보 (plmhub) · 포트 8050 · 확장 sample" in out
    assert "주(A) 10.0.0.1 — 상대 B 10.0.0.2" in out
    assert "db-primary" in out and "B 에 넘길 파일" in out
    # 대기는 이름 · 포트를 다시 묻지 않는다 — A 의 .env 를 그대로 받는다.
    answers_b = "B\nplmhub\n10.0.0.1\n\n\nops\n\n"
    out_b = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "setup", "--plan"],
        cwd=bundle,
        env={**env, "SELF_IP": "10.0.0.2"},
        input=answers_b,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "이름 · 포트 · 확장은 A 의 설정을 그대로" in out_b
    assert "대기(B) 10.0.0.2 — 주 A 10.0.0.1" in out_b and "db-standby" in out_b
    # 이상한 slug 는 계획 전에 막는다.
    bad = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "setup", "--plan"],
        cwd=bundle,
        env=env,
        input="A\nPLM Hub\n",
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0 and "소문자" in bad.stderr
