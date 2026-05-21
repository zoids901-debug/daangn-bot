# -*- coding: utf-8 -*-
"""당근 차단 임계치 측정 v2 — 요청 속도를 단계적으로 올리며 어디서 막히는지 찾는다.

1차 측정에서 '천천히(초당 0.5회)는 600회까지 멀쩡, 6갈래 동시는 금방 차단'이 나왔다.
즉 차단 기준은 '총 횟수'가 아니라 '속도(req/s)'. 이 스크립트로 안전한 최대 속도를 찾는다.
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
PER_PHASE = 150
# (동시 워커수, 워커당 추가 대기초) — 단계적으로 빠르게
PHASES = [(2, 0.10), (3, 0.05), (4, 0.0), (6, 0.0), (10, 0.0)]


def check(tid):
    try:
        r = requests.get(
            "https://www.daangn.com/kr/buy-sell/",
            params={"in": tid},
            headers={"User-Agent": UA},
            timeout=15,
        )
        return (r.status_code == 200) and ("depth1RegionName" in r.text)
    except Exception:
        return False


def main():
    print("=== 당근 차단 임계치 측정 v2 (속도 램프) ===\n")
    tid_pool = [1 + (i * 17) % 8499 for i in range(5000)]
    pos = 0
    safe_rate = 0.0

    for workers, delay in PHASES:
        tids = tid_pool[pos:pos + PER_PHASE]
        pos += PER_PHASE

        def task(tid):
            if delay:
                time.sleep(delay)
            return check(tid)

        t0 = time.time()
        ok = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(task, tids):
                if r:
                    ok += 1
        elapsed = time.time() - t0
        rate = PER_PHASE / elapsed
        pct = ok / PER_PHASE * 100
        verdict = "정상" if pct >= 90 else ("불안정" if pct >= 50 else "차단")
        print(f"[{workers}워커/간격{delay}s] {PER_PHASE}회 중 {ok}성공 "
              f"({pct:.0f}%) / {rate:.2f} req/s → {verdict}")

        if pct >= 90:
            safe_rate = rate
            time.sleep(15)   # 다음 단계 전 잠깐 쉼
        else:
            print(f"\n>>> {rate:.2f} req/s 에서 막힘. 직전 안전속도 ≈ {safe_rate:.2f} req/s")
            break
    else:
        print(f"\n>>> 모든 단계 통과 — 최소 {safe_rate:.2f} req/s 까지 안전")

    print(f"\n[판정] 깃허브 IP 1개 안전 속도 ≈ {safe_rate:.2f} req/s")
    print("       (매트릭스로 IP를 N개 쓰면 전체 속도 = 이 값 × N)")


if __name__ == "__main__":
    main()
