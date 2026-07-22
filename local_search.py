# -*- coding: utf-8 -*-
"""로컬 급속 검색 — 집 인터넷으로 돌려 차단 없이 빠르게.
사용: 검색.bat 더블클릭, 또는  python local_search.py "키워드" [지역]
토큰은 keyring(Scripts\\creds)에서 자동으로 가져온다.
"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
os.environ["MAX_WORKERS"] = "5"          # 2026-07-22 워커 1~10 전수 측정(각 40회, 실패 0건):
                                          #   1:0.76  2:1.79  3:1.97  4:2.40  5:2.44 ← 최고
                                          #   6:2.26  7:2.16  8:1.57  9:1.60 10:1.44
                                          # 4~5가 평평한 천장이고 6부터 꺾인다. 더 늘리면 손해.
os.environ["FETCH_DELAY_MIN"] = "0.05"   # 연결 재사용 + JSON 으로 바뀐 뒤엔 대기가 병목이 아니다.
os.environ["FETCH_DELAY_MAX"] = "0.15"   # 워커 5개가 천장이라 대기는 최소로 둔다.
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"C:\Users\zoids\Scripts\creds")

from creds import get_cred
import daangn_search as d

d.TELEGRAM_TOKEN  = get_cred("daangn_telegram_token")
d.CHAT_ID         = get_cred("daangn_chat_id")
d.NOTION_TOKEN    = get_cred("daangn_notion_token")
d.NOTION_DATABASE = get_cred("daangn_notion_db")

# 위치 인자(옛 검색.bat 호환) + --chunk(하이브리드 분담용)
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
_chunk = ""
for a in sys.argv[1:]:
    if a.startswith("--chunk="):
        _chunk = a.split("=", 1)[1]
keyword = _args[0] if _args else input("검색할 키워드: ").strip()
region  = _args[1] if len(_args) > 1 else ""

if not keyword:
    print("키워드가 비어 있습니다.")
    sys.exit(1)

scope = "전국" if not region else region
part = f" [내 몫 {_chunk}]" if _chunk else ""
print(f"\n'{keyword}' {scope} 검색 시작{part} (집 IP, 연결재사용+JSON)...")
print("결과는 노션에 실시간 적재, 끝나면 텔레그램으로 요약이 옵니다.\n")
d.run_search(keyword, region, chunk=_chunk or None)
print("\n끝났습니다. 노션 DB와 텔레그램을 확인하세요.")
