# -*- coding: utf-8 -*-
"""서버 쪽 하이브리드 진행자 — 노트북이 앞장을 집는지 보고 자기 몫을 정한다.

daangn_runner 가 이걸 detached 로 띄운다(전국은 오래 걸려 poller 를 못 붙잡으므로).

흐름:
  1) hybrid/<id>.json 요청 파일을 만들고 push → 노트북 hybrid_poller 가 본다.
  2) grace(기본 4분) 동안 hybrid/<id>.laptop(노트북이 집었다는 표시)이 뜨는지 폴링.
  3) 떴으면  → 서버는 뒷몫만(3-3/3). 노트북이 앞 2/3 를 돈다 → 전국 ~14분.
     안 떴으면 → 노트북이 꺼진 것. 서버가 전국 전담(chunk 없음) → ~37분.

이렇게 하면 노트북이 켜져 있든 없든 결과는 항상 완전하고, 켜져 있으면 빨라진다.
"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
HYDIR = os.path.join(HERE, "hybrid")
SERVER_FRACTION = "3-3/3"          # 노트북이 참여할 때 서버 몫(뒤 1/3)
GRACE_SEC = int(os.environ.get("HYBRID_GRACE", "240"))   # 노트북 claim 대기(4분)


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


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else ""
    region = sys.argv[2] if len(sys.argv) > 2 else ""
    if not keyword:
        log("키워드 없음"); return

    os.makedirs(HYDIR, exist_ok=True)
    # 오래된 요청·claim 정리(6시간 지난 것). 안 하면 hybrid/ 가 무한정 쌓인다.
    now = time.time()
    stale = []
    for f in os.listdir(HYDIR):
        fp = os.path.join(HYDIR, f)
        if f != ".gitkeep" and os.path.isfile(fp) and now - os.path.getmtime(fp) > 21600:
            try:
                os.remove(fp); stale.append(f)
            except OSError:
                pass
    if stale:
        git("add", "-A", HYDIR)
        git("commit", "-m", f"hybrid: 오래된 요청 {len(stale)}개 정리")

    rid = time.strftime("%Y%m%d-%H%M%S")
    req = {"keyword": keyword, "region": region, "ts": time.time()}
    rp = os.path.join(HYDIR, rid + ".json")
    json.dump(req, open(rp, "w", encoding="utf-8"), ensure_ascii=False)
    git("add", rp)
    git("commit", "-m", f"hybrid: 검색 요청 {rid} {keyword}")
    for _ in range(3):
        if git("push").returncode == 0:
            break
        git("pull", "--rebase")
    log(f"요청 {rid} 게시: {keyword} / {region or '전국'}")

    # 지역 지정 검색은 짧으니 하이브리드 필요 없음 — 서버가 바로 전량.
    if region:
        d = load_creds()
        os.environ.update(MAX_WORKERS="5", FETCH_DELAY_MIN="0.05", FETCH_DELAY_MAX="0.15")
        d.run_search(keyword, region)
        return

    claim = os.path.join(HYDIR, rid + ".laptop")
    waited = 0
    while waited < GRACE_SEC:
        time.sleep(30)
        waited += 30
        git("pull", "--ff-only")
        if os.path.exists(claim):
            log(f"노트북이 앞장 집음({waited}초) → 서버는 뒷몫 {SERVER_FRACTION}")
            break

    d = load_creds()
    os.environ.update(MAX_WORKERS="5", FETCH_DELAY_MIN="0.05", FETCH_DELAY_MAX="0.15")
    if os.path.exists(claim):
        d.run_search(keyword, "", chunk=SERVER_FRACTION)     # 뒷몫만
    else:
        log("노트북 미참여(꺼짐) → 서버 전국 전담")
        d.run_search(keyword, "")                            # 전국 전량


if __name__ == "__main__":
    main()
