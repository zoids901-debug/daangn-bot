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


def main():
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
