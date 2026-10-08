# {SYSTEM} 코어 연동 키트

「{SYSTEM_NAME}」 이 외부에 공개한 기준정보를 **주기적으로 수신**하기 위한 키트입니다.
수신 측에서 작성할 코드는 없습니다 — 설정 파일 한 곳만 수정하고 실행합니다.

- 연동 주소(API 루트): `{BASE}` — 카탈로그는 `{BASE}/core`
- 공개 타입: {TYPES_LONG}
- 정의 판(revision): `{REVISION}`
- 생성 시각: {AT}

## 1. 준비 (5분)

```bash
pip install requests
cp config.example.ini config.ini
# config.ini 의 token 에 발급받은 액세스 토큰을 입력합니다(범위 core:read).
```

토큰은 공개 측 관리자가 시스템마다 개별 발급합니다. 파일에 기록하지 않으려면
환경변수 `SP_TOKEN` 을 사용합니다.

## 2. 연결 점검

```bash
SP_TOKEN=발급받은토큰 ./check.sh
```

세 가지를 확인합니다 — 인증 없는 호출이 거부되는가, 카탈로그를 읽을 수 있는가,
첫 페이지를 받을 수 있는가. 여기까지 통과하면 연결은 완료입니다.

## 3. 수신

```bash
python sp_core_pull.py --config config.ini          # 증분 수신
python sp_core_pull.py --config config.ini --full   # 처음부터 다시 수신
```

첫 실행은 전량을, 이후에는 **직전 수신 이후 변경분만** 받습니다. 결과는 `out/` 에
저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `out/<타입>.csv` | **수신 이력** — 받은 행을 이어 적습니다(같은 `key` 가 여러 번 나옵니다) |
| `out/<타입>-relations.csv` | 그 타입에서 **출발하는 선**의 수신 이력(관계가 열린 타입만) |
| `out/sp_core.sqlite` | **현재 상태** — 객체는 `key` 기준, 선은 `src·relation·dst` 기준으로 갱신됩니다. 삭제 행은 `deleted=1`, **끊긴 선은 지웁니다** |
| `out/state.json` | 어디까지 받았는지(수신 기준 시각) |

객체만 받으면 점만 있고 **선이 없습니다.** 카탈로그의 `relations` 에 값이 있는 타입은
`GET /core/<타입>/relations` 도 같은 규칙(`since` · `next` · `as_of`)으로 받습니다 —
클라이언트가 이미 그렇게 합니다.

## 4. 자동 실행

```cron
0 3 * * *  cd /opt/sp-core-client && SP_TOKEN=... python sp_core_pull.py --config config.ini
```

야간 1회를 권장합니다. **수동 실행 경로를 함께 마련하십시오** — 야간 수신이 실패했을 때
다음 날까지 대기하지 않아야 합니다.

## 5. 자체 데이터베이스에 저장하려면

`sp_core_pull.py` 의 `save_rows()` 하나만 수정합니다. 나머지(페이지 이어받기 · 증분 ·
삭제 처리 · 재시도)는 그대로 동작합니다.

```python
def save_rows(cfg, type_slug, rows):
    for row in rows:
        if row.get("deleted"):
            내DB.비활성(row["key"], 병합처리=row.get("merged_into"))
        else:
            내DB.upsert(key=row["key"], label=row["label"], **row["properties"])
```

## 6. 수신 측이 지켜야 할 규칙

1. **`as_of` 는 받은 값을 그대로 반환합니다.** 수신 측 시계로 생성하면 그 사이의 변경이
   누락됩니다.
2. **`next` 가 있으면 `as_of` 를 저장하지 않습니다.** 전체 페이지를 받은 뒤에만 저장합니다.
3. **`key` 를 저장합니다.** 다음 수신이 신규 생성이 아닌 수정이 되는 근거입니다.
4. **`deleted` 행은 비활성 처리합니다.** 삭제하면 해당 항목을 참조하던 자료가 끊어집니다.
   `merged_into` 가 있으면 참조를 그 `key` 로 이전합니다. `hidden: true` 도 함께 오면 삭제가
   아니라 **이 토큰으로 더 이상 볼 수 없게 된 것**(소유 부서 이동)입니다 — 이름 · 칸은 오지
   않으므로 보유한 값은 그대로 두고 비활성 처리만 합니다.
5. **정의되지 않은 칸은 무시합니다.** 공개 측이 칸을 추가해도 수신이 중단되지 않습니다.
6. **`reset: true` 가 오면 저장한 `as_of` 를 버리고, 그 타입의 현재 상태를 비우고 처음부터
   받습니다.** 오래 안 받아 가면 공개 측이 「그 시점부터는 **끊긴 것을 알려 줄 수 없다**」
   고 말합니다(끊긴 선의 기록은 한동안만 보관합니다). 그 빈 응답을 「변경 없음」 으로 읽으면
   이미 끊긴 선을 영구히 보유하게 됩니다. 비우지 않고 다시 받으면 그 사이에 사라진 것이
   그대로 남습니다 — `sp_core_pull.py` 는 이것을 자동으로 처리하고 화면에
   「처음부터 다시 받음」 이라고 적습니다.

## 7. 응답 형식

봉투(고정) + `properties`(공개 타입의 정의에 따름) 두 층입니다.

```json
{
  "as_of": "2026-09-24T02:00:00.000000Z",
  "next": null,
  "items": [
    { "key": "SS400", "label": "일반구조용강", "status": "active",
      "updated_at": "2026-09-23T08:11:00.000000Z", "deleted": false,
      "properties": { "density": 7.85, "supplier": "ACME-001" } },
    { "key": "OLD-1", "deleted": true, "merged_into": "SS400", "properties": {} }
  ]
}
```

선은 이렇게 옵니다(`GET /core/<타입>/relations`).

```json
{
  "as_of": "2026-09-24T02:00:00.000000Z",
  "next": null,
  "items": [
    { "src": "P-1", "relation": "supplies", "dst": "ACME-001", "dst_type": "vendor",
      "evidence_note": "계약서 3.2", "properties": { "n": 2 },
      "updated_at": "2026-09-23T08:11:00.000000Z", "deleted": false },
    { "src": "P-1", "relation": "supplies", "dst": "HAN-002", "dst_type": "vendor",
      "updated_at": "2026-09-24T01:00:00.000000Z", "deleted": true }
  ]
}
```

`deleted: true` 인 선은 **끊긴 선**입니다 — 자기 쪽에서도 끊으십시오. 양끝이 모두 공개된
관계 종류만 옵니다(한쪽이 공개되지 않았으면 찾을 수 없는 끝점을 받게 되므로).

너무 오래 안 받아 갔으면 대신 이것이 옵니다.

```json
{ "as_of": null, "items": [], "reset": true,
  "reset_reason": "30일보다 오래된 시점부터는 끊긴 선을 알려 줄 수 없습니다 — `since` 를 비우고 처음부터 받으세요." }
```

| 값 | 표기 |
| --- | --- |
| 참조 속성 | 대상 객체의 `key` (`"ACME-001"`) |
| 다중 값 | 배열 (`["A","B"]`) |
| 날짜 · 일시 | `2026-09-24` · `2026-09-24T01:23:45.000000Z` |
| 빈 값 | 해당 키가 존재하지 않음 |
| 첨부 파일 | 제공하지 않음 |

전체 명세는 `{BASE}/docs` 에서 확인할 수 있습니다.

## 8. 문제가 발생하면

| 증상 | 확인할 것 |
| --- | --- |
| HTTP 401 · 403 | 토큰 오류 또는 범위 부족(`core:read` 필요) |
| HTTP 404 | 해당 타입이 공개 목록에 없음 — 카탈로그(`{BASE}/core`)로 확인 |
| 수신 건수가 0 | 정상입니다. 직전 수신 이후 변경분이 없다는 의미입니다 |
| 처음부터 다시 받고 싶음 | `--full` 또는 `out/state.json` 삭제 |
| 「처음부터 다시 받음」 이 찍힘 | 정상입니다. 너무 오래 안 받아 가서 공개 측이 `reset` 을 보냈고, 그 타입을 비우고 전체를 다시 받았습니다. 자주 보이면 수신 주기를 줄이십시오 |
