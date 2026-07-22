#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
당근마켓 검색·알림 봇 (깃허브 액션 매트릭스판)

한 IP로 전국을 몰아 훑으면 당근이 차단하므로, 전국을 여러 '갈래(chunk)'로
쪼개 깃허브에서 동시 실행한다. 갈래마다 IP가 달라 차단되지 않는다.

모드:
  watch   : 갈래 하나를 맡아 '새 매물 후보'를 찾아 결과파일(--out)에 기록
  search  : 갈래 하나를 맡아 키워드 매물을 찾아 결과파일(--out)에 기록
  aggregate : 갈래들의 결과파일을 모아 텔레그램·노션으로 전달 (--target watch|search)
  map     : 전국 지역코드(region_map.json) 수집 (단일 실행, 안전 속도)

--chunk i/N : 전체 지역 중 i번째 묶음만 처리 (1-기반)
--out FILE  : 결과를 FILE(JSON)에 기록하고 전달은 하지 않음 (갈래 워커용)

비밀정보(텔레그램·노션 토큰)는 환경변수로 받는다. 갈래 워커는 비밀정보가
필요 없고, aggregate 단계에서만 사용한다.
"""
import os
import sys
import csv
import time
import json
import random
import argparse
import datetime
import threading
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# 윈도우 콘솔(cp949)에서 이모지/특수문자 출력 시 크래시 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ==================== 설정 ====================
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID         = os.environ.get("TELEGRAM_CHAT_ID", "")
NOTION_TOKEN    = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE = os.environ.get("NOTION_DATABASE_ID", "")

REGION_MAP_FILE = "region_map.json"
KEYWORDS_FILE   = "keywords.json"
SEEN_FILE       = "seen.json"

SEEN_TTL_DAYS   = 30
# 동시 요청 수. 깃허브 IP 안전선은 측정상 2 (초당 ~1회). 환경변수로 덮어쓸 수 있다.
MAX_WORKERS     = int(os.environ.get("MAX_WORKERS", "4"))
SEND_DELAY      = 0.4   # 텔레그램 메시지 간격(초)
# 지역 요청마다 넣는 랜덤 대기(초). 기본은 클라우드(깃허브) 안전값이 크고,
# 집 IP 로컬 급속검색은 FETCH_DELAY_MIN/MAX 환경변수로 확 줄여 빠르게 돌린다.
FETCH_MIN       = float(os.environ.get("FETCH_DELAY_MIN", "0.4"))
FETCH_MAX       = float(os.environ.get("FETCH_DELAY_MAX", "1.0"))

REGION_ALIASES = {
    "전라도": "전라", "경상도": "경상", "충청도": "충청", "강원도": "강원",
    "경기":   "경기도", "서울": "서울특별시", "부산": "부산광역시",
    "인천":   "인천광역시", "대구": "대구광역시", "광주": "광주광역시",
    "대전":   "대전광역시", "울산": "울산광역시", "제주": "제주특별자치도",
    "세종":   "세종특별자치시", "전남": "전라남도", "전북": "전라북도",
    "경남":   "경상남도", "경북": "경상북도", "충남": "충청남도", "충북": "충청북도",
}


# ==================== 텔레그램 ====================
def send_telegram(msg):
    """텔레그램으로 메시지 1건 전송. 토큰이 없으면 화면 출력만 한다."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        try:
            print("[텔레그램 미설정] " + msg.replace("\n", " | "))
        except UnicodeEncodeError:
            print("[텔레그램 미설정] (콘솔 인코딩 문제로 메시지 출력 생략)")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"[텔레그램 오류] {e}")


def send_telegram_id(msg):
    """메시지를 보내고 message_id를 돌려준다(진행율 제자리 갱신용). 실패시 None."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return None
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "disable_web_page_preview": True},
            timeout=10,
        )
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"[텔레그램 오류] {e}")
        return None


def edit_telegram(msg_id, msg):
    """진행율 메시지를 제자리 갱신(editMessageText). msg_id 없거나 실패하면 조용히 넘어감."""
    if not (TELEGRAM_TOKEN and CHAT_ID and msg_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
            json={"chat_id": CHAT_ID, "message_id": msg_id, "text": msg,
                  "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception:
        pass


def send_item(item):
    """매물 1건을 텔레그램으로 전송 (+ 노션 적재)."""
    kw = f" ({item['keyword']})" if item.get("keyword") else ""
    send_telegram(
        f"✨ [{item.get('addr','')}]{kw}\n"
        f"📦 {item.get('title','')}\n"
        f"💰 {item.get('price','')}\n"
        f"🔗 {item.get('link','')}"
    )
    push_notion(item)
    time.sleep(SEND_DELAY)


# ==================== 노션 ====================
def push_notion(item):
    """노션 DB에 매물 1건 기록. 토큰/DB가 없으면 조용히 건너뛴다.
    일시적 SSL/네트워크 오류로 유실되지 않게 최대 3회 재시도한다."""
    if not NOTION_TOKEN or not NOTION_DATABASE:
        return
    price_num = int(re.sub(r"[^\d]", "", item.get("price", "")) or 0)
    payload = {
        "parent": {"database_id": NOTION_DATABASE},
        "properties": {
            "상품명": {"title":     [{"text": {"content": item.get("title", "")[:100]}}]},
            "검색어": {"rich_text": [{"text": {"content": item.get("keyword", "")}}]},
            "지역명": {"rich_text": [{"text": {"content": item.get("addr", "")}}]},
            "가격":   {"number": price_num},
            "링크":   {"url": item.get("link", "")},
        },
    }
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    for attempt in range(3):
        try:
            r = requests.post("https://api.notion.com/v1/pages",
                              headers=headers, json=payload, timeout=10)
            if r.status_code == 200:
                return
            if 400 <= r.status_code < 500:      # 잘못된 요청은 재시도해도 소용없음
                print(f"[노션 실패 {r.status_code}] {r.text[:150]}")
                return
        except Exception as e:
            last = e
        time.sleep(1.5 * (attempt + 1))         # 1.5s → 3s 백오프 후 재시도
    print(f"[노션 오류] 3회 재시도 실패: {item.get('title','')[:40]}")


# ==================== 파일 입출력 ====================
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_tids_by_region(region_map, region_keyword):
    """'서울' 같은 지역 이름으로 해당 지역코드들을 찾는다."""
    kw = REGION_ALIASES.get(region_keyword, region_keyword)
    return sorted(int(t) for t, name in region_map.items() if kw in name)


def apply_chunk(tids, chunk):
    """전체 tids 중 내 몫만 골라낸다. 두 가지 형식을 받는다.

      'i/N'      : N등분한 것 중 i번째 (1-기반, 인터리브 — 갈래 병렬용)
      'a-b/N'    : N등분한 것 중 a..b번째 묶음 (연속 구간 — 하이브리드 분담용)
                   예) 노트북='1-2/3'(앞 2/3), 서버='3-3/3'(뒤 1/3)
    구간 방식도 인터리브로 떼어내 전국에 고르게 퍼지게 한다(한 지역만 몰리지 않게).
    """
    if not chunk:
        return tids
    rng, n = chunk.split("/")
    n = int(n)
    if "-" in rng:
        a, b = (int(x) for x in rng.split("-"))
        keep = set(range(a - 1, b))          # 0-기반 슬롯 a-1 .. b-1
        return [t for k, t in enumerate(tids) if k % n in keep]
    i = int(rng)
    return tids[i - 1::n]


# ==================== 키워드 매칭 ====================
def keyword_words(kw):
    """키워드를 단어 단위로 쪼개 정규화. '더블알엘 모자' -> ['더블알엘','모자']"""
    return [w for w in (p.replace(" ", "").lower() for p in kw.split()) if w]


def title_matches(title, words):
    """제목에 키워드의 모든 단어가 들어있으면 True (순서 무관, 띄어쓰기 무시)."""
    title_norm = title.replace(" ", "").lower()
    return bool(words) and all(w in title_norm for w in words)


# ==================== 페이지 파싱 ====================
def _fix(text):
    """당근 페이지의 깨진 한글 인코딩 복구."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except Exception:
        return text


def parse_region_name(html):
    """페이지에서 지역명만 가볍게 추출 (map 모드 전용)."""
    d1 = re.search(r'"depth1RegionName"\s*:\s*"([^"]*)"', html)
    if not d1:
        return None
    d2 = re.search(r'"depth2RegionName"\s*:\s*"([^"]*)"', html)
    addr = f"{_fix(d1.group(1))} {_fix(d2.group(1) if d2 else '')}".strip()
    return addr or None


def parse_page(html):
    """페이지 HTML -> (지역명, [진행중 매물 dict ...])"""
    m = re.search(r"window\.__remixContext\s*=\s*({.*?});", html, re.DOTALL)
    if not m:
        return None, []
    try:
        data = json.loads(m.group(1).encode("utf-8", "replace").decode("utf-8"))
        main = data.get("state", {}).get("loaderData", {}).get("routes/kr.buy-sell._index", {})
        return parse_loader(main, broken_encoding=True)
    except Exception:
        return None, []


def parse_loader(main, broken_encoding=False):
    """당근이 내려주는 화면 데이터 덩어리 -> (지역명, [진행중 매물 ...]).

    HTML 안에 박힌 것과 JSON 주소로 받은 것이 같은 구조라 파싱을 한 곳에서 한다.
    HTML 경로는 인코딩이 깨져 들어오므로 그때만 복구한다(JSON 은 안 깨진다).
    """
    fix = _fix if broken_encoding else (lambda t: t)
    try:
        reg  = main.get("region", {})
        addr = f"{fix(reg.get('depth1RegionName',''))} {fix(reg.get('depth2RegionName',''))}".strip()

        exclude = ["판매완료", "거래완료", "완료", "솔드아웃", "예약중"]
        items = []
        for it in main.get("allPage", {}).get("fleamarketArticles", []):
            if it.get("status", "") != "Ongoing":
                continue
            title = fix(it.get("title", ""))
            if any(ex in title.replace(" ", "") for ex in exclude):
                continue
            try:
                price = format(int(float(it.get("price", 0))), ",") + "원"
            except Exception:
                price = "0원"
            # href 가 곧 올바른 전체 주소 (/kr/buy-sell/...)
            href = it.get("href", "")
            if not href:
                continue
            items.append({
                "title": title,
                "price": price,
                "addr":  addr or "지역미상",
                "link":  href,
            })
        return addr, items
    except Exception:
        return None, []


BLOCK_HITS = 0     # 차단(403/429) 누적(전역)
TIMEOUT_HITS = 0   # 타임아웃·네트워크 오류 누적(전역) — 병렬 갈래의 '조용한 누락' 감지용

# 당근이 속도를 조일 때 쓰는 응답 코드. 403뿐 아니라 429(요청 과다)도 차단이다.
# (2026-07-22 사고: 429를 못 알아보고 그 응답을 그냥 파싱 → 매물 0건으로 둔갑 →
#  '불가리안백 전국 0건 · 차단 0'. 깃허브에서 실측하니 요청의 70%가 429였다.)
BLOCK_CODES = (403, 429)

# 429가 나오면 전체가 느려지도록 스스로 감속한다(갈래 안 공용).
_throttle = 1.0
_throttle_lock = threading.Lock()
# 감속 상한. 기본 간격이 이미 실측 안전선(갈래당 0.2 req/s)이라, 여기서 더 늦출 이유가 거의 없다.
# 2026-07-22: 상한 3배로 뒀더니 429가 1%뿐인데도 간격이 15초까지 늘어 전국이 35분→3시간이 됐다.
# 못 읽은 지역은 뒤의 보정 패스가 책임지므로, 본 훑기는 1.5배까지만 늦춘다.
THROTTLE_MAX = 1.5

# 끝내 못 읽은 지역. 훑기가 끝난 뒤 느린 속도로 다시 훑어 메운다(보정 패스).
# 이게 없으면 차단이 많은 날에는 그 지역 매물이 통째로 빠진 채 결과가 나온다.
FAILED_TIDS = []
_failed_lock = threading.Lock()


def _mark_failed(tid):
    with _failed_lock:
        FAILED_TIDS.append(tid)


def drain_failed():
    with _failed_lock:
        out = list(FAILED_TIDS)
        FAILED_TIDS.clear()
    return out


def _slow_down():
    global _throttle
    with _throttle_lock:
        _throttle = min(_throttle * 1.2, THROTTLE_MAX)


def _speed_up():
    global _throttle
    with _throttle_lock:
        if _throttle > 1.0:
            _throttle = max(1.0, _throttle * 0.9)   # 성공이 이어지면 곧 원래 속도로 복귀


# 스레드마다 연결을 하나씩 두고 계속 재쓴다. 지역마다 새로 연결하면 TCP/TLS 악수를
# 8,499번 반복하게 되는데, 실측으로 그게 전체 시간의 절반이었다.
#   2026-07-22 실측(집 IP, 워커 5, 각 40회):
#     HTML+새연결 2.26 req/s → HTML+재사용 4.29 → JSON+재사용 5.55 (2.45배, 전국 58분→26분)
#     HTTP/2 는 당근이 403 으로 막는다(브라우저가 안 쓰는 방식이라 봇 판정) — 쓰지 말 것.
_local = threading.local()

# 화면 데이터만 주는 주소. 같은 내용을 1/15 크기로 주고, 한글도 안 깨진다.
DATA_ROUTE = "routes/kr.buy-sell._index"


def _session():
    s = getattr(_local, "sess", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        _local.sess = s
    return s


def fetch_region(tid, keyword=None):
    """지역 페이지 1개를 가져와 매물 목록을 반환. keyword 를 주면 당근 서버에서 미리 검색.

    실패를 조용히 '매물 없음'으로 넘기지 않는 것이 이 함수의 핵심이다.
    차단(403/429)·타임아웃·목록 없는 응답을 각각 재시도하고, 끝내 실패하면 집계한다.
    → 병렬 갈래에서 지역이 소리없이 빠지지 않고, aggregate가 결과 불완전 여부를 알 수 있다."""
    global BLOCK_HITS, TIMEOUT_HITS
    params = {"in": tid, "_data": DATA_ROUTE}
    if keyword:
        params["search"] = keyword
    for attempt in range(4):
        time.sleep(random.uniform(FETCH_MIN, FETCH_MAX) * _throttle)
        try:
            res = _session().get(
                "https://www.daangn.com/kr/buy-sell/",
                params=params,
                timeout=12,
            )
            if res.status_code in BLOCK_CODES:
                _slow_down()
                if attempt < 3:
                    # 당근이 Retry-After 로 대기 시간을 알려주면 그대로 따른다.
                    try:
                        wait = float(res.headers.get("Retry-After", "") or 0)
                    except ValueError:
                        wait = 0
                    time.sleep(min(wait or 2.0 * (attempt + 1), 20))
                    continue
                BLOCK_HITS += 1
                _mark_failed(tid)
                return None, []
            try:
                addr, items = parse_loader(res.json())
            except ValueError:
                # JSON 주소가 막히거나 사라지면 예전처럼 페이지 통째로 받아 파싱한다.
                addr, items = parse_page(res.text)
            if addr is None:
                # 200인데 매물 목록 자체가 없는 응답 = 사실상 차단(또는 페이지 구조 변경).
                # 예전엔 이걸 '이 동네엔 매물 없음'으로 삼켜서 전국 0건이 나왔다.
                _slow_down()
                if attempt < 3:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                BLOCK_HITS += 1
                _mark_failed(tid)
                return None, []
            _speed_up()
            return addr, items
        except Exception:
            if attempt < 3:
                time.sleep(1.0 * (attempt + 1))   # 타임아웃은 점증 백오프로 재시도
                continue
            TIMEOUT_HITS += 1
            _mark_failed(tid)
            return None, []
    return None, []


# ==================== 요약문 ====================
def build_summary(title_line, items):
    """매물 목록으로 텔레그램 요약문을 만든다."""
    prices = []
    for it in items:
        digits = "".join(ch for ch in it.get("price", "") if ch.isdigit())
        if digits and int(digits) > 0:
            prices.append(int(digits))
    prov = {}
    for it in items:
        if it.get("addr"):
            p = it["addr"].split()[0]
            prov[p] = prov.get(p, 0) + 1
    top = sorted(prov.items(), key=lambda x: -x[1])[:6]

    msg = f"{title_line}\n━━━━━━━━━━━━\n✅ 총 {len(items)}건\n"
    if prices:
        msg += f"💰 {min(prices):,} ~ {max(prices):,}원 (평균 {sum(prices)//len(prices):,}원)\n"
    if top:
        msg += "📍 " + " / ".join(f"{k} {v}" for k, v in top) + "\n"
    msg += "\n📋 전체 목록 → 노션 DB"
    return msg


# ==================== 모드: watch (알림봇 갈래 워커) ====================
def run_watch(chunk=None, out=None):
    region_map = load_json(REGION_MAP_FILE, {})
    if not region_map:
        if out:
            save_json(out, []); print("[watch] region_map 비어있음"); return
        send_telegram("❌ 알림봇: region_map.json 이 비어 있습니다."); sys.exit(1)

    keywords = load_json(KEYWORDS_FILE, [])
    if not keywords:
        if out:
            save_json(out, []); print("[watch] 키워드 없음"); return
        send_telegram("⚠️ 알림봇: keywords.json 에 키워드가 없습니다."); sys.exit(0)

    seen = load_json(SEEN_FILE, {})
    tids = apply_chunk(sorted(int(t) for t in region_map.keys()), chunk)
    norm_keywords = [(k, keyword_words(k)) for k in keywords]

    # 키워드가 적으면(≤3) 피드 훑기 대신 당근 서버 검색을 쓴다.
    # 피드는 지역당 최신 ~260건이 끝이다(다음 페이지 없음 — 2026-07-22 확인). 바쁜 동네에선
    # 회차(8시간) 사이에 그보다 많이 올라오면 뒤로 밀린 매물을 영영 놓친다.
    # 서버 검색은 게시 시점과 무관하게 키워드 일치를 다 돌려주므로 깊이 한계가 없다.
    # 요청 수 = 지역 × 키워드라, 키워드가 많으면 예전 방식(피드 1회로 전 키워드 대조)이 낫다.
    search_mode = len(keywords) <= 3
    jobs = ([(tid, kw) for tid in tids for kw in keywords] if search_mode
            else [(tid, None) for tid in tids])
    print(f"[watch] chunk={chunk or '전체'} / 지역 {len(tids)}개 / 키워드 {keywords} "
          f"/ 방식={'서버검색' if search_mode else '피드훑기'} / 요청 {len(jobs)}개")

    global BLOCK_HITS
    new_items, done = [], 0

    def absorb(articles):
        # 이미 본 매물도 버리지 않는다 — deliver_watch 가 '아직 팔리는 중' 표시(날짜 갱신)에 쓴다.
        # 여기서 걸러버리면 30일 넘게 팔리는 매물이 seen 에서 청소된 뒤 '새 매물'로 재알림된다.
        for art in articles:
            if not art["link"]:
                continue
            for kw, words in norm_keywords:
                if title_matches(art["title"], words):
                    art["keyword"] = kw
                    new_items.append(art)
                    break

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_region, tid, kw) for tid, kw in jobs]
        for fut in as_completed(futures):
            done += 1
            if done % 100 == 0:
                blk = f" · ⚠️차단 {BLOCK_HITS}건" if BLOCK_HITS else ""
                print(f"  진행 {done}/{len(jobs)} ... 후보 {len(new_items)}건{blk}", flush=True)
            try:
                _, articles = fut.result()
            except Exception:
                continue          # 한 지역이 터져도 나머지 감시는 계속돼야 한다
            absorb(articles)

    # 검색과 똑같이 보정 패스를 돈다. 감시는 '새로 올라온 매물'을 잡는 게 목적이라
    # 그날 못 읽은 지역은 영영 못 잡는다(다음 회차엔 이미 seen 이 아니라 그냥 놓친 게 된다).
    for round_no in (1, 2):
        retry = drain_failed()
        if not retry:
            break
        print(f"  [보정{round_no}] 수집 실패 {len(retry)}개 지역 재수집(느린 속도)", flush=True)
        recovered = 0
        for tid in set(retry):
            for kw in (keywords if search_mode else [None]):
                addr, articles = fetch_region(tid, kw)
                if addr is None:
                    continue
                recovered += 1
                absorb(articles)
        BLOCK_HITS = max(0, BLOCK_HITS - recovered)
        print(f"  [보정{round_no}] {recovered}건 복구 (대상 {len(set(retry))}개 지역)", flush=True)

    if out:
        save_json(out, {"items": new_items,
                        "stats": {"regions": len(tids), "blocks": BLOCK_HITS, "timeouts": TIMEOUT_HITS}})
        print(f"[watch] chunk {chunk} — 신규후보 {len(new_items)}건 기록 "
              f"(차단 {BLOCK_HITS}·타임아웃 {TIMEOUT_HITS}) ({out})")
        return

    # 단일 실행 모드 (로컬·수동용)
    deliver_watch(new_items, seen)


def deliver_watch(new_items, seen):
    """새 매물 후보를 받아 텔레그램·노션 전달 + seen.json 갱신·커밋용 저장."""
    # 링크 기준 중복 제거
    uniq = {}
    for it in new_items:
        if it.get("link"):
            uniq[it["link"]] = it
    new_items = list(uniq.values())

    first_run = len(seen) == 0
    today = datetime.date.today().isoformat()
    fresh = [it for it in new_items if it["link"] not in seen]
    # 새것뿐 아니라 '아직 보이는' 매물 전부 날짜를 갱신한다. fresh 만 찍으면 30일 넘게
    # 팔리는 매물이 prune 에 청소된 뒤 다음 회차에 '새 매물'로 재알림된다(오래된 버그).
    # 이렇게 하면 prune 의 의미가 '30일째 목록에서 안 보임 = 내려간 매물'로 바로잡힌다.
    for it in new_items:
        seen[it["link"]] = today
    prune_seen(seen)
    save_json(SEEN_FILE, seen)

    if first_run:
        send_telegram(f"🥕 알림봇 가동 시작!\n현재 매물 {len(fresh)}건을 기록했습니다.\n다음 실행부터 '새 매물'만 알려드립니다.")
        print(f"[watch] 첫 실행 — {len(fresh)}건 기록만 (알림 생략)")
        return

    for it in fresh:
        send_item(it)
    if fresh:
        kws = ", ".join(sorted(set(it.get("keyword", "") for it in fresh)))
        send_telegram(f"🥕 알림봇: 새 매물 {len(fresh)}건 발견 ({kws})")
    print(f"[watch] 완료 — 새 매물 {len(fresh)}건")


def prune_seen(seen):
    """오래된 '본 매물' 기록을 정리해 파일이 무한정 커지지 않게 한다."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=SEEN_TTL_DAYS)).isoformat()
    for link in [k for k, v in seen.items() if v < cutoff]:
        del seen[link]


# ==================== 모드: search (즉석검색 갈래 워커) ====================
def run_search(keyword, region, chunk=None, out=None):
    global BLOCK_HITS          # 보정 패스에서 되살아난 지역을 실패 집계에서 뺀다
    keyword = (keyword or "").strip()
    if not keyword:
        if out:
            save_json(out, []); print("[search] 키워드 없음"); return
        send_telegram("❌ 즉석검색: 키워드가 비어 있습니다."); sys.exit(1)

    region_map = load_json(REGION_MAP_FILE, {})
    if region and region.strip():
        tids, bad = [], []
        for r in [x.strip() for x in region.replace(" ", "").split(",") if x.strip()]:
            found = get_tids_by_region(region_map, r)
            (tids.extend(found) if found else bad.append(r))
        tids = sorted(set(tids))
        scope = region.strip()
    else:
        tids = sorted(int(t) for t in region_map.keys())
        scope = "전국"

    if not tids:
        if out:
            save_json(out, []); print("[search] 대상 지역 없음"); return
        send_telegram(f"❌ 즉석검색: '{region}' 지역을 찾을 수 없습니다."); sys.exit(1)

    tids = apply_chunk(tids, chunk)
    kw_words = keyword_words(keyword)
    print(f"[search] '{keyword}' / {scope} / chunk={chunk or '전체'} / 지역 {len(tids)}개")

    results, seen_fp, done = [], set(), 0
    total = len(tids)
    # 세 가지 실행 형태:
    #   single   : 혼자 전국 돈다(chunk 없음, out 없음) — 시작·진행율·합산요약 다 보냄
    #   part     : 하이브리드 조각(chunk 있음, out 없음) — 노션 실시간 적재는 하되 텔레그램은
    #              자기 몫 완료 한 줄만(시작·진행율은 앞장 한 대만). 2대가 안 겹치게.
    #   갈래(out) : 결과를 파일로만 남기고 aggregate가 나중에 합쳐 보낸다.
    part = bool(chunk) and not out
    single = not out and not chunk
    realtime = single or part                 # 발견 즉시 노션 적재하는가
    is_leader = part and str(chunk).startswith("1-")   # 시작 메시지 담당(앞장 1대)
    csv_f = csv_w = None
    prog_id = None
    t0 = time.time()
    if single or is_leader:
        send_telegram(f"🔍 즉석검색 시작\n키워드: {keyword} ({scope})"
                      + ("\n※ 이 노트북+서버 동시 검색 중" if is_leader else ""))
    if single:
        prog_id = send_telegram_id(f"📡 검색 진행율 0% (0/{total})\n✅ 발견 0건 · 남은시간 계산 중...")
    if realtime:
        # 발견 즉시 노션+CSV에 쏘는 실시간 모드: 중간에 끊겨도 이미 찾은 건 보존된다.
        tag = f"_{str(chunk).replace('/', '_')}" if part else ""
        csv_f = open(f"검색결과_{keyword.replace(' ', '_')}{tag}.csv", "w", encoding="utf-8-sig", newline="")
        csv_w = csv.writer(csv_f)
        csv_w.writerow(["지역", "상품명", "가격", "링크"])
        csv_f.flush()

    def absorb(articles):
        """찾은 매물을 걸러 결과에 담는다(본 훑기와 보정 패스가 같은 처리를 쓰도록)."""
        for art in articles:
            if not title_matches(art["title"], kw_words):
                continue
            fp = f"{art['title']}_{art['price']}_{art['addr']}"
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            art["keyword"] = keyword
            results.append(art)
            if realtime:
                push_notion(art)                       # 발견 즉시 노션 적재
                csv_w.writerow([art.get("addr", ""), art.get("title", ""),
                                art.get("price", ""), art.get("link", "")])
                csv_f.flush()                          # 즉시 디스크 반영(중단 대비)
                print(f"★ 발견[{len(results)}] {art.get('addr','')} | "
                      f"{art.get('title','')} | {art.get('price','')} | {art.get('link','')}", flush=True)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_region, tid, keyword) for tid in tids]
        for fut in as_completed(futures):
            done += 1
            if done % 100 == 0:
                blk = f" · ⚠️차단 {BLOCK_HITS}건" if BLOCK_HITS else ""
                print(f"  진행 {done}/{total} ... 발견 {len(results)}건{blk}", flush=True)
                # 차단(403)이 30% 넘으면 검색이 사실상 무효 → 텔레그램으로 경고
                if single and BLOCK_HITS >= 100 and BLOCK_HITS > done * 0.3:
                    edit_telegram(prog_id, f"⛔ 당근 IP 차단 감지 ({BLOCK_HITS}/{done}) — 결과 불완전. "
                                           f"10~30분 뒤 재시도 권장")
            # 진행율 텔레그램 제자리 갱신(500개마다 = 전국 기준 약 17번). 도배 방지.
            if single and done % 500 == 0:
                pct = done * 100 // total
                el = time.time() - t0
                eta = int((total - done) / done * el / 60) if done else 0
                edit_telegram(prog_id, f"📡 검색 진행율 {pct}% ({done}/{total})\n"
                                       f"✅ 발견 {len(results)}건 · 남은시간 약 {eta}분")
            try:
                _, articles = fut.result()
            except Exception:
                continue
            absorb(articles)

    # ── 보정 패스: 끝내 못 읽은 지역을 느린 속도로 혼자 다시 훑는다 ──
    # 차단이 몰린 날엔 그 지역 매물이 통째로 빠진 채 "0건"이 나온다.
    # (2026-07-22: 평택 불가리안백 12KG처럼 사람은 보는데 봇은 못 보던 매물이 여기서 걸린다)
    for round_no in (1, 2):
        retry = drain_failed()
        if not retry:
            break
        print(f"  [보정{round_no}] 수집 실패 {len(retry)}개 지역 재수집(느린 속도)", flush=True)
        recovered = 0
        for tid in retry:
            addr, articles = fetch_region(tid, keyword)
            if addr is None:
                continue
            recovered += 1
            absorb(articles)
        # 되살아난 지역은 실패 집계에서 뺀다(경고가 과장되지 않게)
        BLOCK_HITS = max(0, BLOCK_HITS - recovered)
        print(f"  [보정{round_no}] {recovered}/{len(retry)}개 지역 복구", flush=True)

    if out:
        save_json(out, {"items": results,
                        "stats": {"regions": total, "blocks": BLOCK_HITS, "timeouts": TIMEOUT_HITS}})
        print(f"[search] chunk {chunk} — {len(results)}건 기록 "
              f"(차단 {BLOCK_HITS}·타임아웃 {TIMEOUT_HITS}) ({out})")
        return

    csv_f.close()
    lost = BLOCK_HITS + TIMEOUT_HITS
    warn = ""
    if lost:
        warn = (f"\n⚠️ 지역 {lost}개 수집 실패(차단 {BLOCK_HITS}·타임아웃 {TIMEOUT_HITS}) "
                f"— 결과가 불완전할 수 있어요.")
    if part:
        # 하이브리드 조각: 자기 몫만 한 줄 요약(합산은 노션 DB에서 자동으로 합쳐짐).
        where = "이 노트북" if is_leader else "서버"
        send_telegram(f"🏁 [{where} 몫] {keyword} — {len(results)}건 완료{warn}")
        print(f"[search] {where} 몫 완료 — {len(results)}건")
        return
    # 단일 실행 마무리: 진행율 100% + 합산 요약
    edit_telegram(prog_id, f"📡 검색 진행율 100% ({total}/{total})\n✅ 발견 {len(results)}건 · 완료")
    send_telegram(build_summary(f"🏁 즉석검색 완료 — {keyword} ({scope})", results) + warn)
    print(f"[search] 완료 — {len(results)}건 (차단 {BLOCK_HITS}·타임아웃 {TIMEOUT_HITS})")


def _write_csv(name, items):
    with open(name, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["지역", "상품명", "가격", "링크"])
        for it in items:
            w.writerow([it.get("addr", ""), it.get("title", ""), it.get("price", ""), it.get("link", "")])


# ==================== 모드: aggregate (갈래 결과 집계·전달) ====================
def run_aggregate(target, keyword, indir):
    paths = []
    for root, _, files in os.walk(indir):
        for fn in files:
            if fn.endswith(".json"):
                paths.append(os.path.join(root, fn))

    # 갈래 결과가 하나도 없다 = 실행이 취소되거나 전부 실패한 것.
    # 이걸 그냥 집계하면 "검색 완료 0건"이라는 새빨간 거짓말이 나간다.
    if not paths and target != "map":
        msg = "⚠️ 검색이 중단돼 결과를 못 받았습니다(갈래 결과 파일 없음). 다시 시도해 주세요."
        print("[aggregate] 갈래 결과 파일 없음 — 집계 생략")
        send_telegram(msg)
        return

    # map: 갈래 결과는 {지역코드: 지역명} 딕셔너리
    if target == "map":
        region_map = load_json(REGION_MAP_FILE, {})
        for p in paths:
            part = load_json(p, {})
            if isinstance(part, dict):
                region_map.update(part)
        save_json(REGION_MAP_FILE, region_map)
        send_telegram(f"지역코드 수집 완료 — 총 {len(region_map)}개 지역")
        print(f"[aggregate] region_map 총 {len(region_map)}개 지역")
        return

    # watch / search: 갈래 결과 = {items:[...], stats:{...}} (구버전 순수 리스트도 허용)
    items = []
    blocks = timeouts = regions = 0
    for p in paths:
        part = load_json(p, [])
        if isinstance(part, dict) and "items" in part:
            items.extend(part.get("items", []))
            st = part.get("stats", {}) or {}
            blocks += int(st.get("blocks", 0) or 0)
            timeouts += int(st.get("timeouts", 0) or 0)
            regions += int(st.get("regions", 0) or 0)
        elif isinstance(part, list):          # 구버전 갈래 파일 하위호환
            items.extend(part)
    # 링크 기준 중복 제거
    uniq = {}
    for it in items:
        if it.get("link"):
            uniq[it["link"]] = it
    items = list(uniq.values())

    # 병렬 갈래에서 소리없이 빠진 지역이 있으면 경고(단일 실행이 갖던 '차단 감지'를 병렬에서 복구)
    lost = blocks + timeouts
    warn = ""
    if lost:
        pct = f", 약 {lost * 100 // regions}%" if regions else ""
        warn = (f"\n⚠️ 지역 {lost}개 수집 실패(차단 {blocks} · 타임아웃 {timeouts}{pct}) "
                f"— 결과가 불완전할 수 있어요. 잠시 후 재검색을 권장합니다.")
    print(f"[aggregate] target={target} / 합계 {len(items)}건 / "
          f"차단 {blocks} 타임아웃 {timeouts} (대상지역 {regions})")

    if target == "watch":
        seen = load_json(SEEN_FILE, {})
        deliver_watch(items, seen)
        if warn:
            send_telegram("🥕 알림봇" + warn)
    elif target == "search":
        for it in items:
            push_notion(it)
        _write_csv(f"검색결과_{(keyword or 'search').replace(' ', '_')}.csv", items)
        send_telegram(build_summary(f"🏁 즉석검색 완료 — {keyword}", items) + warn)
        print(f"[aggregate] 검색 {len(items)}건 — 노션·CSV·요약 완료")


# ==================== 모드: map (지역코드 수집) ====================
def run_map(chunk=None, out=None):
    print(f"[map] 전국 지역코드 수집 (1~8500 홀짝 전체 / chunk={chunk or '전체'})")
    ids = apply_chunk(list(range(1, 8501)), chunk)
    new_map = {}
    lock = threading.Lock()
    done = [0]

    def one(tid):
        time.sleep(random.uniform(0.3, 0.7))
        try:
            res = requests.get(
                "https://www.daangn.com/kr/buy-sell/",
                params={"in": tid},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=12,
            )
            addr = parse_region_name(res.text)
            if addr:
                with lock:
                    new_map[str(tid)] = addr
        except Exception:
            pass
        with lock:
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  진행 {done[0]}/{len(ids)} ... 수집 {len(new_map)}개")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(one, ids))

    if out:
        save_json(out, new_map)
        print(f"[map] chunk {chunk} — {len(new_map)}개 지역 기록 ({out})")
        return

    region_map = load_json(REGION_MAP_FILE, {})
    region_map.update(new_map)
    save_json(REGION_MAP_FILE, region_map)
    send_telegram(f"지역코드 수집 완료 — 총 {len(region_map)}개 지역")
    print(f"[map] 완료 — region_map.json 총 {len(region_map)}개")


# ==================== 실행 진입점 ====================
def main():
    parser = argparse.ArgumentParser(description="당근마켓 검색·알림 봇")
    parser.add_argument("--mode", required=True,
                        choices=["watch", "search", "map", "aggregate"])
    parser.add_argument("--keyword", default="", help="search 키워드")
    parser.add_argument("--region",  default="", help="search 지역 (비우면 전국)")
    parser.add_argument("--chunk",   default="", help="갈래 i/N (예: 3/20)")
    parser.add_argument("--out",     default="", help="갈래 결과 기록 파일")
    parser.add_argument("--target",  default="", choices=["", "watch", "search", "map"],
                        help="aggregate 대상")
    parser.add_argument("--indir",   default="results", help="aggregate 입력 폴더")
    args = parser.parse_args()

    if args.mode == "watch":
        run_watch(chunk=args.chunk or None, out=args.out or None)
    elif args.mode == "search":
        run_search(args.keyword, args.region,
                   chunk=args.chunk or None, out=args.out or None)
    elif args.mode == "map":
        run_map(chunk=args.chunk or None, out=args.out or None)
    elif args.mode == "aggregate":
        run_aggregate(args.target, args.keyword, args.indir)


if __name__ == "__main__":
    main()
