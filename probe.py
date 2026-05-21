# -*- coding: utf-8 -*-
"""당근 차단 임계치 측정기.

당근 지역 페이지를 일정 속도로 요청하면서, 어느 지점에서 차단이 시작되는지 기록한다.
깃허브 액션에서 돌리면 '깃허브 IP 1개가 몇 번까지 버티는지'를 알 수 있다.
이 수치로 매트릭스 분할(갈래 수)과 요청 간격을 정한다.

환경변수:
  PROBE_MAX   : 최대 요청 수 (기본 600)
  PROBE_DELAY : 요청 간격 초 (기본 0.3)
"""
import sys
import os
import time
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MAX_REQUESTS = int(os.environ.get("PROBE_MAX", "600"))
DELAY        = float(os.environ.get("PROBE_DELAY", "0.3"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def check(tid):
    """지역 페이지 1개 요청. (정상여부, 상태, 응답시간) 반환."""
    t0 = time.time()
    try:
        r = requests.get(
            "https://www.daangn.com/kr/buy-sell/",
            params={"in": tid},
            headers={"User-Agent": UA},
            timeout=15,
        )
        lat = time.time() - t0
        # 차단되면 200이라도 데이터(지역명)가 빠진 페이지가 오므로 둘 다 확인
        ok = (r.status_code == 200) and ("depth1RegionName" in r.text)
        return ok, str(r.status_code), lat
    except Exception as e:
        return False, f"ERR:{type(e).__name__}", time.time() - t0


def main():
    print("=== 당근 차단 임계치 측정 ===")
    print(f"최대 {MAX_REQUESTS}회 / 요청간격 {DELAY}초\n")

    # 전국에 골고루 퍼진 지역코드 (같은 페이지 반복 방지)
    tids = [1 + (i * 17) % 8499 for i in range(MAX_REQUESTS)]

    ok_count = fail_count = consec_fail = 0
    first_fail_at = None
    start = time.time()

    for i, tid in enumerate(tids, 1):
        ok, status, lat = check(tid)
        if ok:
            ok_count += 1
            consec_fail = 0
        else:
            fail_count += 1
            consec_fail += 1
            if first_fail_at is None:
                first_fail_at = i

        if i % 50 == 0 or not ok:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed else 0
            mark = "OK" if ok else f"FAIL({status})"
            print(f"[{i:4}회] {mark:14} 누적 OK {ok_count} / FAIL {fail_count} / {rate:.1f}req/s")

        if consec_fail >= 8:
            print(f"\n>>> 연속 8회 실패 — 차단 확정. {i}회째에서 막힘.")
            break

        time.sleep(DELAY)

    elapsed = time.time() - start
    total = ok_count + fail_count
    print("\n=== 측정 결과 ===")
    print(f"총 시도   : {total}회")
    print(f"성공      : {ok_count}회")
    print(f"실패      : {fail_count}회")
    if first_fail_at:
        print(f"첫 실패   : {first_fail_at}회째")
    else:
        print(f"첫 실패   : 없음 — {total}회까지 차단 안 됨")
    print(f"평균 속도 : {total / elapsed:.1f} req/s ({elapsed:.0f}초 소요)")
    print(f"\n[판정] 깃허브 IP 1개당 안전 요청수 ≈ "
          + (f"{first_fail_at - 1}회 미만" if first_fail_at else f"{total}회 이상 (한도 미도달)"))


if __name__ == "__main__":
    main()
