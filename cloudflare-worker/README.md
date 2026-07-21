# daangn-bot Cloudflare Worker (대화형 텔레그램 봇)

이 Worker가 **실제 대화형 봇의 본체**다. (예전 `daangn_telegram.py`(run_polling+playwright)는 폐기된 옛 로컬 버전 — 안 쓴다.)

## 구조

```
텔레그램 →[웹훅]→ Cloudflare Worker(daangn-bot) →[GitHub Actions search.yml 디스패치]→ 검색 실행 → 텔레그램 알림
```

- Worker 이름: `daangn-bot`  /  주소: https://daangn-bot.zoids901.workers.dev
- 텔레그램 웹훅이 이 주소를 가리킨다(`setWebhook`). `getWebhookInfo`로 확인 가능.
- 봇이 하는 일: 들어온 메시지가 `검색 키워드 [지역]` 이면 GitHub Actions `search.yml`을 디스패치. 실제 검색은 Actions에서 돈다.

## 명령

- `검색 키워드` — 전국. **병렬 워크플로 `search-parallel.yml` 디스패치 (~10분)**
- `검색 키워드/지역` — 지정 지역. 단일 `search.yml` 디스패치(범위 작아 빠름)
- `/검색`, `/search`, `search` 도 모두 동작 (앞 슬래시는 선택)
- `도움` / `help` — 사용법

## 상시 감시 키워드 관리 (채팅으로)

봇이 `keywords.json`을 GitHub Contents API로 직접 편집한다(GH_PAT 사용).
- `상시목록` / `상시등록`(인자 없이) → 현재 감시 키워드 목록
- `상시등록 키워드` → 추가
- `상시삭제 키워드` (또는 `상시등록 키워드 삭제`) → 삭제
- 키워드 공백 허용(예: `상시등록 더블알엘 모자`). 다음 자동 감시부터 반영.
- ⚠️ **GH_PAT에 `contents:write` 권한 필요**(워크플로 디스패치는 `actions:write`). 상시등록이 403/404면 GH_PAT 권한 부족 → 토큰 교체.

### 키워드/지역 구분 = '/' (중요)
키워드에 공백이 들어갈 수 있어서(예: `더블알엘 모자`) **키워드와 지역은 공백이 아니라 `/` 로 나눈다.**
- `검색 더블알엘 모자` → 키워드="더블알엘 모자", 전국
- `검색 더블알엘 모자/서울` → 키워드="더블알엘 모자", 지역="서울"
- `/` 없으면 인자 전체가 키워드. (옛 daangn_telegram.py 규칙과 동일)

### 라우팅 (중요)
Worker가 지역 유무로 워크플로를 고른다: **지역 없음(전국) → `search-parallel.yml`**(20갈래 병렬, region 입력 없음), **지역 있음 → `search.yml`**(keyword+region). 병렬 워크플로에 region 을 보내면 422(미정의 입력)로 실패하니 라우팅 유지 필수.

## 시크릿 바인딩 (Cloudflare에 저장, 코드엔 없음)

`GH_PAT`, `GH_REPO`, `TELEGRAM_TOKEN`, `WEBHOOK_SECRET` — 모두 secret_text 바인딩.

## 배포 방법

이 노트북(메인)은 ARM이라 wrangler가 안 돈다. **Cloudflare REST API로 직접 배포**한다.
`keep_bindings: ["secret_text"]` 를 metadata에 넣어야 시크릿이 안 날아간다. compat_date=2026-05-26, 모듈 워커(`main_module: worker.js`).
배포 스크립트 예시는 세션 기록(cf_worker_deploy.py) 참고. CF 토큰은 keyring `zoids/cloudflare_api_token`.

## 히스토리

- 2026-07-21: `검색` 명령의 앞 슬래시를 선택(`\/` → `\/?`)으로 변경 — 슬래시 없이 `검색 키워드` 로 쓸 수 있게. 도움말도 동일 처리 + 안내문 갱신.
- 2026-07-21: 전국 검색을 `search.yml`(단일, 2~3시간) → **`search-parallel.yml`(병렬 ~10분)** 로 라우팅. 지역 지정 시엔 region 지원하는 `search.yml` 유지.
