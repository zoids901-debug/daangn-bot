# -*- coding: utf-8 -*-
"""검색이 '집 IP에서는 나오는데 깃허브에서는 0건'인지 가려내는 진단.

같은 키워드/같은 지역코드로 당근에 물어보고, 응답이 실제로 몇 건이며
상태(진행중/거래완료)가 어떻게 오는지 그대로 찍는다. 텔레그램은 건드리지 않는다.

로컬:   py _diag_search.py 불가리안백 395
깃허브: workflow_dispatch (diag-search.yml) 로 같은 인자를 넘겨 로그 비교
"""
import json
import re
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def fix(t):
    try:
        return t.encode("latin-1").decode("utf-8")
    except Exception:
        return t


def probe(keyword, tid=None):
    params = {"search": keyword}
    if tid:
        params["in"] = tid
    r = requests.get("https://www.daangn.com/kr/buy-sell/", params=params,
                     headers={"User-Agent": UA}, timeout=20)
    label = f"in={tid or '(없음)'}"
    m = re.search(r"window\.__remixContext\s*=\s*({.*?});", r.text, re.DOTALL)
    if not m:
        print(f"  {label:14s} status={r.status_code} len={len(r.text)} → remixContext 없음(차단/구조변경)")
        return
    main = json.loads(m.group(1))["state"]["loaderData"]["routes/kr.buy-sell._index"]
    arts = main.get("allPage", {}).get("fleamarketArticles", []) or []
    cur = main.get("currentFilters", {})
    st = {}
    for a in arts:
        st[a.get("status")] = st.get(a.get("status"), 0) + 1
    print(f"  {label:14s} status={r.status_code} len={len(r.text)} 총 {len(arts)}건 상태={st} "
          f"필터={ {k: fix(v) if isinstance(v, str) else v for k, v in cur.items()} }")
    for a in arts[:5]:
        print(f"        [{a.get('status')}] {fix(a.get('title',''))}")


def load_test(keyword, n, workers):
    """부하 재현: 실제 검색과 같은 속도로 N개 지역을 훑어, '결과 0건'으로 오는 비율을 센다.

    당근이 막을 때 403을 주면 봇이 '차단'으로 셀 수 있지만, 200 + 빈 목록으로 주면
    봇은 그걸 '이 동네엔 매물이 없다'로 착각한다 → 전국 0건. 그 조용한 빈손을 찾는다.
    대조군으로 어디에나 매물이 있는 키워드를 쓰면, 0건 비율이 곧 누락률이다.
    """
    import random
    from concurrent.futures import ThreadPoolExecutor

    from collections import Counter

    tids = [1 + (i * 37) % 8499 for i in range(n)]
    tally = Counter()
    samples = []

    def one(tid):
        time.sleep(random.uniform(0.4, 1.0))
        try:
            r = requests.get("https://www.daangn.com/kr/buy-sell/",
                             params={"in": tid, "search": keyword},
                             headers={"User-Agent": UA}, timeout=12)
        except Exception as e:
            return "예외:" + type(e).__name__, None
        if r.status_code != 200:
            return f"HTTP{r.status_code}", None
        m = re.search(r"window\.__remixContext\s*=\s*({.*?});", r.text, re.DOTALL)
        if not m:
            # 200인데 내용이 다르다 = 봇 판정/챌린지 페이지. 봇의 parse_page 는 이걸
            # 조용히 (None, []) 로 넘겨 '매물 없음'으로 둔갑시킨다 → 차단 집계도 안 된다.
            return "200인데_목록없음", (len(r.text), r.text[:200].replace("\n", " "))
        main = json.loads(m.group(1))["state"]["loaderData"]["routes/kr.buy-sell._index"]
        return ("0건" if not main.get("allPage", {}).get("fleamarketArticles") else "정상"), None

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for kind, s in ex.map(one, tids):
            tally[kind] += 1
            if s and len(samples) < 3:
                samples.append(s)
    el = time.time() - t0
    print(f"[부하시험] '{keyword}' {n}개 지역 / 워커 {workers} / {el:.0f}초 ({n/el:.1f} req/s)")
    for k, v in tally.most_common():
        print(f"    {k:22s} {v:4d}건 ({v*100//max(n,1)}%)")
    for ln, head in samples:
        print(f"    [본문표본 {ln}자] {head}")


def ramp(keyword="아이폰", per_phase=60, cooldown=45):
    """429가 안 나오는 최대 속도를 찾는다(느린 쪽에서 빠른 쪽으로 올려가며).

    당근의 제한은 '총 횟수'가 아니라 '속도'다. 단계마다 목표 req/s 를 정해 그 속도로
    쏘고 429 비율을 잰다. 한 번 걸리면 한동안 이어지므로 단계 사이에 쉬어준다.
    마지막에 '429 0%였던 가장 빠른 속도'를 안전선으로 제시한다.
    """
    from concurrent.futures import ThreadPoolExecutor

    # (목표 req/s, 동시 워커) — 느린 쪽부터. 워커를 늘리는 게 아니라 간격으로 속도를 만든다.
    PHASES = [(0.5, 1), (1.0, 1), (1.5, 2), (2.0, 2), (3.0, 3), (4.0, 4)]
    pos = 0
    safe = None

    def one(tid, gap):
        time.sleep(gap)
        try:
            r = requests.get("https://www.daangn.com/kr/buy-sell/",
                             params={"in": tid, "search": keyword},
                             headers={"User-Agent": UA}, timeout=15)
        except Exception:
            return "예외"
        if r.status_code == 429:
            return "429"
        if r.status_code != 200:
            return f"HTTP{r.status_code}"
        return "정상" if "__remixContext" in r.text else "목록없음"

    print(f"=== 안전 속도 측정 (단계당 {per_phase}회, 단계 사이 {cooldown}초 휴식) ===")
    for target, workers in PHASES:
        tids = [1 + ((pos + i) * 37) % 8499 for i in range(per_phase)]
        pos += per_phase
        gap = workers / target          # 워커 1개가 요청마다 쉬는 시간
        tally = {}
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for v in ex.map(lambda t: one(t, gap), tids):
                tally[v] = tally.get(v, 0) + 1
        el = time.time() - t0
        actual = per_phase / el
        n429 = tally.get("429", 0)
        pct = n429 * 100 // per_phase
        mark = "OK" if n429 == 0 else "제한"
        print(f"  목표 {target:>4.1f} req/s (워커 {workers}) → 실측 {actual:4.1f} req/s | "
              f"429 {n429:2d}/{per_phase} ({pct:2d}%) [{mark}] {tally}")
        if n429 == 0:
            safe = actual
        else:
            break                       # 한 번 걸리면 그 위는 볼 것도 없다
        time.sleep(cooldown)             # 제한 창이 리셋되도록 쉬어준다

    if safe:
        print(f"\n>>> 안전선: 약 {safe:.1f} req/s 까지 429 없음")
        print(f">>> 20갈래 병렬이면 갈래당 이 속도 → 8499지역 / 20 = 425지역 ≈ "
              f"{425/safe/60:.1f}분")
    else:
        print("\n>>> 가장 느린 단계에서도 429 — 지금은 깃허브에서 검색을 자제할 것")


def fingerprint(keyword="아이폰", tid="1477"):
    """속도가 아니라 '누가 요청하느냐'가 문제인지 가른다.

    - 러너의 공인 IP를 찍어 둔다(다른 실행과 비교해 IP별인지 대역 전체인지 판별).
    - 헤더만 바꿔 같은 요청을 반복한다. 지금 봇은 User-Agent 한 줄만 보내는데,
      진짜 브라우저는 Accept/Accept-Language/sec-ch-ua 등을 함께 보낸다.
      헤더만으로 통과율이 달라지면 원인은 IP가 아니라 '봇처럼 보이는 요청'이다.
    """
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text
    except Exception:
        ip = "(확인 실패)"
    print(f"=== 요청자 판별 (러너 공인 IP: {ip}) ===")

    bare = {"User-Agent": UA}
    browser = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.daangn.com/kr/buy-sell/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": '"Chromium";v="126", "Not:A-Brand";v="24", "Google Chrome";v="126"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    for label, hdr, sess in (("현재(UA만)", bare, False),
                             ("브라우저 헤더", browser, False),
                             ("브라우저 헤더+세션유지", browser, True)):
        s = requests.Session() if sess else requests
        codes = {}
        for i in range(12):
            time.sleep(1.5)
            try:
                r = s.get("https://www.daangn.com/kr/buy-sell/",
                          params={"in": tid, "search": keyword}, headers=hdr, timeout=15)
                key = str(r.status_code)
                if r.status_code == 200 and "__remixContext" not in r.text:
                    key = "200(목록없음)"
            except Exception as e:
                key = "예외:" + type(e).__name__
            codes[key] = codes.get(key, 0) + 1
        ok = codes.get("200", 0)
        print(f"  {label:20s} 12회 → 성공 {ok:2d} · {codes}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--fingerprint":
        fingerprint()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--ramp":
        ramp(sys.argv[2] if len(sys.argv) > 2 else "아이폰",
             int(sys.argv[3]) if len(sys.argv) > 3 else 60)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--load":
        load_test(sys.argv[2] if len(sys.argv) > 2 else "아이폰",
                  int(sys.argv[3]) if len(sys.argv) > 3 else 200,
                  int(sys.argv[4]) if len(sys.argv) > 4 else 4)
        return
    keyword = sys.argv[1] if len(sys.argv) > 1 else "불가리안백"
    tids = sys.argv[2].split(",") if len(sys.argv) > 2 else ["395"]
    print(f"=== 진단: '{keyword}' ===")
    probe(keyword)                      # 지역 미지정(당근 기본 지역)
    for t in tids:
        time.sleep(1.0)
        probe(keyword, t.strip())
    # 대조군: 결과가 많은 키워드로 '검색 자체가 먹히는지' 확인
    print("=== 대조군: '아이폰' ===")
    time.sleep(1.0)
    probe("아이폰", tids[0].strip())


if __name__ == "__main__":
    main()
