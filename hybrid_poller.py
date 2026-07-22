# -*- coding: utf-8 -*-
"""이 노트북용 가벼운 감시자 — 하이브리드 검색의 '앞장(1-2/3)' 몫을 돌린다.

[왜] 당근이 IP당 처리량을 조인다. 집 IP 두 개(이 노트북 + 서버)로 지역을 나눠
동시에 돌리면 전국이 절반 시간에 끝난다. 서버는 상시 poller 가 있지만 이 노트북은
없으므로, 이 파일을 작업 스케줄러에 몇 분마다 걸어 '켜져 있을 때만' 참여시킨다.

[흐름] daangn-bot/hybrid/ 폴더의 요청 파일(*.json)을 본다 → 아직 이 노트북이 안 집은
요청이면 claim 파일을 만들고(중복 실행 방지) → 앞장 몫(--chunk=1-2/3)으로 검색 →
서버는 같은 요청을 보고 뒷몫(3-3/3)을 돈다. 노트북이 꺼져 있으면 claim 이 안 생기고,
서버가 grace(기본 4분) 뒤 노트북 몫까지 흡수해 전국을 전담한다(daangn_runner 가 처리).

[상태] hybrid/<id>.json = 요청, hybrid/<id>.laptop = 이 노트북이 집었다는 표시.
       둘 다 git 으로 서버와 공유된다(서버가 laptop 표시를 보고 자기 몫을 정한다).
"""
import glob
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
HYDIR = os.path.join(HERE, "hybrid")
LAPTOP_FRACTION = "1-2/3"          # 이 노트북 몫(앞 2/3). 서버 몫은 '3-3/3'.
STALE_SEC = 3600                   # 1시간 지난 요청은 무시(밀린 것 재실행 방지)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def git(*a):
    return subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def load_creds():
    sys.path.insert(0, os.path.join(os.path.expanduser("~"), "Scripts", "creds"))
    from creds import get_cred
    import daangn_search as d
    d.TELEGRAM_TOKEN = get_cred("daangn_telegram_token")
    d.CHAT_ID = get_cred("daangn_chat_id")
    d.NOTION_TOKEN = get_cred("daangn_notion_token")
    d.NOTION_DATABASE = get_cred("daangn_notion_db")
    return d


def run_share(d, req):
    """앞장 몫 검색. local_search 를 별도로 안 쓰고 직접 부른다(같은 프로세스)."""
    os.environ["MAX_WORKERS"] = "5"
    os.environ["FETCH_DELAY_MIN"] = "0.05"
    os.environ["FETCH_DELAY_MAX"] = "0.15"
    d.run_search(req["keyword"], req.get("region", ""), chunk=LAPTOP_FRACTION)


def main():
    if not os.path.isdir(os.path.join(HERE, ".git")):
        log("daangn-bot git 클론이 아님 — 종료"); return
    git("reset", "--hard", "HEAD")           # 작업트리 오염만 정리
    git("pull", "--ff-only")
    os.makedirs(HYDIR, exist_ok=True)

    reqs = sorted(glob.glob(os.path.join(HYDIR, "*.json")))
    if not reqs:
        return
    d = None
    for rp in reqs:
        rid = os.path.splitext(os.path.basename(rp))[0]
        claim = os.path.join(HYDIR, rid + ".laptop")
        if os.path.exists(claim):
            continue                          # 이미 이 노트북이 집은 요청
        try:
            req = json.load(open(rp, encoding="utf-8"))
        except Exception:
            continue
        if time.time() - req.get("ts", 0) > STALE_SEC:
            continue                          # 너무 오래된 요청

        # claim 을 먼저 만들어 서버에 알린다(서버는 이걸 보고 뒷몫만 돈다).
        open(claim, "w", encoding="utf-8").write(time.strftime("%Y-%m-%d %H:%M:%S"))
        git("add", claim)
        git("commit", "-m", f"hybrid: 노트북이 {rid} 앞장 집음")
        for _ in range(3):
            if git("push").returncode == 0:
                break
            git("pull", "--rebase")
        log(f"요청 {rid} — 앞장({LAPTOP_FRACTION}) 시작: {req.get('keyword')}")

        if d is None:
            d = load_creds()
        try:
            run_share(d, req)
            log(f"요청 {rid} — 앞장 완료")
        except Exception as e:
            log(f"요청 {rid} — 앞장 실패: {e}")


if __name__ == "__main__":
    main()
