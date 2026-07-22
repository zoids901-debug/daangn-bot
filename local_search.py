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
os.environ["FETCH_DELAY_MIN"] = "0.1"    # 지역요청 대기 최소(초) — 대기는 짧게, 동시수로 throttle 회피
os.environ["FETCH_DELAY_MAX"] = "0.2"    # 지역요청 대기 최대(초)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"C:\Users\zoids\Scripts\creds")

from creds import get_cred
import daangn_search as d

d.TELEGRAM_TOKEN  = get_cred("daangn_telegram_token")
d.CHAT_ID         = get_cred("daangn_chat_id")
d.NOTION_TOKEN    = get_cred("daangn_notion_token")
d.NOTION_DATABASE = get_cred("daangn_notion_db")

keyword = sys.argv[1] if len(sys.argv) > 1 else input("검색할 키워드: ").strip()
region  = sys.argv[2] if len(sys.argv) > 2 else ""

if not keyword:
    print("키워드가 비어 있습니다.")
    sys.exit(1)

print(f"\n'{keyword}' 전국 검색 시작 (집 인터넷 1개 IP, 8,499지역 = 약 58분)...")
print("  ※ 워커 5개가 천장(2.44 req/s). 더 늘리면 오히려 느려집니다.")
print("  ※ 깃허브 병렬은 더 빠르지 않습니다 — 당근이 데이터센터 IP를 속도와 무관하게")
print("     30% 이상 거절합니다(12.5초에 한 번 쏴도 30%). 러너 20대 = 이 노트북 1.4대.")
print("결과는 노션에 실시간 적재, 끝나면 텔레그램으로 요약이 옵니다.\n")
d.run_search(keyword, region)
print("\n끝났습니다. 노션 DB와 텔레그램을 확인하세요.")
