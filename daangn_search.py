#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
당근마켓 검색·알림 봇 (깃허브 액션용 단발 실행판)

이 스크립트는 한 번 실행되면 일을 끝내고 바로 종료한다.
24시간 켜두는 봇이 아니라, 깃허브 액션이 정해진 시점에 깨워서 돌리는 구조다.

모드:
  watch  : keywords.json 의 키워드들을 전국에서 찾아 '새 매물'만 텔레그램 알림
           (seen.json 에 이미 본 매물을 기록해 중복 알림을 막는다)
  search : 키워드 1개를 즉석으로 전국/지역 검색, 찾은 매물 전부 텔레그램 전송
  map    : 전국 지역코드(region_map.json)를 새로 수집

비밀정보(텔레그램·노션 토큰)는 코드에 적지 않고 모두 환경변수로 받는다.
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

# ==================== 설정 (비밀정보는 환경변수에서) ====================
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID         = os.environ.get("TELEGRAM_CHAT_ID", "")
NOTION_TOKEN    = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE = os.environ.get("NOTION_DATABASE_ID", "")

REGION_MAP_FILE = "region_map.json"
KEYWORDS_FILE   = "keywords.json"
SEEN_FILE       = "seen.json"

SEEN_TTL_DAYS   = 30      # 이 일수보다 오래된 '본 매물' 기록은 정리
MAX_WORKERS     = 4       # 동시 요청 수 (너무 높이면 차단 위험)
SEND_DELAY      = 0.4     # 텔레그램 메시지 사이 간격(초) — 도배 제한 회피

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
        print("[텔레그램 미설정] " + msg.replace("\n", " | "))
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"[텔레그램 오류] {e}")


def send_item(item):
    """매물 1건을 텔레그램으로 전송 (+ 노션 적재)."""
    kw = f" ({item['keyword']})" if item.get("keyword") else ""
    send_telegram(
        f"✨ [{item['addr']}]{kw}\n"
        f"📦 {item['title']}\n"
        f"💰 {item['price']}\n"
        f"🔗 {item['link']}"
    )
    push_notion(item)
    time.sleep(SEND_DELAY)


# ==================== 노션 적재 ====================
def push_notion(item):
    """노션 DB에 매물 1건 기록. 토큰/DB가 없으면 조용히 건너뛴다."""
    if not NOTION_TOKEN or not NOTION_DATABASE:
        return
    try:
        price_num = int(re.sub(r"[^\d]", "", item["price"]) or 0)
        requests.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={
                "parent": {"database_id": NOTION_DATABASE},
                "properties": {
                    "상품명": {"title":     [{"text": {"content": item["title"][:100]}}]},
                    "검색어": {"rich_text": [{"text": {"content": item.get("keyword", "")}}]},
                    "지역명": {"rich_text": [{"text": {"content": item["addr"]}}]},
                    "가격":   {"number": price_num},
                    "링크":   {"url": item["link"]},
                },
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[노션 오류] {e}")


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


# ==================== 페이지 파싱 ====================
def _fix(text):
    """당근 페이지의 깨진 한글 인코딩 복구."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except Exception:
        return text


def parse_page(html):
    """페이지 HTML -> (지역명, [진행중 매물 dict ...])"""
    m = re.search(r"window\.__remixContext\s*=\s*({.*?});", html, re.DOTALL)
    if not m:
        return None, []
    try:
        data = json.loads(m.group(1).encode("utf-8", "replace").decode("utf-8"))
        main = data.get("state", {}).get("loaderData", {}).get("routes/kr.buy-sell._index", {})
        reg  = main.get("region", {})
        addr = f"{_fix(reg.get('depth1RegionName',''))} {_fix(reg.get('depth2RegionName',''))}".strip()

        exclude = ["판매완료", "거래완료", "완료", "솔드아웃", "예약중"]
        items = []
        for it in main.get("allPage", {}).get("fleamarketArticles", []):
            if it.get("status", "") != "Ongoing":
                continue
            title = _fix(it.get("title", ""))
            if any(ex in title.replace(" ", "") for ex in exclude):
                continue
            try:
                price = format(int(float(it.get("price", 0))), ",") + "원"
            except Exception:
                price = "0원"
            id_val = it.get("id", "")
            art_id = id_val.split("/")[-2] if "/" in id_val else ""
            items.append({
                "title": title,
                "price": price,
                "addr":  addr or "지역미상",
                "id":    art_id,
                "link":  f"https://www.daangn.com/articles/{art_id}",
            })
        return addr, items
    except Exception:
        return None, []


def fetch_region(tid, keyword=None):
    """지역 페이지 1개를 가져와 매물 목록을 반환. keyword 를 주면 당근 서버에서 미리 검색."""
    time.sleep(random.uniform(0.4, 1.0))
    params = {"in": tid}
    if keyword:
        params["search"] = keyword
    try:
        res = requests.get(
            "https://www.daangn.com/kr/buy-sell/",
            params=params,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=12,
        )
        return parse_page(res.text)
    except Exception:
        return None, []


# ==================== 모드: watch (알림봇) ====================
def run_watch():
    region_map = load_json(REGION_MAP_FILE, {})
    if not region_map:
        send_telegram("❌ 알림봇: region_map.json 이 비어 있습니다.\n먼저 '지역코드 수집' 워크플로우를 한 번 실행하세요.")
        sys.exit(1)

    keywords = load_json(KEYWORDS_FILE, [])
    if not keywords:
        send_telegram("⚠️ 알림봇: keywords.json 에 키워드가 없습니다.")
        sys.exit(0)

    seen = load_json(SEEN_FILE, {})
    first_run = len(seen) == 0   # 첫 실행이면 알림 도배를 막기 위해 조용히 기록만 한다

    tids = sorted(int(t) for t in region_map.keys())
    norm_keywords = [(k, k.replace(" ", "").lower()) for k in keywords]
    today = datetime.date.today().isoformat()

    print(f"[watch] 키워드 {keywords} / 지역 {len(tids)}개 / 첫실행={first_run}")

    new_items = []
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_region, tid) for tid in tids]
        for fut in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  진행 {done}/{len(tids)} ... 신규 {len(new_items)}건")
            _, articles = fut.result()
            for art in articles:
                if not art["id"] or art["link"] in seen:
                    continue
                title_norm = art["title"].replace(" ", "").lower()
                for kw, kw_norm in norm_keywords:
                    if kw_norm in title_norm:
                        seen[art["link"]] = today
                        art["keyword"] = kw
                        new_items.append(art)
                        break

    prune_seen(seen)
    save_json(SEEN_FILE, seen)

    if first_run:
        send_telegram(f"🥕 알림봇 가동 시작!\n현재 매물 {len(new_items)}건을 기록했습니다.\n다음 실행부터 '새 매물'만 알려드립니다.")
        print(f"[watch] 첫 실행 — {len(new_items)}건 기록만 함 (알림 생략)")
        return

    for it in new_items:
        send_item(it)
    if new_items:
        send_telegram(f"🥕 알림봇: 새 매물 {len(new_items)}건 발견 ({', '.join(keywords)})")
    print(f"[watch] 완료 — 새 매물 {len(new_items)}건")


def prune_seen(seen):
    """오래된 '본 매물' 기록을 정리해 파일이 무한정 커지지 않게 한다."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=SEEN_TTL_DAYS)).isoformat()
    for link in [k for k, v in seen.items() if v < cutoff]:
        del seen[link]


# ==================== 모드: search (즉석검색) ====================
def run_search(keyword, region):
    keyword = (keyword or "").strip()
    if not keyword:
        send_telegram("❌ 즉석검색: 키워드가 비어 있습니다.")
        sys.exit(1)

    region_map = load_json(REGION_MAP_FILE, {})

    if region and region.strip():
        tids = []
        bad  = []
        for r in [x.strip() for x in region.replace(" ", "").split(",") if x.strip()]:
            found = get_tids_by_region(region_map, r)
            (tids.extend(found) if found else bad.append(r))
        tids = sorted(set(tids))
        if not tids:
            send_telegram(f"❌ 즉석검색: '{region}' 지역을 찾을 수 없습니다.")
            sys.exit(1)
        scope = region.strip()
        if bad:
            send_telegram(f"⚠️ 즉석검색: '{', '.join(bad)}' 지역은 못 찾아 건너뜁니다.")
    else:
        if not region_map:
            send_telegram("❌ 즉석검색: region_map.json 이 비어 있습니다.\n먼저 '지역코드 수집'을 실행하세요.")
            sys.exit(1)
        tids  = sorted(int(t) for t in region_map.keys())
        scope = "전국"

    send_telegram(f"🔍 즉석검색 시작\n키워드: {keyword}\n범위: {scope} ({len(tids)}개 지역)")
    print(f"[search] '{keyword}' / {scope} / {len(tids)}개 지역")

    kw_norm = keyword.replace(" ", "").lower()
    results = []
    seen_fp = set()
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_region, tid, keyword) for tid in tids]
        for fut in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  진행 {done}/{len(tids)} ... 발견 {len(results)}건")
            _, articles = fut.result()
            for art in articles:
                if kw_norm not in art["title"].replace(" ", "").lower():
                    continue
                fp = f"{art['title']}_{art['price']}_{art['addr']}"
                if fp in seen_fp:
                    continue
                seen_fp.add(fp)
                art["keyword"] = keyword
                results.append(art)

    # CSV 저장 (워크플로우가 결과물로 첨부)
    csv_name = f"검색결과_{keyword.replace(' ', '_')}.csv"
    with open(csv_name, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["지역", "상품명", "가격", "링크"])
        for it in results:
            w.writerow([it["addr"], it["title"], it["price"], it["link"]])

    for it in results:
        send_item(it)
    send_telegram(f"🏁 즉석검색 완료\n키워드: {keyword}\n총 {len(results)}건 발견")
    print(f"[search] 완료 — {len(results)}건")


# ==================== 모드: map (지역코드 수집) ====================
def run_map():
    print("[map] 전국 지역코드 수집 시작 (1~8500)")
    ids = list(range(1, 8501, 2))
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
            addr, _ = parse_page(res.text)
            if addr:
                with lock:
                    new_map[str(tid)] = addr
        except Exception:
            pass
        with lock:
            done[0] += 1
            if done[0] % 500 == 0:
                print(f"  진행 {done[0]}/{len(ids)} ... 수집 {len(new_map)}개")

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(one, ids))

    region_map = load_json(REGION_MAP_FILE, {})
    region_map.update(new_map)
    save_json(REGION_MAP_FILE, region_map)
    send_telegram(f"🗺️ 지역코드 수집 완료\n총 {len(region_map)}개 지역 저장")
    print(f"[map] 완료 — region_map.json 총 {len(region_map)}개")


# ==================== 실행 진입점 ====================
def main():
    parser = argparse.ArgumentParser(description="당근마켓 검색·알림 봇")
    parser.add_argument("--mode", required=True, choices=["watch", "search", "map"])
    parser.add_argument("--keyword", default="", help="search 모드의 검색 키워드")
    parser.add_argument("--region",  default="", help="search 모드의 지역 (비우면 전국)")
    args = parser.parse_args()

    if args.mode == "watch":
        run_watch()
    elif args.mode == "search":
        run_search(args.keyword, args.region)
    elif args.mode == "map":
        run_map()


if __name__ == "__main__":
    main()
