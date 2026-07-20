# -*- coding: utf-8 -*-
"""로컬 급속 검색 — 집 인터넷으로 돌려 차단 없이 빠르게.
사용: 검색.bat 더블클릭, 또는  python local_search.py "키워드" [지역]
토큰은 keyring(Scripts\\creds)에서 자동으로 가져온다.
"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
os.environ["MAX_WORKERS"] = "4"          # 2026-07-20 재측정: 당근이 IP당 ~2.3req/s로 조임.
                                          # 4워커가 최적(2.3req/s). 8은 오히려 throttle 걸려 느림(1.8req/s, 응답4s+)
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

print(f"\n'{keyword}' 전국 검색 시작 (집 인터넷 1개 IP, 8,499지역 = 약 50~65분)...")
print("  ※ 당근이 IP당 속도를 조여서 한 IP로는 이 이상 못 줄입니다.")
print("  ※ 빨리 전국을 훑으려면 GitHub Actions 병렬(여러 IP) 쪽을 쓰세요.")
print("결과는 노션에 실시간 적재, 끝나면 텔레그램으로 요약이 옵니다.\n")
d.run_search(keyword, region)
print("\n끝났습니다. 노션 DB와 텔레그램을 확인하세요.")
