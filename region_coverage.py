# -*- coding: utf-8 -*-
"""지역 목록에서 '겹치는 지역'을 걷어내 요청 수 자체를 줄인다.

[왜] 당근 검색은 반경 기반이라 옆 동네끼리 결과가 겹친다(종로 tid1/tid2 는 결과가 완전히
같고, 서초는 하나도 안 겹친다). 지금은 8,499개를 전부 요청하는데, 겹치는 만큼이 그대로
낭비다. 이건 속도 제한을 우회하는 게 아니라 애초에 덜 요청하는 방식이라 어느 경로에서든
그대로 효과가 난다(깃허브 병렬이든 서버든).

[방법] 매물이 전국에 고루 있는 키워드로 지역마다 한 번씩 훑어, 각 지역이 '어떤 매물을
보여주는지' 기록한다. 그다음 그 매물들을 전부 덮는 최소한의 지역을 고른다(욕심쟁이 방식).
어느 지역의 매물이 다른 지역에 이미 다 들어 있으면 그 지역은 빼도 결과가 같다.

[주의] 반경은 키워드와 무관한 지리적 성질이지만, 매물이 드문 지역은 표본이 약하다.
그래서 --verify 로 다른 키워드를 한 번 더 돌려 교차 확인할 수 있게 해뒀다.

실행:
  py region_coverage.py --scan --keyword 아이폰          # 전국 훑어 기록(집 IP 약 60분)
  py region_coverage.py --plan                            # 기록으로 최소 지역 목록 만들기
  py region_coverage.py --verify --keyword 의자           # 다른 키워드로 손실 검증
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MAX_WORKERS", "4")
os.environ.setdefault("FETCH_DELAY_MIN", "0.1")
os.environ.setdefault("FETCH_DELAY_MAX", "0.2")

import daangn_search as D   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_FILE = os.path.join(HERE, "region_scan.json")
SLIM_FILE = os.path.join(HERE, "region_map_slim.json")


def load_regions():
    return json.load(open(os.path.join(HERE, "region_map.json"), encoding="utf-8"))


def scan(keyword):
    """지역마다 '보이는 매물 목록'을 기록한다. 실패한 지역은 남겨서 다시 훑는다."""
    regions = load_regions()
    tids = sorted(int(t) for t in regions.keys())
    done = {}
    if os.path.exists(SCAN_FILE):          # 중간에 끊겨도 이어서
        saved = json.load(open(SCAN_FILE, encoding="utf-8"))
        if saved.get("keyword") == keyword:
            done = saved.get("regions", {})
            print(f"[이어하기] 이미 {len(done)}개 기록됨")
    todo = [t for t in tids if str(t) not in done]
    print(f"[훑기] '{keyword}' — 남은 지역 {len(todo)}/{len(tids)}")

    t0 = time.time()
    lock_n = [0]

    def one(tid):
        addr, items = D.fetch_region(tid, keyword)
        lock_n[0] += 1
        if lock_n[0] % 250 == 0:
            el = time.time() - t0
            eta = (len(todo) - lock_n[0]) / max(lock_n[0], 1) * el / 60
            print(f"  {lock_n[0]}/{len(todo)} ... 남은시간 약 {eta:.0f}분", flush=True)
        if addr is None:
            return tid, None
        return tid, sorted({it["link"] for it in items})

    with ThreadPoolExecutor(max_workers=int(os.environ["MAX_WORKERS"])) as ex:
        for tid, links in ex.map(one, todo):
            if links is not None:
                done[str(tid)] = links
            if len(done) % 500 == 0:
                json.dump({"keyword": keyword, "regions": done},
                          open(SCAN_FILE, "w", encoding="utf-8"), ensure_ascii=False)

    json.dump({"keyword": keyword, "regions": done},
              open(SCAN_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    covered = sorted({l for v in done.values() for l in v})
    print(f"[완료] 지역 {len(done)}개 기록 · 매물 {len(covered)}종 (실패 {len(tids)-len(done)}개)")


def plan():
    """기록으로 '전부 덮는 최소 지역'을 고른다(욕심쟁이: 매번 가장 많이 새로 덮는 지역 선택)."""
    if not os.path.exists(SCAN_FILE):
        print("먼저 --scan 을 돌리세요."); return
    data = json.load(open(SCAN_FILE, encoding="utf-8"))
    regions = {k: set(v) for k, v in data["regions"].items() if v}
    all_items = set().union(*regions.values()) if regions else set()
    print(f"[계획] 기록된 지역 {len(regions)}개 · 매물 {len(all_items)}종")

    picked, covered = [], set()
    pool = dict(regions)
    while covered != all_items and pool:
        tid, gain = max(((t, len(s - covered)) for t, s in pool.items()), key=lambda x: x[1])
        if gain == 0:
            break
        picked.append(tid)
        covered |= pool.pop(tid)

    # 매물이 하나도 없던 지역은 이 키워드로는 판단 불가 → 안전하게 남긴다.
    empty = [k for k, v in data["regions"].items() if not v]
    keep = sorted(set(picked) | set(empty), key=int)
    names = load_regions()
    json.dump({k: names.get(k, "") for k in keep},
              open(SLIM_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    total = len(names)
    print(f"[결과] {total}개 → {len(keep)}개 ({len(keep)*100//total}%)")
    print(f"   매물 있는 지역에서 고른 대표 {len(picked)}개 + 매물 없어 판단 보류 {len(empty)}개")
    print(f"   덮은 매물 {len(covered)}/{len(all_items)}종")
    print(f"   → {SLIM_FILE}")
    if total and len(keep) < total:
        print(f"   전국 소요 예상: 기존 대비 {len(keep)*100//total}% "
              f"(집 IP 62분 기준 약 {62*len(keep)//total}분)")


def verify(keyword):
    """줄인 목록으로 다른 키워드를 검색해 결과가 빠지는지 본다(손실 검증)."""
    if not os.path.exists(SLIM_FILE):
        print("먼저 --plan 을 돌리세요."); return
    slim = json.load(open(SLIM_FILE, encoding="utf-8"))
    full = load_regions()
    print(f"[검증] '{keyword}' — 전체 {len(full)}개 vs 줄인 {len(slim)}개")

    def collect(tids):
        found = set()
        with ThreadPoolExecutor(max_workers=int(os.environ["MAX_WORKERS"])) as ex:
            for _, items in ex.map(lambda t: D.fetch_region(int(t), keyword), tids):
                found |= {it["link"] for it in items}
        return found

    a = collect(list(slim.keys()))
    print(f"  줄인 목록 결과 {len(a)}종")
    b = collect(list(full.keys()))
    print(f"  전체 목록 결과 {len(b)}종")
    miss = b - a
    print(f"  줄여서 놓친 매물 {len(miss)}종 ({len(miss)*100//max(len(b),1)}%)")
    for m in list(miss)[:10]:
        print("   -", m)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--keyword", default="아이폰")
    args = ap.parse_args()
    if args.scan:
        scan(args.keyword)
    elif args.plan:
        plan()
    elif args.verify:
        verify(args.keyword)
    else:
        ap.print_help()
