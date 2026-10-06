"""파이프라인 도구는 패키지가 아니라 **한 파일씩**이다 — 사용자 PC 에서 그 자리에서 돈다.
시험도 그 파일들을 그대로 집어 오도록 폴더를 길에 넣는다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 정제 도구 키트의 **이 PC 설정**(등록한 플랫폼 · 토큰)을 시험이 읽지 않게 — 없는 자리.
os.environ["SP_SETTINGS"] = os.path.join(os.devnull, "sp-pipeline-settings.json")
