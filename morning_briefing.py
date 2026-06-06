# -*- coding: utf-8 -*-
"""
morning_briefing.py - 매일 아침 8시 자동 브리핑
1. 미국 주요 지수 + 원자재 + 금리
2. 연합인포맥스 뉴욕증시 기사 요약
3. 증권사 리서치 리포트 (네이버 검색 API - 개선버전)
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN")
CHAT_ID             = os.getenv("CHAT_ID")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 우선 키워드 (제목에 포함 시 선별)
PRIORITY_KEYWORDS = [
    "업사이드", "리레이팅", "멀티플", "실적", "서프라이즈",
    "성장", "구조적", "밸류체인", "수혜", "턴어라운드",
    "모멘텀", "호실적", "어닝", "급증", "최대",
    "목표주가", "상향", "신규편입", "매수", "강력매수",
    "성장성", "수주", "흑자전환", "최고치", "사상최대"
]

# 리포트 출처로 판단할 키워드 (증권사명)
FIRM_KEYWORDS = [
    "증권", "투자증권", "자산운용", "리서치", "애널리스트",
    "신한", "키움", "하나", "미래에셋", "KB", "삼성",
    "한국투자", "대신", "메리츠", "NH", "유안타", "SK"
]

# 제외 키워드 (노이즈)
EXCLUDE_KEYWORDS = [
    "광고", "홍보", "채용", "인사발령", "속보",
    "코인", "비트코인", "가상화폐"
]

# =============================================================
# 텔레그램 전송
# =============================================================
def send_telegram(msg: str):
    try:
        url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
        for chunk in chunks:
            requests.post(url, data={
                "chat_id":    CHAT_ID,
                "text":       chunk,
                "parse_mode": "HTML"
            }, timeout=10)
    except Exception as e:
        print(f"텔레그램 오류: {e}")

def get_page(url: str, timeout=10):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.encoding = resp.apparent_encoding
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  페이지 오류: {e}")
        return None

def clean_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&quot;', '"').replace('&amp;', '&')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&apos;', "'")
    return text.strip()

def highlight_keywords(text: str) -> str:
    for kw in PRIORITY_KEYWORDS:
        if kw in text:
            text = text.replace(kw, f"<b>{kw}</b>")
    return text

# =============================================================
# 1. 미국 주요 지수 (Yahoo Finance 직접 API 호출)
# =============================================================
def fetch_us_indices() -> str:
    print("  [1] 미국 지수 조회 (Yahoo Finance API)...")

    symbols = {
        "다우존스":        "^DJI",
        "나스닥":          "^IXIC",
        "S&P500":         "^GSPC",
        "필라델피아반도체":  "^SOX",
        "WTI유가":         "CL=F",
        "금(Gold)":        "GC=F",
        "미국채2년물":      "^IRX",
    }
    labels = {
        "다우존스":        "DOW   ",
        "나스닥":          "NASDAQ",
        "S&P500":         "S&P500",
        "필라델피아반도체":  "SOX   ",
        "WTI유가":         "WTI   ",
        "금(Gold)":        "GOLD  ",
        "미국채2년물":      "US2Y  ",
    }

    now_str = datetime.now().strftime("%m/%d %H:%M")
    msg  = f"<b>해외 시황 브리핑</b> ({now_str})\n"
    msg += "미국 전일 마감 기준\n"
    msg += "--------------------\n\n"

    yf_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    for name, symbol in symbols.items():
        try:
            url  = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                    f"?interval=1d&range=2d")
            resp = requests.get(url, headers=yf_headers, timeout=10)
            data = resp.json()

            result = data["chart"]["result"][0]
            closes = result["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]

            if len(closes) < 2:
                msg += f"  {labels[name]}: 조회실패\n"
                continue

            prev = closes[-2]
            curr = closes[-1]
            rate = (curr - prev) / prev * 100
            sign = "+" if rate >= 0 else ""

            if name == "미국채2년물":
                msg += f"  {labels[name]}: {curr:.2f}%  ({sign}{rate:.2f}%)\n"
            elif name in ("WTI유가", "금(Gold)"):
                msg += f"  {labels[name]}: ${curr:,.2f}  ({sign}{rate:.2f}%)\n"
            else:
                msg += f"  {labels[name]}: {curr:,.2f}  ({sign}{rate:.2f}%)\n"

        except Exception as e:
            print(f"    {name} 오류: {e}")
            msg += f"  {labels[name]}: 조회실패\n"

    return msg

# =============================================================
# 2. 뉴욕증시 기사 (네이버 뉴스 검색 API)
# =============================================================
def fetch_nyse_news() -> str:
    print("  [2] 뉴욕증시 기사 조회...")
    try:
        naver_headers = {
            "X-Naver-Client-Id":     NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers=naver_headers,
            params={
                "query":   "뉴욕증시 마감",
                "display": 5,
                "sort":    "date",
            },
            timeout=10
        )
        items = resp.json().get("items", [])
        if not items:
            return ""

        # 날짜 상관없이 가장 최신 뉴욕증시 기사 1개
        for item in items:
            title = clean_html(item.get("title", ""))
            desc  = clean_html(item.get("description", ""))
            pub   = item.get("pubDate", "")[:16]
            if desc:
                summary = desc[:150]
                return (
                    f"\n<b>뉴욕증시 요약</b> ({pub})\n"
                    f"[{title}]\n"
                    f"{summary}...\n"
                )
        return ""
    except Exception as e:
        print(f"  뉴욕증시 기사 오류: {e}")
        return ""

# =============================================================
# 3. 네이버 검색 API - 증권사 리서치 리포트
# =============================================================
def is_research_report(title: str, desc: str) -> bool:
    """증권사 리포트 여부 판단"""
    full_text = title + " " + desc

    # 제외 키워드 체크
    if any(kw in full_text for kw in EXCLUDE_KEYWORDS):
        return False

    # 증권사 관련 + 우선 키워드 둘 다 있어야 함
    has_firm    = any(kw in full_text for kw in FIRM_KEYWORDS)
    has_keyword = any(kw in full_text for kw in PRIORITY_KEYWORDS)

    return has_firm and has_keyword

def is_today_or_yesterday(pub_date: str) -> bool:
    """오늘 또는 어제 날짜 여부"""
    try:
        today     = datetime.now().strftime("%d %b %Y")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d %b %Y")
        return today in pub_date or yesterday in pub_date
    except:
        return True   # 날짜 파싱 실패 시 포함

def fetch_naver_research() -> list:
    print("  [3] 네이버 검색 API - 증권사 리서치...")

    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("  네이버 API 키 없음")
        return []

    naver_headers = {
        "X-Naver-Client-Id":     NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    # 정확도 높은 쿼리 목록
    queries = [
        "증권사 리포트 목표주가 상향",
        "증권사 리포트 실적 수혜",
        "애널리스트 투자의견 매수",
        "증권 리서치 성장 구조적",
        "증권사 리포트 서프라이즈",
        "증권 리포트 신규편입 추천",
    ]

    results = []
    seen    = set()

    for query in queries:
        try:
            resp = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers=naver_headers,
                params={
                    "query":   query,
                    "display": 20,
                    "sort":    "date",
                },
                timeout=10
            )
            data  = resp.json()
            items = data.get("items", [])

            for item in items:
                title = clean_html(item.get("title", ""))
                desc  = clean_html(item.get("description", ""))
                date  = item.get("pubDate", "")
                link  = item.get("link", "")

                # 중복 제거
                if title in seen:
                    continue

                # 날짜 필터 (오늘/어제)
                if not is_today_or_yesterday(date):
                    continue

                # 리서치 리포트 여부 판단
                if not is_research_report(title, desc):
                    continue

                seen.add(title)
                results.append({
                    "title": title,
                    "desc":  desc[:80] if desc else "",
                })

        except Exception as e:
            print(f"  네이버 검색 오류 ({query}): {e}")

    print(f"  리서치 {len(results)}개 수집")
    return results

# =============================================================
# 메인 브리핑 실행
# =============================================================
def run_briefing():
    print(f"\n[{datetime.now().strftime('%H:%M')}] 모닝 브리핑 시작...")

    # 1. 미국 지수
    msg_indices = fetch_us_indices()

    # 2. 뉴욕증시 기사
    news_summary = fetch_nyse_news()
    if news_summary:
        msg_indices += news_summary

    send_telegram(msg_indices)
    print("  지수 브리핑 전송 완료")

    # 3. 리서치 리포트
    reports = fetch_naver_research()
    reports = reports[:10]   # 최대 10개

    now_str      = datetime.now().strftime("%m/%d %H:%M")
    msg_research = f"<b>증권사 리서치 브리핑</b> ({now_str})\n"
    msg_research += "네이버 검색 기준 | 증권사 + 우선키워드 필터\n"
    msg_research += "--------------------\n\n"

    if reports:
        for i, r in enumerate(reports, 1):
            title = highlight_keywords(r["title"])
            msg_research += f"{i}. {title}\n"
            if r["desc"]:
                msg_research += f"   {r['desc']}\n"
            msg_research += "\n"
    else:
        msg_research += "오늘 해당 키워드 리포트 없음\n"

    send_telegram(msg_research)
    print(f"  리서치 브리핑 전송 완료 ({len(reports)}개)")
    print(f"[{datetime.now().strftime('%H:%M')}] 모닝 브리핑 완료!")

if __name__ == "__main__":
    run_briefing()