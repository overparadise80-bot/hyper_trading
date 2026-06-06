# -*- coding: utf-8 -*-
"""
primary_notice.py - 매일 저녁 8시 DART 공시 브리핑
- 실적 공시 (매출액, 영업이익 YoY)
- 수주 공시 (총규모, 매출액 대비 비율, 거래상대방)
- 유상증자 (조달금액, 증자방식, 신주배정)
DART OpenAPI 사용 (무료)
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")
DART_API_KEY   = os.getenv("DART_API_KEY", "")   # .env에 추가 필요

DART_BASE = "https://opendart.fss.or.kr/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

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

# =============================================================
# DART API 공통 조회
# =============================================================
def dart_get(endpoint: str, params: dict) -> dict:
    try:
        params["crtfc_key"] = DART_API_KEY
        resp = requests.get(
            f"{DART_BASE}/{endpoint}",
            params=params,
            headers=HEADERS,
            timeout=15
        )
        return resp.json()
    except Exception as e:
        print(f"  DART API 오류 ({endpoint}): {e}")
        return {}

def get_today() -> str:
    return datetime.now().strftime("%Y%m%d")

# =============================================================
# 1. 당일 공시 목록 조회
# =============================================================
def fetch_today_disclosures() -> list:
    """당일 전체 공시 목록"""
    today = get_today()
    print(f"  [DART] 당일 공시 조회: {today}")

    data = dart_get("list.json", {
        "bgn_de": today,
        "end_de": today,
        "page_count": 100,
        "sort": "date",
        "sort_mth": "desc",
    })

    if data.get("status") != "000":
        print(f"  DART 오류: {data.get('message', '알수없음')}")
        return []

    return data.get("list", [])

# =============================================================
# 2. 실적 공시 파싱
# =============================================================
EARNINGS_KEYWORDS = [
    "영업실적", "매출액", "잠정실적", "실적발표",
    "분기보고서", "반기보고서", "사업보고서"
]

def is_earnings(title: str) -> bool:
    return any(kw in title for kw in EARNINGS_KEYWORDS)

def fetch_earnings_detail(rcept_no: str) -> dict:
    """실적 공시 상세 (매출액, 영업이익)"""
    try:
        data = dart_get("fnlttSinglAcnt.json", {
            "rcept_no": rcept_no,
            "reprt_code": "11011",   # 사업보고서
            "fs_div": "CFS",         # 연결재무제표
        })
        if data.get("status") != "000":
            return {}

        items = data.get("list", [])
        result = {}
        for item in items:
            nm = item.get("account_nm", "")
            if "매출액" in nm:
                result["매출액_당기"] = item.get("thstrm_amount", "")
                result["매출액_전기"] = item.get("frmtrm_amount", "")
            elif "영업이익" in nm:
                result["영업이익_당기"] = item.get("thstrm_amount", "")
                result["영업이익_전기"] = item.get("frmtrm_amount", "")
        return result
    except:
        return {}

def calc_yoy(current: str, previous: str) -> str:
    """YoY 등락률 계산"""
    try:
        c = int(current.replace(",", "").replace("-", "0") or "0")
        p = int(previous.replace(",", "").replace("-", "0") or "0")
        if p == 0:
            return "N/A"
        rate = (c - p) / abs(p) * 100
        return f"{rate:+.1f}%"
    except:
        return "N/A"

# =============================================================
# 3. 수주 공시 파싱
# =============================================================
ORDER_KEYWORDS = ["수주", "공급계약", "납품계약", "용역계약"]

def is_order(title: str) -> bool:
    return any(kw in title for kw in ORDER_KEYWORDS)

def fetch_order_detail(rcept_no: str, corp_name: str) -> dict:
    """수주 공시 상세"""
    try:
        data = dart_get("document.json", {"rcept_no": rcept_no})
        # DART document API는 XML 반환 - 간단히 텍스트 파싱
        return {"raw": True}   # 상세 파싱은 생략, 제목만 브리핑
    except:
        return {}

# =============================================================
# 4. 유상증자 공시 파싱
# =============================================================
RIGHTS_KEYWORDS = ["유상증자", "신주발행", "주주배정"]

def is_rights_offering(title: str) -> bool:
    return any(kw in title for kw in RIGHTS_KEYWORDS)

# =============================================================
# 5. 감자 공시
# =============================================================
REDUCTION_KEYWORDS = ["감자", "주식병합"]

def is_reduction(title: str) -> bool:
    return any(kw in title for kw in REDUCTION_KEYWORDS)

# =============================================================
# 메인 브리핑 실행
# =============================================================
def run_briefing():
    print(f"\n[{datetime.now().strftime('%H:%M')}] 주요 공시 브리핑 시작...")

    if not DART_API_KEY:
        send_telegram(
            "DART API 키가 설정되지 않았습니다.\n"
            ".env에 DART_API_KEY를 추가해주세요.\n"
            "발급: https://opendart.fss.or.kr"
        )
        return

    disclosures = fetch_today_disclosures()
    if not disclosures:
        send_telegram("오늘 주요 공시가 없습니다.")
        return

    # 카테고리별 분류
    earnings_list   = []
    order_list      = []
    rights_list     = []
    reduction_list  = []

    for item in disclosures:
        title     = item.get("report_nm", "")
        corp_name = item.get("corp_name", "")
        rcept_no  = item.get("rcept_no", "")
        rcept_dt  = item.get("rcept_dt", "")

        if is_earnings(title):
            earnings_list.append({
                "name": corp_name, "title": title,
                "rcept_no": rcept_no, "date": rcept_dt
            })
        elif is_order(title):
            order_list.append({
                "name": corp_name, "title": title,
                "rcept_no": rcept_no, "date": rcept_dt
            })
        elif is_rights_offering(title):
            rights_list.append({
                "name": corp_name, "title": title,
                "rcept_no": rcept_no, "date": rcept_dt
            })
        elif is_reduction(title):
            reduction_list.append({
                "name": corp_name, "title": title,
                "rcept_no": rcept_no, "date": rcept_dt
            })

    now_str = datetime.now().strftime("%m/%d %H:%M")

    # ── 메시지1: 실적 공시 ──
    msg1  = f"<b>주요 공시 브리핑</b> ({now_str})\n"
    msg1 += f"총 {len(disclosures)}건 공시 중 주요 항목\n"
    msg1 += "===================\n\n"

    msg1 += f"<b>실적 공시</b> ({len(earnings_list)}건)\n"
    msg1 += "--------------------\n"
    if earnings_list:
        for item in earnings_list[:10]:
            msg1 += f"• <b>{item['name']}</b>\n"
            msg1 += f"  {item['title']}\n"

            # 상세 조회 시도
            detail = fetch_earnings_detail(item["rcept_no"])
            if detail:
                rev_now  = detail.get("매출액_당기", "")
                rev_prev = detail.get("매출액_전기", "")
                op_now   = detail.get("영업이익_당기", "")
                op_prev  = detail.get("영업이익_전기", "")

                if rev_now:
                    yoy = calc_yoy(rev_now, rev_prev)
                    msg1 += f"  매출액: {rev_now}원 (YoY {yoy})\n"
                if op_now:
                    yoy = calc_yoy(op_now, op_prev)
                    msg1 += f"  영업이익: {op_now}원 (YoY {yoy})\n"
            msg1 += "\n"
    else:
        msg1 += "해당 공시 없음\n"
    msg1 += "\n"

    # ── 메시지2: 수주 공시 ──
    msg1 += f"<b>수주/공급계약 공시</b> ({len(order_list)}건)\n"
    msg1 += "--------------------\n"
    if order_list:
        for item in order_list[:10]:
            msg1 += f"• <b>{item['name']}</b>\n"
            msg1 += f"  {item['title']}\n\n"
    else:
        msg1 += "해당 공시 없음\n"
    msg1 += "\n"

    # ── 메시지3: 유상증자/감자 ──
    msg1 += f"<b>유상증자/감자 공시</b> ({len(rights_list)+len(reduction_list)}건)\n"
    msg1 += "--------------------\n"

    if rights_list:
        for item in rights_list[:5]:
            msg1 += f"• <b>[유상증자]</b> {item['name']}\n"
            msg1 += f"  {item['title']}\n\n"

    if reduction_list:
        for item in reduction_list[:5]:
            msg1 += f"• <b>[감자]</b> {item['name']}\n"
            msg1 += f"  {item['title']}\n\n"

    if not rights_list and not reduction_list:
        msg1 += "해당 공시 없음\n"

    send_telegram(msg1)
    print(f"  공시 브리핑 전송 완료")
    print(f"  실적:{len(earnings_list)} 수주:{len(order_list)} "
          f"증자:{len(rights_list)} 감자:{len(reduction_list)}")
    print(f"[{datetime.now().strftime('%H:%M')}] 공시 브리핑 완료!")

if __name__ == "__main__":
    run_briefing()