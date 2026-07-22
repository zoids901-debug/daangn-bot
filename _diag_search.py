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


def main():
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
