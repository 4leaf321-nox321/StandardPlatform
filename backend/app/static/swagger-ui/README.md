# API 문서 화면의 파일(Swagger UI)

`/api/docs` 가 쓰는 화면 파일을 **서버가 직접 준다.** FastAPI 의 기본값은 스크립트 · 스타일을
`cdn.jsdelivr.net` 에서, 아이콘을 `fastapi.tiangolo.com` 에서 받게 해서 폐쇄망에서는 흰 화면이
됐다 — 다른 시스템 개발자에게 「`/api/docs` 를 열라」 고 안내하는 입구가 막혀 있던 것이다.
붙이는 코드는 `app/api_docs.py`.

| | |
| --- | --- |
| 꾸러미 | npm `swagger-ui-dist` **5.33.3**(2026-10-15) — 받은 그대로, 고치지 않았다 |
| 지문 | `sha512-7xJKxAjf+/ajlW/V/dMfbiY+NC5BWtbvzzdI/AsX5UzMtOMudmFz/tjuKN0jN7QVe5drKKnx01CdYEJvTFvQlw==`(npm 등록소의 `dist.integrity` 와 같음) |
| 라이선스 | Apache-2.0 — `LICENSE` · `NOTICE`. 스크립트 안에 묶인 것들(MIT 등)은 `swagger-ui-bundle.js.LICENSE.txt` |
| 고른 까닭 | 나온 지 2주가 넘은 판 가운데, 새 갈래(5.34.0)의 첫 판 대신 앞 갈래의 마지막 고침판 |

꾸러미에서 가져온 것은 화면이 실제로 부르는 셋(`swagger-ui-bundle.js` · `swagger-ui.css` ·
`favicon-32x32.png`)과 라이선스 파일뿐이다. 소스맵(`.map`)은 안 가져왔다 — 스크립트 끝에 소스맵 줄이 없어
브라우저가 찾지 않는다.

## 판을 바꿀 때

```bash
mkdir -p /tmp/swagger/pkg /tmp/swagger/x && cd /tmp/swagger/pkg
npm pack swagger-ui-dist@<판>                      # 받는다
npm view swagger-ui-dist@<판> dist.integrity       # 이 값과
echo "sha512-$(openssl dgst -sha512 -binary swagger-ui-dist-<판>.tgz | base64 -w0)"   # 이 값이 같아야 한다
tar xzf swagger-ui-dist-<판>.tgz -C /tmp/swagger/x
cp /tmp/swagger/x/package/{swagger-ui-bundle.js,swagger-ui.css,favicon-32x32.png,LICENSE,NOTICE,swagger-ui-bundle.js.LICENSE.txt} .
```

그 뒤 이 표의 판 · 지문과 `THIRD_PARTY_NOTICES.md` 의 판을 고치고, `tests/api/test_api_docs.py` 를 돌린다
(화면이 바깥 주소를 하나도 안 부르는지 · 부르는 파일이 모두 있는지를 본다).
