# -*- coding: utf-8 -*-
"""
common.py - 공통 설정 / 텔레그램 / 유틸
모든 모듈이 이 파일을 import해서 사용
"""

import os
import threading
import requests
from datetime import datetime, time
from dotenv import load_dotenv

load_dotenv()

# =============================================================
# API 설정
# =============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

# =============================================================
# 모의투자 계좌
# =============================================================
ACCOUNT_NUM = "8126033411"

# =============================================================
# 모듈1 설정
# =============================================================
M1_START    = time(9, 10)
M1_END      = time(15, 10)
M1_INTERVAL = 15 * 60 * 1000   # 15분

# 섹터 TOP7, 주도주 TOP4 (등락률 조건 없음)
M1_SECTOR_TOP  = 7
M1_STOCK_TOP   = 4

# =============================================================
# 모듈2 황사장 설정
# =============================================================
M2_START      = time(9, 5)
M2_END        = time(14, 0)
AUTO_TRADE_CONDITION = "단타검색식황사장"
M2_SCREEN     = "0211"

# =============================================================
# 모듈2 전일고점돌파 설정
# =============================================================
GDJUM_CONDITION  = "단타검색식전일고점돌파"
GDJUM_SCREEN     = "0210"
GDJUM_TICK_MIN   = 1e8    # 호가당 최소 1억원
GDJUM_VOL_MULT   = 2.0    # 거래량 배율
GDJUM_CANDLE_N   = 60     # 평균 거래량 기준 캔들 수
GDJUM_TICK_DOWN  = 3      # 진입가 = 전일고점 - 3틱
GDJUM_RISE_SKIP  = 0.08   # 8% 이상 상승 후 눌림 스킵

# =============================================================
# 모듈3 종가베팅 설정
# =============================================================
M3_SCAN_HOUR    = 15
M3_SCAN_MINUTE  = 18
M3_MAX_STOCKS   = 5
M3_CONDITION    = "급등후조정반등"
M3_RATE_LIMIT   = 5.0
M3_TAIL_MULT    = 2.5
M3_INST_DAYS    = 10
M3_INST_MIN     = 5

# =============================================================
# 모듈4 찰나의 매매 설정
# =============================================================
M4_START          = time(9, 0)
M4_END            = time(15, 20)
M4_MAX_POSITIONS  = 5
H52_MAX_POSITIONS = 10
TOP_N             = 100
BULK_LOW          = 3000
BULK_MID          = 1000
BULK_HIGH         = 500
PRICE_B1          = 50000
PRICE_B2          = 100000
SELL_BUY_RATIO    = 2.0
CHEGYUL_MIN       = 100.0
WALL_BREAK_RATE   = 0.10   # 매도벽 붕괴 기준: (직전매도잔량-현재매도잔량) / 매수잔량 >= 이 값
ALERT_COOLDOWN    = 300

# =============================================================
# 공통 매매 설정
# =============================================================
ENTRY_AMOUNT     = 250000   # 1차 진입금액
ADD_AMOUNT       = 250000   # 2차 추가금액
HIGH_PRICE_LIMIT = 250000   # 이 가격 초과 시 1주
ADD_BUY_RATE     = -0.02    # 2차 매수 조건
ADD_BUY_MINUTES  = 20       # 2차 매수 대기 시간
STOP_LOSS_RATE   = -0.025   # 손절
TRAIL_ACTIVATE   = 0.03     # 트레일링 활성화
MAX_POSITIONS    = 15       # 전체 최대 보유 종목

TRAIL_STOP = [
    (0.13, -0.05),
    (0.08, -0.04),
    (0.05, -0.03),
]

NOON_CUTOFF    = time(12, 0)
FORCE_EXIT_ALL = time(14, 50)
HOLD_MINUTES   = 90

# =============================================================
# 텔레그램
# =============================================================
def send_telegram(msg: str):
    def _send():
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
    threading.Thread(target=_send, daemon=True).start()

# =============================================================
# 유틸
# =============================================================
def is_weekday() -> bool:
    return datetime.now().weekday() < 5

def is_open(start: time, end: time) -> bool:
    if not is_weekday():
        return False
    return start <= datetime.now().time() <= end

def is_m1_open() -> bool:
    return is_open(M1_START, M1_END)

def is_m2_open() -> bool:
    return is_open(M2_START, M2_END)

def is_m4_open() -> bool:
    return is_open(M4_START, M4_END)

def get_tick_size(price: int) -> int:
    if price < 1000:     return 1
    elif price < 5000:   return 5
    elif price < 10000:  return 10
    elif price < 50000:  return 50
    elif price < 100000: return 100
    elif price < 500000: return 500
    else:                return 1000

def calc_qty(price: int) -> int:
    if price > HIGH_PRICE_LIMIT:
        return 1
    return max(1, ENTRY_AMOUNT // price)

def now_str() -> str:
    return datetime.now().strftime("%m/%d %H:%M")





    


    