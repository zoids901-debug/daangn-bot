# -*- coding: utf-8 -*-
"""감시봇을 집 IP에서 돌린다 (서버노트북 스케줄러용, 2026-07-22 깃허브에서 이전).

깃허브 러너 IP는 당근이 속도와 무관하게 30% 이상 거절한다(실측). 재시도로 버텨는
왔지만, 집 IP는 같은 요청이 100% 통과라 여기서 도는 게 빠르고 빠짐이 없다.

흐름: keyring 토큰 주입 → 전국 감시(키워드 적으면 서버검색 모드) → 새 매물 텔레그램
     → seen.json 커밋·푸시(깃허브판과 같은 장부를 이어 씀 — 재알림 방지의 핵심).
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
os.environ["MAX_WORKERS"] = "5"          # 집 IP 천장(워커 1~10 전수 측정, 2026-07-22)
os.environ["FETCH_DELAY_MIN"] = "0.05"
os.environ["FETCH_DELAY_MAX"] = "0.15"
sys.path.insert(0, os.path.join(os.path.expanduser("~"), "Scripts", "creds"))

from creds import get_cred            # noqa: E402
import daangn_search as d             # noqa: E402

d.TELEGRAM_TOKEN = get_cred("daangn_telegram_token")
d.CHAT_ID = get_cred("daangn_chat_id")
d.NOTION_TOKEN = get_cred("daangn_notion_token")
d.NOTION_DATABASE = get_cred("daangn_notion_db")

print("[감시] 전국 감시 시작 (집 IP, 약 23분)")
d.run_watch()                          # 단일 실행 모드: 전달 + seen.json 갱신까지


def push_seen():
    """seen.json 을 원격에도 반영한다. 이 장부가 밀리면 같은 매물이 또 알림된다."""
    def git(*a):
        return subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, timeout=120)

    st = git("status", "--porcelain", "seen.json")
    if not st.stdout.strip():
        print("[감시] seen.json 변화 없음")
        return
    git("add", "seen.json")
    git("commit", "-m", "auto: watch seen.json (server)")
    for _ in range(3):
        if git("push").returncode == 0:
            print("[감시] seen.json push 완료")
            return
        git("pull", "--rebase")
    print("[감시] ⚠️ seen.json push 실패 — 다음 회차 pull 때 재시도됨")


push_seen()
