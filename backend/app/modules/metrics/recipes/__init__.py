"""분석 — 지표의 셀 위에서 추론한다(ADR 0014).

레시피 하나는 **순수 함수**(배열 → 결과, DB 를 모른다)와 셀을 읽는 얇은 어댑터다. 순수 함수라야
정답을 아는 합성 자료(와이블 β=1.5 로 뽑은 코호트, 계단 하나를 심은 추이)로 DB 없이 시험한다.

레시피 모듈은 import 될 때 `registry` 에 스스로 등록한다 — 여기서 전부 import 한다.
"""

from app.modules.metrics.recipes import assoc, changes, control, life, logit, pareto, sprt

__all__ = ["assoc", "changes", "control", "life", "logit", "pareto", "sprt"]
