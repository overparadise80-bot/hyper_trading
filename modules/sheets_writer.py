# -*- coding: utf-8 -*-
"""
sheets_writer.py - 구글 스프레드시트 주도섹터 자동 기록
매일 15:45 QTimer로 자동 기록

날짜 1행 + 테마 헤더 1행 + 주도주 5행 = 하루치 6행 블록, 최신이 위
  행  │ A(날짜)     │ B(1위)          │ C(2위)          │ ... │ H(7위)
  2   │ 26/06/21   │ 반도체 +3.2% 2조 │ AI +5.1% 8500억 │ ... │
  3   │            │ 삼성전자 +3.5%   │ 두산로보틱 +8.1% │ ... │
  ...
"""

import hashlib
import os
import threading
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials

_BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(_BASE_DIR, "wansur1929-1124da2160c2.json")
SPREADSHEET_ID   = "14XuC7rFkFcEjae-qzsnyh6dI2peI2AAMmMKRlV3NO3M"
SHEET_NAME       = "주도섹터"

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SECTOR_TOP_N = 7
STOCK_TOP_N  = 5
ROWS_PER_DAY = 1 + STOCK_TOP_N   # 6행

_WHITE  = {"red": 1.00, "green": 1.00, "blue": 1.00}
_HDR_BG = {"red": 0.16, "green": 0.16, "blue": 0.20}

def _hsl_to_rgb(h: float, s: float, l: float) -> tuple:
    if s == 0:
        return l, l, l
    def _hue(p, q, t):
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q - p) * 6 * t
        if t < 1/2: return q
        if t < 2/3: return p + (q - p) * (2/3 - t) * 6
        return p
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    return _hue(p, q, h + 1/3), _hue(p, q, h), _hue(p, q, h - 1/3)


def _sector_color(theme_name: str) -> dict:
    """
    섹터명 → 고정 파스텔 배경색
    - 같은 섹터명: 날짜가 달라도 항상 동일한 색
    - 다른 섹터명: 0~359° 연속 색조 공간에서 1/360 미만 확률로만 동일
    - 채도 0.45 / 명도 0.88 → 눈이 편한 파스텔 톤
    """
    # 128비트 전체 해시로 색조 결정 → 섹터명마다 고유 hue 보장
    digest = int(hashlib.md5(theme_name.encode("utf-8")).hexdigest(), 16)
    hue = (digest % 360) / 360.0
    r, g, b = _hsl_to_rgb(hue, 0.45, 0.88)
    return {"red": round(r, 3), "green": round(g, 3), "blue": round(b, 3)}


def _col_letter(col: int) -> str:
    result = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        result = chr(65 + rem) + result
    return result


def _format_amount(eok: int) -> str:
    if eok >= 10000:
        jo, rem = divmod(eok, 10000)
        return f"{jo}조 {rem}억" if rem else f"{jo}조"
    return f"{eok}억"


def _ensure_header(ws: gspread.Worksheet):
    first = ws.row_values(1)
    if first and first[0] == "날짜":
        return
    header = ["날짜"] + [f"{i}위" for i in range(1, SECTOR_TOP_N + 1)]
    ws.insert_row(header, 1)
    last_col = _col_letter(1 + SECTOR_TOP_N)
    ws.format(f"A1:{last_col}1", {
        "backgroundColor": _HDR_BG,
        "textFormat": {"bold": True, "foregroundColor": _WHITE, "fontSize": 10},
        "horizontalAlignment": "CENTER",
    })


def _build_rows(theme_ranking: list) -> list:
    today  = datetime.now().strftime("%y/%m/%d")
    themes = theme_ranking[:SECTOR_TOP_N]

    theme_row = [today]
    for t in themes:
        theme_row.append(
            f"{t['theme']}  {t['avg_rate']:+.2f}%\n{_format_amount(t['total_amount'])}"
        )
    while len(theme_row) < 1 + SECTOR_TOP_N:
        theme_row.append("")

    stock_rows = []
    for si in range(STOCK_TOP_N):
        row = [""]
        for t in themes:
            stocks = t["stocks"][:STOCK_TOP_N]
            if si < len(stocks):
                s = stocks[si]
                row.append(f"{s['name']}  {s['rate']:+.2f}%")
            else:
                row.append("")
        while len(row) < 1 + SECTOR_TOP_N:
            row.append("")
        stock_rows.append(row)

    return [theme_row] + stock_rows


def write_daily_sector(theme_ranking: list):
    """비동기로 구글 시트에 오늘 주도섹터 6행 블록 기록"""
    def _write():
        try:
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
            gc    = gspread.authorize(creds)
            sh    = gc.open_by_key(SPREADSHEET_ID)

            try:
                ws = sh.worksheet(SHEET_NAME)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=20)

            _ensure_header(ws)

            rows = _build_rows(theme_ranking)
            ws.insert_rows(rows, row=2)

            last_stock_row = 2 + ROWS_PER_DAY - 1   # = 7
            last_theme_col = _col_letter(1 + SECTOR_TOP_N)
            themes         = theme_ranking[:SECTOR_TOP_N]
            fmt_requests   = []

            # 날짜 열 (A2:A7) — 흰색 배경, 가운데 정렬, 병합
            ws.merge_cells(f"A2:A{last_stock_row}")
            fmt_requests.append({
                "range": f"A2:A{last_stock_row}",
                "format": {
                    "textFormat": {"bold": True, "fontSize": 10},
                    "backgroundColor": _WHITE,
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }
            })

            # 주도주 행 (B3:H7) — 흰색 배경 일괄 초기화
            fmt_requests.append({
                "range": f"B3:{last_theme_col}{last_stock_row}",
                "format": {
                    "textFormat": {"bold": False, "fontSize": 9},
                    "backgroundColor": _WHITE,
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }
            })

            # 테마 헤더 행 (row 2) — 섹터별 고정 파스텔 색 + 굵은 글씨
            for ci, t in enumerate(themes):
                col_letter = _col_letter(2 + ci)
                fmt_requests.append({
                    "range": f"{col_letter}2",
                    "format": {
                        "textFormat": {"bold": True, "fontSize": 10},
                        "backgroundColor": _sector_color(t["theme"]),
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                })

            if fmt_requests:
                ws.batch_format(fmt_requests)

            print(f"  [Sheets] 주도섹터 기록 완료: {datetime.now().strftime('%Y/%m/%d %H:%M')}")

        except Exception as e:
            print(f"  [Sheets] 기록 오류: {e}")

    threading.Thread(target=_write, daemon=True).start()


def setup_sheets_timer(get_theme_ranking):
    """
    15:45에 구글 시트 자동 기록 QTimer 설정
    get_theme_ranking: theme_ranking 리스트를 반환하는 callable (lambda 등)
    condition_kiwoom_v2.py on_condition_load에서 한 번 호출
    """
    from PyQt5.QtCore import QTimer
    now    = datetime.now()
    target = now.replace(hour=15, minute=45, second=0, microsecond=0)
    if now >= target:
        print("  [Sheets] 15:45 이미 지남 — 타이머 스킵")
        return
    ms = int((target - now).total_seconds() * 1000)

    timer = QTimer()
    timer.setSingleShot(True)

    def _fire():
        ranking = get_theme_ranking()
        if ranking:
            print("  [Sheets] 15:45 자동 기록 실행")
            write_daily_sector(ranking)
        else:
            print("  [Sheets] 15:45 theme_ranking 없음 — 스킵")

    timer.timeout.connect(_fire)
    timer.start(ms)
    # QTimer는 Qt 이벤트루프에서 살아있어야 하므로 전역 참조 유지
    setup_sheets_timer._timer = timer
    print(f"  [Sheets] 15:45 자동 기록 타이머 설정 ({ms // 1000}초 후)")
