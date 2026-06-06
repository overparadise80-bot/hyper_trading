# -*- coding: utf-8 -*-
import sys
import os
import json
import requests
from dotenv import load_dotenv
from datetime import datetime, time
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

app    = QApplication(sys.argv)
kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

# =============================================================
# 설정
# =============================================================
# 모의투자 계좌
ACCOUNT_NUM = "8126033411"

# 모듈1: 주도섹터 + 52주신고가
M1_START    = time(9, 10)
M1_END      = time(15, 10)
M1_INTERVAL = 15 * 60 * 1000   # 15분

# 모듈2: 단타 조건검색 실시간
M2_START      = time(9, 5)
M2_END        = time(14, 0)
M2_CONDITIONS = ["단타검색식전일고점돌파", "단타검색식황사장"]
M2_SCREENS    = {
    "단타검색식전일고점돌파": "0210",
    "단타검색식황사장":       "0211",
}

# 모듈2-1: 자동매매 대상 조건식
AUTO_TRADE_CONDITION = "단타검색식황사장"

# 매매 설정
ENTRY_AMOUNT     = 250000   # 1차 진입금액 (25만원)
ADD_AMOUNT       = 250000   # 2차 추가금액 (25만원)
HIGH_PRICE_LIMIT = 250000   # 이 가격 초과 시 1주 단위
ADD_BUY_RATE     = -0.02    # 2차 매수 조건: -2%
ADD_BUY_MINUTES  = 20       # 2차 매수 대기 시간 (분)
STOP_LOSS_RATE   = -0.025   # 손절: -2.5%
TRAIL_ACTIVATE   = 0.03     # 트레일링 스탑 활성화: +3%
MAX_POSITIONS    = 15       # 최대 보유 종목 수

# 트레일링 스탑 비율 (진입가 대비 수익률 구간별)
TRAIL_STOP = [
    (0.13, -0.05),   # 13% 이상 -> 고점 대비 -5%
    (0.08, -0.04),   # 8% 이상  -> 고점 대비 -4%
    (0.05, -0.03),   # 5% 이상  -> 고점 대비 -3%
]

# 청산 시간 규칙
NOON_CUTOFF    = time(12, 0)   # 정오 이후 진입 -> 1시간 내 청산
FORCE_EXIT_ALL = time(14, 50)  # 일괄 청산 시각
HOLD_MINUTES   = 90            # 일반 보유 한도 (분)

with open("sector_map.json", "r", encoding="utf-8") as f:
    SECTOR_MAP = json.load(f)

UPJONG_MAP = {
    "005": "음식료품", "006": "섬유의복", "007": "종이목재",
    "008": "화학",     "009": "의약품",   "010": "비금속광물",
    "011": "철강금속", "012": "기계",     "013": "전기전자",
    "014": "의료정밀", "015": "운수장비", "016": "유통업",
    "017": "전기가스업","018": "건설업",  "019": "운수창고",
    "020": "통신업",   "021": "금융업",   "022": "은행",
    "024": "증권",     "025": "보험",     "026": "서비스업",
}
UPJONG_CODES = list(UPJONG_MAP.keys())

# =============================================================
# 전역 타이머
# =============================================================
repeat_timer_m1 = QTimer()

# =============================================================
# 모듈1 전역 상태
# =============================================================
condition_list       = {}
upjong_data          = {}
upjong_idx           = 0
upjong_stock_queue   = []
upjong_stock_idx     = 0
upjong_stock_rate    = {}
upjong_stock_results = {}
theme_stock_map      = {}
stock_name_map       = {}
stock_rate_map       = {}
theme_scan_queue     = []
theme_scan_idx       = 0
shingoga_codes       = []
shingoga_detail      = {}
shingoga_idx         = 0
top7_upjong_codes    = []

# =============================================================
# 모듈2 전역 상태
# =============================================================
m2_realtime_cache = {}   # {코드: {condition, time, name, notified}}

# 편입 후 기본정보 조회 큐
m2_detail_queue = []
m2_detail_idx   = 0

# =============================================================
# 모듈2-1: 포지션 관리
# =============================================================
# positions: {종목코드: {
#   name, entry_price, qty, total_qty,
#   entry_time (datetime), entry_amount,
#   high_price,           # 고점 (트레일링용)
#   stop_price,           # 현재 스탑로스 가격
#   trail_active,         # 트레일링 스탑 활성화 여부
#   add_bought,           # 2차 매수 완료 여부
#   add_timer,            # 2차 매수 QTimer
#   condition,            # 편입된 조건식명
#   noon_entry,           # 정오 이후 진입 여부
# }}
positions = {}

# 주문 접수 후 체결 확인용
# {주문번호: {code, order_type, qty}}
pending_orders = {}

# 실시간 체결가 구독 중인 코드 집합
realtime_subscribed = set()

# 스크린번호 카운터 (주문/실시간 용)
_screen_counter = 300

def next_screen():
    global _screen_counter
    _screen_counter += 1
    if _screen_counter > 9999:
        _screen_counter = 300
    return str(_screen_counter).zfill(4)

# 14:50 일괄청산 타이머
force_exit_timer = QTimer()

# =============================================================
# 공통 유틸
# =============================================================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id":    CHAT_ID,
            "text":       msg,
            "parse_mode": "HTML"
        })
    except Exception as e:
        print(f"텔레그램 오류: {e}")

def safe_disconnect(signal, slot=None):
    try:
        if slot:
            signal.disconnect(slot)
        else:
            signal.disconnect()
    except Exception:
        pass

def is_m1_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return M1_START <= now.time() <= M1_END

def is_m2_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return M2_START <= now.time() <= M2_END

def calc_qty(price):
    """진입금액 기준 수량 계산 (25만원 초과 종목은 1주)"""
    if price > HIGH_PRICE_LIMIT:
        return 1
    return max(1, ENTRY_AMOUNT // price)

# =============================================================
# 로그인 + 조건식 로드
# =============================================================
def on_login(err_code):
    if err_code == 0:
        print("로그인 성공!")
        QTimer.singleShot(1000, lambda:
            kiwoom.dynamicCall("GetConditionLoad()"))
    else:
        print(f"로그인 실패: {err_code}")

def on_condition_load():
    result = kiwoom.dynamicCall("GetConditionNameList()")
    for c in result.split(';'):
        if not c:
            continue
        parts = c.split('^')
        if len(parts) >= 2:
            condition_list[parts[1]] = parts[0]
    print(f"조건식 로드 완료: {list(condition_list.keys())}")

    # 모듈1 타이머
    repeat_timer_m1.timeout.connect(on_interval_m1)
    repeat_timer_m1.start(M1_INTERVAL)
    print("모듈1 15분 타이머 시작")

    # 체결 이벤트 연결
    kiwoom.OnReceiveChejanData.connect(on_chejan)

    # 14:50 일괄청산 타이머 설정
    setup_force_exit_timer()

    # 모듈2 실시간 등록
    QTimer.singleShot(1000, register_m2_realtime)

    # 모듈3 15:18 타이머 설정
    setup_m3_timer()

    # 모듈4 거래량 상위100 조회 + 30분 갱신 타이머
    QTimer.singleShot(1500, fetch_top100)
    m4_top100_timer.timeout.connect(fetch_top100)
    m4_top100_timer.start(30 * 60 * 1000)
    print("모듈4 top100 타이머 시작 (30분)")

    # 모듈1 초기 실행
    QTimer.singleShot(2000, start_m1_scan)

def setup_force_exit_timer():
    """14:50 일괄청산 타이머: 매일 장중에 정확한 시각에 발동"""
    now = datetime.now()
    target = now.replace(hour=14, minute=50, second=0, microsecond=0)
    if now >= target:
        return   # 이미 지난 경우 스킵
    ms = int((target - now).total_seconds() * 1000)
    force_exit_timer.setSingleShot(True)
    force_exit_timer.timeout.connect(force_exit_all)
    force_exit_timer.start(ms)
    print(f"14:50 일괄청산 타이머 설정 ({ms//1000}초 후)")

# =============================================================
# 모듈1 타이머 콜백
# =============================================================
def on_interval_m1():
    print(f"\n[{datetime.now().strftime('%H:%M')}] 모듈1 타이머 발동!")
    if is_m1_open():
        start_m1_scan()
    else:
        print("모듈1 장외 - 스캔 스킵")

# =============================================================
# 모듈2: 단타 조건검색 실시간 등록
# =============================================================
def register_m2_realtime():
    print("\n[모듈2] 실시간 조건검색 등록...")
    safe_disconnect(kiwoom.OnReceiveRealCondition, on_realtime_condition)
    kiwoom.OnReceiveRealCondition.connect(on_realtime_condition)
    safe_disconnect(kiwoom.OnReceiveTrCondition, on_receive_m2_initial)
    kiwoom.OnReceiveTrCondition.connect(on_receive_m2_initial)

    count = 0
    for cname in M2_CONDITIONS:
        if cname not in condition_list:
            print(f"  조건식 없음: {cname}")
            continue
        cidx   = condition_list[cname]
        screen = M2_SCREENS[cname]
        kiwoom.dynamicCall("SendCondition(QString, QString, int, int)",
                           screen, cname, int(cidx), 1)
        print(f"  실시간 등록: [{screen}] {cname}")
        count += 1

    if count > 0:
        send_telegram(
            f"<b>단타 조건검색 실시간 등록 완료</b>\n"
            f"{'  '.join(M2_CONDITIONS)}\n"
            f"편입 즉시 알림 + 황사장 자동매매 시작!"
        )

def on_receive_m2_initial(screen, code_list, condition_name, idx, prev_next):
    """실시간 등록 시 현재 편입 종목 초기 수신 (알림 없이 캐시만)"""
    if condition_name not in M2_CONDITIONS:
        return
    codes = [c for c in code_list.split(';') if c]
    for code in codes:
        m2_realtime_cache[code] = {
            "condition": condition_name,
            "time":      datetime.now().strftime("%H:%M"),
            "notified":  False,
        }
    print(f"  [초기편입] {condition_name}: {len(codes)}개 캐시 등록")

def on_realtime_condition(code, condition_type, condition_name, index, next_cond):
    """실시간 편입(I) / 이탈(D) 콜백"""
    if condition_name not in M2_CONDITIONS:
        return

    now_str = datetime.now().strftime("%H:%M")

    if condition_type == "I":
        # 중복 방지
        if code in m2_realtime_cache and m2_realtime_cache[code].get("notified"):
            return
        m2_realtime_cache[code] = {
            "condition": condition_name,
            "time":      now_str,
            "notified":  False,
        }
        print(f"  [편입] {now_str} {code} / {condition_name}")
        m2_detail_queue.append((code, condition_name))
        if len(m2_detail_queue) == 1:
            QTimer.singleShot(200, lambda: fetch_m2_detail(0))

        # 전일고점돌파 전용 매매 로직 시작
        if condition_name == GDJUM_CONDITION:
            QTimer.singleShot(500, lambda: gdjum_on_enter(code, condition_name))

    elif condition_type == "D":
        name = m2_realtime_cache.get(code, {}).get("name", code)
        m2_realtime_cache.pop(code, None)
        print(f"  [이탈] {now_str} {name} / {condition_name}")
        send_telegram(
            f"<b>조건검색 이탈</b> ({now_str})\n"
            f"{condition_name}\n"
            f"• {name}"
        )

# 편입 종목 기본정보 조회
def fetch_m2_detail(idx):
    global m2_detail_idx
    m2_detail_idx = idx
    if idx >= len(m2_detail_queue):
        m2_detail_queue.clear()
        return
    code, cname = m2_detail_queue[idx]
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_m2_detail)
    kiwoom.OnReceiveTrData.connect(on_tr_m2_detail)
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "모듈2편입상세요청", "opt10001", 0, "0212")

def on_tr_m2_detail(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "모듈2편입상세요청":
        return
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_m2_detail)

    code, cname = m2_detail_queue[m2_detail_idx]
    name      = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "종목명").strip()
    price_str = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "현재가").strip()
    open_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "시가").strip()
    low_str   = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "저가").strip()
    rate_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "등락율").strip()
    try:
        price    = abs(int(price_str))
        open_p   = abs(int(open_str))
        low_p    = abs(int(low_str))
        rate     = float(rate_str)
        is_sijeo = (open_p == low_p)

        if code in m2_realtime_cache:
            m2_realtime_cache[code]["name"]     = name
            m2_realtime_cache[code]["notified"] = True

        now_str   = m2_realtime_cache.get(code, {}).get("time", "?")
        sijeo_tag = "  <b>[시=저]</b>" if is_sijeo else ""

        # 텔레그램 알림
        msg  = f"<b>단타 조건검색 편입!</b> ({now_str})\n"
        msg += f"검색식: {cname}\n"
        msg += "--------------------\n"
        msg += f"• <b>{name}</b>{sijeo_tag}\n"
        msg += f"  현재가 {price:,}원  등락 <b>{rate:+.2f}%</b>\n"
        send_telegram(msg)

        # 황사장 조건식이면 자동매매 진입
        if cname == AUTO_TRADE_CONDITION:
            QTimer.singleShot(300, lambda: try_enter(code, name, price))

    except Exception as e:
        print(f"  상세조회 오류: {e}")

    QTimer.singleShot(300, lambda: fetch_m2_detail(m2_detail_idx + 1))

# =============================================================
# 모듈2-1: 자동매매 - 진입
# =============================================================
def try_enter(code, name, price):
    """황사장 편입 종목 자동 진입 시도"""
    now = datetime.now()

    # 장 시간 체크
    if not is_m2_open():
        print(f"  [진입스킵] 장외: {name}")
        return

    # 이미 보유 중
    if code in positions:
        print(f"  [진입스킵] 이미 보유: {name}")
        return

    # 최대 포지션 체크
    if len(positions) >= MAX_POSITIONS:
        print(f"  [진입스킵] 최대포지션({MAX_POSITIONS}): {name}")
        send_telegram(
            f"<b>진입 대기</b>\n"
            f"• {name} - 최대 {MAX_POSITIONS}종목 초과\n"
            f"  기존 종목 청산 후 진입 예정"
        )
        return

    # 수량 계산
    qty = calc_qty(price)
    is_high_price = (price > HIGH_PRICE_LIMIT)
    entry_amount  = price * qty

    # 시장가 매수 주문
    screen = next_screen()
    order_no = send_order_market_buy(screen, code, qty)
    print(f"  [진입] {name} {qty}주 시장가 매수 (화면:{screen})")

    # 포지션 등록 (체결 전 예약)
    noon_entry = (now.time() >= NOON_CUTOFF)
    positions[code] = {
        "name":         name,
        "entry_price":  price,   # 체결가로 나중에 업데이트
        "qty":          qty,
        "total_qty":    qty,
        "entry_time":   now,
        "entry_amount": entry_amount,
        "high_price":   price,
        "stop_price":   price * (1 + STOP_LOSS_RATE),
        "trail_active": False,
        "add_bought":   False,
        "add_timer":    None,
        "condition":    AUTO_TRADE_CONDITION,
        "noon_entry":   noon_entry,
        "is_high_price": is_high_price,
    }

    # 실시간 체결가 구독
    subscribe_realtime(code)

    # 20분 후 2차 매수 타이머
    add_timer = QTimer()
    add_timer.setSingleShot(True)
    add_timer.timeout.connect(lambda: check_add_buy(code))
    add_timer.start(ADD_BUY_MINUTES * 60 * 1000)
    positions[code]["add_timer"] = add_timer

    # 시간 기반 청산 타이머
    setup_exit_timer(code, noon_entry)

    # 진입 알림
    hold_min = 60 if noon_entry else HOLD_MINUTES
    send_telegram(
        f"<b>자동매매 진입!</b>\n"
        f"• {name}  {qty}주  시장가\n"
        f"  진입금액: {entry_amount:,}원\n"
        f"  손절: {price*(1+STOP_LOSS_RATE):,.0f}원 (-2.5%)\n"
        f"  {'정오이후: 1시간내 청산' if noon_entry else f'{hold_min}분 후 청산'}"
    )

def send_order_market_buy(screen, code, qty):
    """시장가 매수 주문"""
    result = kiwoom.dynamicCall(
        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
        ["시장가매수", screen, ACCOUNT_NUM,
         1,      # 1=신규매수
         code, qty,
         0,      # 가격 (시장가=0)
         "03",   # 03=시장가
         ""]
    )
    print(f"    매수주문 결과: {result}")
    return result

def send_order_market_sell(screen, code, qty, reason=""):
    """시장가 매도 주문"""
    if code not in positions:
        return
    name = positions[code]["name"]
    result = kiwoom.dynamicCall(
        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
        ["시장가매도", screen, ACCOUNT_NUM,
         2,      # 2=신규매도
         code, qty,
         0,
         "03",   # 시장가
         ""]
    )
    print(f"    매도주문: {name} {qty}주 [{reason}] 결과: {result}")
    return result

# =============================================================
# 모듈2-1: 2차 매수 (20분 후 -2% 체크)
# =============================================================
def check_add_buy(code):
    """20분 후 2차 추가매수 체크"""
    if code not in positions:
        return
    pos = positions[code]

    if pos["add_bought"]:
        return

    entry_price  = pos["entry_price"]
    current_price = get_current_price(code)

    if current_price <= 0:
        print(f"  [2차매수] 현재가 조회 실패: {pos['name']}")
        return

    rate = (current_price - entry_price) / entry_price

    # -2% 이하일 때 추가매수
    if rate <= ADD_BUY_RATE:
        is_high = pos["is_high_price"]
        add_qty  = 1 if is_high else max(1, ADD_AMOUNT // current_price)
        screen   = next_screen()
        send_order_market_buy(screen, code, add_qty)

        pos["add_bought"]   = True
        pos["total_qty"]   += add_qty
        pos["entry_amount"] += current_price * add_qty

        # 평균단가 재계산
        pos["entry_price"] = pos["entry_amount"] // pos["total_qty"]
        pos["stop_price"]  = pos["entry_price"] * (1 + STOP_LOSS_RATE)

        send_telegram(
            f"<b>2차 추가매수!</b>\n"
            f"• {pos['name']}  {add_qty}주  시장가\n"
            f"  현재가 {current_price:,}원  ({rate:+.2f}%)\n"
            f"  평균단가: {pos['entry_price']:,}원\n"
            f"  총 {pos['total_qty']}주 / {pos['entry_amount']:,}원"
        )
    else:
        print(f"  [2차매수 스킵] {pos['name']} {rate:+.2f}% (조건 미충족)")

def get_current_price(code):
    """현재가 동기 조회 (opt10001)"""
    try:
        kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                           "현재가조회", "opt10001", 0, "0299")
        # 실제로는 콜백 방식이므로, 캐시된 실시간 데이터 우선 활용
        # 여기선 realtime_cache에서 가져오는 방식으로 대체
        cached = m2_realtime_cache.get(code, {})
        return cached.get("price", 0)
    except:
        return 0

# =============================================================
# 모듈2-1: 실시간 체결가 구독 + 트레일링 스탑
# =============================================================
def subscribe_realtime(code):
    """실시간 체결 데이터 구독"""
    if code in realtime_subscribed:
        return
    screen = next_screen()
    kiwoom.dynamicCall(
        "SetRealReg(QString, QString, QString, QString)",
        screen, code, "10;13",   # 10=현재가, 13=누적거래량
        "1"   # 1=추가등록
    )
    realtime_subscribed.add(code)
    print(f"    실시간 구독: {code} (화면:{screen})")

def unsubscribe_realtime(code):
    """실시간 구독 해제"""
    if code not in realtime_subscribed:
        return
    kiwoom.dynamicCall("SetRealRemove(QString, QString)", "ALL", code)
    realtime_subscribed.discard(code)

# 실시간 데이터 수신
kiwoom_realtime_cache = {}   # {코드: 현재가}

def on_realtime_data(code, real_type, real_data):
    """실시간 데이터 수신 - 모듈2-1 트레일링/손절 + 모듈4 찰나 분기"""

    # 모듈4: 호가잔량 / 체결 -> 찰나의 매매 조건 체크
    if real_type in ("주식호가잔량", "주식체결"):
        on_realtime_m4(code, real_type, real_data)

    # 전일고점돌파 전용 호가/체결 처리
    if real_type in ("주식호가잔량", "주식체결"):
        gdjum_on_realtime(code, real_type)

    # 모듈2-1: 보유 포지션 실시간 체결가 처리
    if real_type != "주식체결":
        return
    if code not in positions:
        return

    price_str = kiwoom.dynamicCall("GetCommRealData(QString, int)", real_type, 10)
    try:
        price = abs(int(price_str.strip()))
    except:
        return

    kiwoom_realtime_cache[code] = price
    # 캐시 업데이트
    if code in m2_realtime_cache:
        m2_realtime_cache[code]["price"] = price

    pos          = positions[code]
    entry_price  = pos["entry_price"]
    high_price   = pos["high_price"]
    stop_price   = pos["stop_price"]
    trail_active = pos["trail_active"]

    # 고점 갱신
    if price > high_price:
        pos["high_price"] = price
        high_price = price

    # 현재 수익률 (진입가 대비)
    rate = (price - entry_price) / entry_price

    # 트레일링 스탑 활성화 (3% 이상 수익 시)
    if not trail_active and rate >= TRAIL_ACTIVATE:
        pos["trail_active"] = True
        pos["stop_price"]   = entry_price   # 스탑로스를 진입가로 이동
        trail_active = True
        print(f"  [트레일링ON] {pos['name']} +{rate:.1%} -> 스탑:{entry_price:,}")
        send_telegram(
            f"<b>트레일링 스탑 활성화!</b>\n"
            f"• {pos['name']}\n"
            f"  현재가: {price:,}원  ({rate:+.2%})\n"
            f"  스탑로스 -> 진입가 {entry_price:,}원으로 이동\n"
            f"  (이제 원금 보장 구간)"
        )

    # 트레일링 스탑 비율 동적 계산
    if trail_active:
        trail_rate = -0.03   # 기본 -3%
        for threshold, t_rate in TRAIL_STOP:
            if rate >= threshold:
                trail_rate = t_rate
                break
        new_stop = high_price * (1 + trail_rate)
        if new_stop > pos["stop_price"]:
            pos["stop_price"] = new_stop

    # 손절 / 트레일링 스탑 발동
    if price <= pos["stop_price"]:
        reason = "트레일링스탑" if trail_active else "손절"
        exit_position(code, reason)

def exit_position(code, reason="청산"):
    """포지션 청산"""
    if code not in positions:
        return
    pos    = positions[code]
    qty    = pos["total_qty"]
    screen = next_screen()

    send_order_market_sell(screen, code, qty, reason)

    entry_price = pos["entry_price"]
    cur_price   = kiwoom_realtime_cache.get(code, entry_price)
    pnl_rate    = (cur_price - entry_price) / entry_price
    pnl_amount  = (cur_price - entry_price) * qty
    elapsed     = int((datetime.now() - pos["entry_time"]).total_seconds() / 60)

    send_telegram(
        f"<b>자동매매 청산 [{reason}]</b>\n"
        f"• {pos['name']}\n"
        f"  매수단가: {entry_price:,}원\n"
        f"  매도단가: {cur_price:,}원\n"
        f"  수익률: <b>{pnl_rate:+.2f}%</b>\n"
        f"  수익금액: {pnl_amount:+,.0f}원\n"
        f"  경과시간: {elapsed}분"
    )

    # 정리
    if pos.get("add_timer"):
        pos["add_timer"].stop()
    unsubscribe_realtime(code)
    positions.pop(code, None)

# =============================================================
# 모듈2-1: 시간 기반 청산 타이머
# =============================================================
def setup_exit_timer(code, noon_entry):
    """진입 후 시간 기반 청산 타이머 설정"""
    if noon_entry:
        # 정오 이후 진입 -> 1시간 내 청산
        ms = 60 * 60 * 1000
        reason = "1시간경과(정오후)"
    else:
        # 일반 -> 1시간 30분 후 청산
        ms = HOLD_MINUTES * 60 * 1000
        reason = "1시간30분경과"

    t = QTimer()
    t.setSingleShot(True)
    t.timeout.connect(lambda: exit_position(code, reason))
    t.start(ms)

    if code in positions:
        positions[code]["exit_timer"] = t

def force_exit_all():
    """14:50 일괄 시장가 청산"""
    if not positions:
        return
    print(f"\n[14:50] 일괄 청산 시작 ({len(positions)}종목)")
    send_telegram(f"<b>14:50 일괄청산 시작</b> ({len(positions)}종목)")
    for code in list(positions.keys()):
        exit_position(code, "14:50일괄청산")
        QTimer.singleShot(500, lambda: None)   # 주문 간 딜레이

# =============================================================
# 체결 이벤트 (OnReceiveChejanData)
# =============================================================
def on_chejan(gubun, item_cnt, fid_list):
    """
    gubun: "0"=주문체결, "1"=잔고
    체결 시 포지션의 entry_price를 실제 체결가로 업데이트
    """
    if gubun != "0":
        return

    code      = kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().lstrip('A')
    order_type= kiwoom.dynamicCall("GetChejanData(int)", 905).strip()   # 매수/매도
    exec_price= kiwoom.dynamicCall("GetChejanData(int)", 910).strip()   # 체결가
    exec_qty  = kiwoom.dynamicCall("GetChejanData(int)", 911).strip()   # 체결수량
    name      = kiwoom.dynamicCall("GetChejanData(int)", 302).strip()

    try:
        ep  = int(exec_price)
        eq  = int(exec_qty)
        if ep <= 0 or eq <= 0:
            return
    except:
        return

    if code in positions and "매수" in order_type:
        pos = positions[code]
        # 실제 체결가로 진입가 업데이트
        pos["entry_price"] = ep
        pos["stop_price"]  = ep * (1 + STOP_LOSS_RATE)
        pos["high_price"]  = ep
        print(f"  [체결확인] {name} 매수 {eq}주 @ {ep:,}원")

# =============================================================
# 모듈1: 주도섹터 + 52주신고가 (15분 주기)
# =============================================================
def start_m1_scan():
    global upjong_data, upjong_idx, upjong_stock_queue
    global upjong_stock_idx, upjong_stock_rate, upjong_stock_results
    global theme_stock_map, stock_name_map, stock_rate_map
    global theme_scan_queue, theme_scan_idx
    global shingoga_codes, shingoga_detail, shingoga_idx, top7_upjong_codes

    upjong_data          = {}
    upjong_stock_queue   = []
    upjong_stock_rate    = {}
    upjong_stock_results = {}
    theme_stock_map      = {}
    stock_name_map       = {}
    stock_rate_map       = {}
    theme_scan_queue     = []
    shingoga_codes       = []
    shingoga_detail      = {}
    shingoga_idx         = 0
    top7_upjong_codes    = []

    print("\n[M1-PHASE1] 업종 스캔...")
    safe_disconnect(kiwoom.OnReceiveTrData)
    kiwoom.OnReceiveTrData.connect(on_tr_upjong)
    QTimer.singleShot(500, lambda: scan_upjong(0))

def scan_upjong(idx):
    global upjong_idx
    upjong_idx = idx
    if idx >= len(UPJONG_CODES):
        m1_phase1_done()
        return
    code = UPJONG_CODES[idx]
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "업종코드", code)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "업종현재가요청", "opt20001", 0, "0101")

def on_tr_upjong(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "업종현재가요청":
        return
    code = UPJONG_CODES[upjong_idx]
    rate = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                               trcode, rqname, 0, "등락률").strip()
    try:
        upjong_data[code] = {"name": UPJONG_MAP[code], "rate": float(rate)}
        print(f"  [{upjong_idx+1}/{len(UPJONG_CODES)}] "
              f"{UPJONG_MAP[code]:12s} {float(rate):+.2f}%")
    except:
        pass
    QTimer.singleShot(300, lambda: scan_upjong(upjong_idx + 1))

def m1_phase1_done():
    global top7_upjong_codes, upjong_stock_queue
    sorted_u = sorted(upjong_data.items(),
                      key=lambda x: x[1]['rate'], reverse=True)
    top7_upjong_codes = [c for c, _ in sorted_u[:7]]
    print(f"\n[M1-PHASE1 완료] TOP7: "
          f"{[upjong_data[c]['name'] for c in top7_upjong_codes]}")
    for uc in top7_upjong_codes:
        for sc in SECTOR_MAP.get(uc, []):
            upjong_stock_queue.append((uc, sc))
    print(f"[M1-PHASE2] 업종 종목 스캔 ({len(upjong_stock_queue)}개)...")
    safe_disconnect(kiwoom.OnReceiveTrData)
    kiwoom.OnReceiveTrData.connect(on_tr_upjong_stock)
    QTimer.singleShot(300, lambda: scan_upjong_stock(0))

def scan_upjong_stock(idx):
    global upjong_stock_idx
    upjong_stock_idx = idx
    if idx >= len(upjong_stock_queue):
        m1_phase2_done()
        return
    uc, sc = upjong_stock_queue[idx]
    if sc in upjong_stock_rate:
        QTimer.singleShot(0, lambda: scan_upjong_stock(idx + 1))
        return
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", sc)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "주식기본정보요청", "opt10001", 0, "0103")

def on_tr_upjong_stock(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "주식기본정보요청":
        return
    uc, sc    = upjong_stock_queue[upjong_stock_idx]
    name      = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "종목명").strip()
    rate_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "등락율").strip()
    price_str = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "현재가").strip()
    try:
        rate  = float(rate_str)
        price = abs(int(price_str))
        upjong_stock_rate[sc] = {"rate": rate, "price": price, "name": name}
        if rate >= 3.0:
            upjong_stock_results.setdefault(uc, []).append(
                {"name": name, "rate": rate, "price": price})
    except:
        pass
    QTimer.singleShot(200, lambda: scan_upjong_stock(upjong_stock_idx + 1))

def m1_phase2_done():
    print("[M1-PHASE2 완료]")
    safe_disconnect(kiwoom.OnReceiveTrData)
    kiwoom.OnReceiveTrData.connect(on_tr_theme)
    QTimer.singleShot(300, build_theme_map)

def build_theme_map():
    global theme_scan_queue
    result = kiwoom.dynamicCall("GetThemeGroupList(int)", 0)
    themes = [t for t in result.split(';') if t]
    for theme in themes:
        parts = theme.split('|')
        if len(parts) < 2:
            continue
        code, name = parts[0], parts[1]
        stocks_raw = kiwoom.dynamicCall("GetThemeGroupCode(QString)", code)
        codes = [s.replace('A', '') for s in stocks_raw.split(';') if s]
        for sc in codes:
            if sc not in stock_name_map:
                stock_name_map[sc] = kiwoom.dynamicCall(
                    "GetMasterCodeName(QString)", sc)
            theme_scan_queue.append((name, sc))
        theme_stock_map[name] = codes
    print(f"테마 스캔 대상: {len(theme_scan_queue)}개")
    QTimer.singleShot(300, lambda: scan_theme_stock(0))

def scan_theme_stock(idx):
    global theme_scan_idx
    theme_scan_idx = idx
    if idx >= len(theme_scan_queue):
        m1_phase3_done()
        return
    _, sc = theme_scan_queue[idx]
    if sc in stock_rate_map:
        QTimer.singleShot(0, lambda: scan_theme_stock(idx + 1))
        return
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", sc)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "테마주식정보요청", "opt10001", 0, "0104")

def on_tr_theme(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "테마주식정보요청":
        return
    _, sc     = theme_scan_queue[theme_scan_idx]
    rate_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "등락율").strip()
    price_str = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "현재가").strip()
    try:
        stock_rate_map[sc] = {
            "rate": float(rate_str), "price": abs(int(price_str))}
    except:
        stock_rate_map[sc] = {"rate": 0.0, "price": 0}
    if theme_scan_idx % 100 == 0:
        print(f"  테마 진행중... {theme_scan_idx}/{len(theme_scan_queue)}")
    QTimer.singleShot(200, lambda: scan_theme_stock(theme_scan_idx + 1))

def m1_phase3_done():
    print("[M1-PHASE3 완료]")
    safe_disconnect(kiwoom.OnReceiveTrData)
    safe_disconnect(kiwoom.OnReceiveTrCondition, on_receive_shingoga_condition)
    kiwoom.OnReceiveTrCondition.connect(on_receive_shingoga_condition)
    QTimer.singleShot(300, run_shingoga_condition)

def run_shingoga_condition():
    if "52주신고가" not in condition_list:
        print("52주신고가 조건식 없음")
        m1_phase4_done([])
        return
    idx = condition_list["52주신고가"]
    kiwoom.dynamicCall("SendCondition(QString, QString, int, int)",
                       "0201", "52주신고가", int(idx), 0)
    print("  52주신고가 조건검색 요청")

def on_receive_shingoga_condition(screen, code_list, condition_name, idx, prev_next):
    if condition_name != "52주신고가":
        return
    codes = [c for c in code_list.split(';') if c]
    print(f"[M1-PHASE4 완료] 52주신고가 {len(codes)}개")
    safe_disconnect(kiwoom.OnReceiveTrCondition, on_receive_shingoga_condition)
    safe_disconnect(kiwoom.OnReceiveTrCondition, on_receive_m2_initial)
    kiwoom.OnReceiveTrCondition.connect(on_receive_m2_initial)
    m1_phase4_done(codes)

def m1_phase4_done(codes):
    global shingoga_codes
    shingoga_codes = codes
    print("[M1-PHASE5] 52주신고가 상세 조회...")
    safe_disconnect(kiwoom.OnReceiveTrData)
    kiwoom.OnReceiveTrData.connect(on_tr_shingoga)
    QTimer.singleShot(300, lambda: scan_shingoga(0))

def scan_shingoga(idx):
    global shingoga_idx
    shingoga_idx = idx
    if idx >= len(shingoga_codes):
        m1_phase5_done()
        return
    code = shingoga_codes[idx]
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "신고가상세요청", "opt10001", 0, "0105")

def on_tr_shingoga(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "신고가상세요청":
        return
    code      = shingoga_codes[shingoga_idx]
    name      = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "종목명").strip()
    price_str = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "현재가").strip()
    open_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "시가").strip()
    low_str   = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "저가").strip()
    rate_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "등락율").strip()
    try:
        price  = abs(int(price_str))
        open_p = abs(int(open_str))
        low_p  = abs(int(low_str))
        shingoga_detail[code] = {
            "name":        name,
            "price":       price,
            "open":        open_p,
            "low":         low_p,
            "rate":        float(rate_str),
            "is_yangbong": price > open_p,
            "is_sijeo":    open_p == low_p,
        }
    except:
        pass
    QTimer.singleShot(200, lambda: scan_shingoga(shingoga_idx + 1))

def m1_phase5_done():
    safe_disconnect(kiwoom.OnReceiveTrData)
    print("[M1-PHASE5 완료] 브리핑 생성...")
    m1_build_and_send()

def m1_build_and_send():
    now = datetime.now().strftime("%m/%d %H:%M")
    sorted_u = sorted(upjong_data.items(),
                      key=lambda x: x[1]['rate'], reverse=True)
    top7_u = [c for c, _ in sorted_u[:7]]

    theme_perf = []
    for tname, codes in theme_stock_map.items():
        valid = [
            {"name": stock_name_map.get(sc, sc),
             "rate": stock_rate_map[sc]["rate"],
             "price": stock_rate_map[sc]["price"]}
            for sc in codes
            if sc in stock_rate_map and stock_rate_map[sc]["rate"] >= 3.0
        ]
        if valid:
            avg = sum(s["rate"] for s in valid) / len(valid)
            theme_perf.append({
                "theme": tname, "avg": avg,
                "stocks": sorted(valid, key=lambda x: x["rate"], reverse=True)
            })
    top7_t = sorted(theme_perf, key=lambda x: x["avg"], reverse=True)[:7]

    sector_stock_set = set()
    for uc in top7_upjong_codes:
        sector_stock_set.update(SECTOR_MAP.get(uc, []))

    filtered = []
    for code, d in shingoga_detail.items():
        if d["is_yangbong"] or d["is_sijeo"]:
            filtered.append({
                "code":      code,
                "name":      d["name"],
                "price":     d["price"],
                "rate":      d["rate"],
                "is_sijeo":  d["is_sijeo"],
                "in_sector": code in sector_stock_set,
            })
    filtered = sorted(filtered, key=lambda x: x["rate"], reverse=True)[:15]

    msg1  = f"<b>업종 주도섹터 TOP7</b> ({now})\n"
    msg1 += "KOSPI 업종 기준 | 3%^ 종목\n--------------------\n\n"
    for i, uc in enumerate(top7_u, 1):
        d = upjong_data[uc]
        stocks = sorted(upjong_stock_results.get(uc, []),
                        key=lambda x: x['rate'], reverse=True)
        e = "1위" if i==1 else "2위" if i==2 else "3위" if i==3 else "-"
        msg1 += f"{e} <b>{d['name']}</b> ({d['rate']:+.2f}%)\n"
        for s in (stocks[:5] if stocks else []):
            msg1 += f"  • {s['name']}  <b>{s['rate']:+.2f}%</b>  {s['price']:,}원\n"
        if not stocks:
            msg1 += "  • (3%^ 종목 없음)\n"
        msg1 += "\n"

    msg2  = f"<b>테마 주도섹터 TOP7</b> ({now})\n"
    msg2 += "KOSPI+KOSDAQ 테마 기준 | 3%^ 종목\n--------------------\n\n"
    for i, t in enumerate(top7_t, 1):
        e = "1위" if i==1 else "2위" if i==2 else "3위" if i==3 else "-"
        msg2 += f"{e} <b>{t['theme']}</b> (평균 {t['avg']:+.2f}%)\n"
        for s in t["stocks"][:5]:
            msg2 += f"  • {s['name']}  <b>{s['rate']:+.2f}%</b>  {s['price']:,}원\n"
        msg2 += "\n"

    msg3  = f"<b>52주 신고가</b> ({now})\n"
    msg3 += "양봉 기준 | [불]=주도섹터 | 시=저 표시\n--------------------\n\n"
    for s in filtered:
        fire  = "[불]" if s["in_sector"] else "   "
        sijeo = " <b>[시=저]</b>" if s["is_sijeo"] else ""
        msg3 += f"{fire} {s['name']}  <b>{s['rate']:+.2f}%</b>  {s['price']:,}원{sijeo}\n"
    if not filtered:
        msg3 += "해당 종목 없음\n"

    print("\n모듈1 텔레그램 전송...")
    send_telegram(msg1)
    QTimer.singleShot(1500, lambda: send_telegram(msg2))
    QTimer.singleShot(3000, lambda: send_telegram(msg3))
    print(f"모듈1 브리핑 완료! [{now}]")

# =============================================================
# 이벤트 연결 + 실행
# =============================================================
kiwoom.OnEventConnect.connect(on_login)
kiwoom.OnReceiveConditionVer.connect(on_condition_load)
kiwoom.OnReceiveRealData.connect(on_realtime_data)
kiwoom.dynamicCall("CommConnect()")

app.exec_()


# =============================================================
# 모듈3: 종가베팅 전략
# =============================================================
# 스캔 시각: 15:18 (1회성 타이머)
# 조건식: 급등후조정반등 -> 등락률 5% 미만 + 양봉 + 윗꼬리 필터
# 기관 순매수 10거래일 중 5일 이상 조건 (opt10059)
# 최대 5종목, 25만원 종가 진입

M3_SCAN_HOUR   = 15
M3_SCAN_MINUTE = 18
M3_MAX_STOCKS  = 5
M3_CONDITION   = "급등후조정반등"
M3_RATE_LIMIT  = 5.0       # 등락률 5% 미만
M3_TAIL_MULT   = 2.5       # 윗꼬리 / 몸통 배율 한도
M3_INST_DAYS   = 10        # 기관 체크 기간 (거래일)
M3_INST_MIN    = 5         # 기관 순매수 최소 일수

# 모듈3 전역 상태
m3_candidate_codes = []    # 조건식 결과
m3_candidate_idx   = 0
m3_candidate_data  = {}    # {코드: {name, price, open, high, rate, pass_filter}}
m3_inst_check_queue= []    # 기관 체크 대기 리스트
m3_inst_idx        = 0
m3_inst_days_data  = {}    # {코드: 기관순매수일수}
m3_final_list      = []    # 최종 종가베팅 대상

m3_timer = QTimer()        # 15:18 타이머 (전역 - GC 방지)

def setup_m3_timer():
    """15:18 종가베팅 타이머 설정"""
    now    = datetime.now()
    target = now.replace(hour=M3_SCAN_HOUR, minute=M3_SCAN_MINUTE,
                         second=0, microsecond=0)
    if now >= target:
        print("15:18 이미 지남 - 모듈3 타이머 스킵")
        return
    ms = int((target - now).total_seconds() * 1000)
    m3_timer.setSingleShot(True)
    m3_timer.timeout.connect(start_m3_scan)
    m3_timer.start(ms)
    print(f"모듈3 15:18 타이머 설정 ({ms//1000}초 후)")

def start_m3_scan():
    """15:18 - 모듈3 스캔 시작"""
    global m3_candidate_codes, m3_candidate_idx
    global m3_candidate_data, m3_inst_check_queue
    global m3_inst_idx, m3_inst_days_data, m3_final_list

    now = datetime.now()
    if now.weekday() >= 5:
        print("모듈3: 주말 스킵")
        return

    m3_candidate_codes  = []
    m3_candidate_idx    = 0
    m3_candidate_data   = {}
    m3_inst_check_queue = []
    m3_inst_idx         = 0
    m3_inst_days_data   = {}
    m3_final_list       = []

    print(f"\n[모듈3] 15:18 종가베팅 스캔 시작...")
    send_telegram("<b>모듈3 종가베팅 스캔 시작</b> (15:18)")

    # 조건식 조회 (1회성)
    if M3_CONDITION not in condition_list:
        print(f"  조건식 없음: {M3_CONDITION}")
        send_telegram(f"모듈3: '{M3_CONDITION}' 조건식 없음")
        return

    safe_disconnect(kiwoom.OnReceiveTrCondition, on_receive_m3_condition)
    kiwoom.OnReceiveTrCondition.connect(on_receive_m3_condition)
    cidx = condition_list[M3_CONDITION]
    kiwoom.dynamicCall("SendCondition(QString, QString, int, int)",
                       "0301", M3_CONDITION, int(cidx), 0)
    print(f"  조건식 '{M3_CONDITION}' 요청")

def on_receive_m3_condition(screen, code_list, condition_name, idx, prev_next):
    if condition_name != M3_CONDITION:
        return
    safe_disconnect(kiwoom.OnReceiveTrCondition, on_receive_m3_condition)
    # 모듈2 초기수신 콜백 재연결
    safe_disconnect(kiwoom.OnReceiveTrCondition, on_receive_m2_initial)
    kiwoom.OnReceiveTrCondition.connect(on_receive_m2_initial)

    global m3_candidate_codes
    m3_candidate_codes = [c for c in code_list.split(';') if c]
    print(f"  조건식 결과: {len(m3_candidate_codes)}개")

    if not m3_candidate_codes:
        send_telegram("모듈3: 조건검색 결과 없음")
        return

    # STEP1: 기본정보 조회 (등락률/양봉/윗꼬리 필터)
    print("[모듈3-STEP1] 기본정보 조회...")
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_m3_basic)
    kiwoom.OnReceiveTrData.connect(on_tr_m3_basic)
    QTimer.singleShot(300, lambda: scan_m3_basic(0))

# ── STEP1: 기본정보 + 1차 필터 ──────────────────
def scan_m3_basic(idx):
    global m3_candidate_idx
    m3_candidate_idx = idx
    if idx >= len(m3_candidate_codes):
        m3_step1_done()
        return
    code = m3_candidate_codes[idx]
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "모듈3기본정보", "opt10001", 0, "0302")

def on_tr_m3_basic(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "모듈3기본정보":
        return
    code      = m3_candidate_codes[m3_candidate_idx]
    name      = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "종목명").strip()
    price_str = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "현재가").strip()
    open_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "시가").strip()
    high_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "고가").strip()
    rate_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "등락율").strip()
    try:
        price  = abs(int(price_str))
        open_p = abs(int(open_str))
        high_p = abs(int(high_str))
        rate   = float(rate_str)

        # ── 1차 필터 ──
        # 조건1: 등락률 5% 미만
        if rate >= M3_RATE_LIMIT:
            print(f"  [제외] {name} 등락률 {rate:.2f}% >= 5%")
            QTimer.singleShot(200, lambda: scan_m3_basic(m3_candidate_idx + 1))
            return

        # 조건2: 양봉 (현재가 > 시가)
        if price <= open_p:
            print(f"  [제외] {name} 음봉")
            QTimer.singleShot(200, lambda: scan_m3_basic(m3_candidate_idx + 1))
            return

        # 조건3: 윗꼬리 < 몸통 × 2.5
        body   = price - open_p          # 몸통
        tail   = high_p - price          # 윗꼬리
        if body > 0 and tail >= body * M3_TAIL_MULT:
            print(f"  [제외] {name} 윗꼬리({tail}) >= 몸통({body})x2.5")
            QTimer.singleShot(200, lambda: scan_m3_basic(m3_candidate_idx + 1))
            return

        # 통과
        m3_candidate_data[code] = {
            "name":  name,
            "price": price,
            "open":  open_p,
            "high":  high_p,
            "rate":  rate,
        }
        print(f"  [통과] {name}  {rate:+.2f}%  {price:,}원")

    except Exception as e:
        print(f"  [오류] {code}: {e}")

    QTimer.singleShot(200, lambda: scan_m3_basic(m3_candidate_idx + 1))

def m3_step1_done():
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_m3_basic)
    passed = list(m3_candidate_data.keys())
    print(f"\n[모듈3-STEP1 완료] 1차 필터 통과: {len(passed)}개")

    if not passed:
        send_telegram("모듈3: 1차 필터 통과 종목 없음")
        return

    # STEP2: 기관 순매수 일수 체크 (opt10059)
    global m3_inst_check_queue
    m3_inst_check_queue = passed
    print("[모듈3-STEP2] 기관 순매수 체크...")
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_m3_institution)
    kiwoom.OnReceiveTrData.connect(on_tr_m3_institution)
    QTimer.singleShot(300, lambda: scan_m3_institution(0))

# ── STEP2: 기관 순매수 일수 조회 (opt10059) ─────
def scan_m3_institution(idx):
    global m3_inst_idx
    m3_inst_idx = idx
    if idx >= len(m3_inst_check_queue):
        m3_step2_done()
        return
    code = m3_inst_check_queue[idx]
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "금액수량구분", "1")  # 1=수량
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "매매구분", "0")      # 0=순매수
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "시작일자", "")
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "모듈3기관조회", "opt10059", 0, "0303")

def on_tr_m3_institution(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "모듈3기관조회":
        return
    code = m3_inst_check_queue[m3_inst_idx]
    name = m3_candidate_data[code]["name"]

    # 최근 10거래일 기관계 순매수 일수 카운트
    inst_buy_days = 0
    for i in range(M3_INST_DAYS):
        try:
            inst_str = kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, i, "기관계").strip()
            inst_val = int(inst_str.replace(',', '').replace('+', ''))
            if inst_val > 0:
                inst_buy_days += 1
        except:
            break

    m3_inst_days_data[code] = inst_buy_days
    result = "통과" if inst_buy_days >= M3_INST_MIN else "제외"
    print(f"  [{result}] {name} 기관순매수 {inst_buy_days}일/{M3_INST_DAYS}거래일")
    QTimer.singleShot(300, lambda: scan_m3_institution(m3_inst_idx + 1))

def m3_step2_done():
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_m3_institution)
    global m3_final_list

    # 기관 조건 통과 종목
    passed = [
        code for code in m3_inst_check_queue
        if m3_inst_days_data.get(code, 0) >= M3_INST_MIN
    ]
    print(f"\n[모듈3-STEP2 완료] 기관 조건 통과: {len(passed)}개")

    if not passed:
        send_telegram("모듈3: 기관 순매수 조건 통과 종목 없음")
        return

    # 등락률 낮은 순 정렬 후 최대 5종목 (안정적인 종목 우선)
    passed_sorted = sorted(
        passed,
        key=lambda c: m3_candidate_data[c]["rate"]
    )[:M3_MAX_STOCKS]

    m3_final_list = passed_sorted
    print(f"  최종 종가베팅 대상: {[m3_candidate_data[c]['name'] for c in m3_final_list]}")

    # 브리핑 + 종가 진입
    m3_enter_all()

# ── 종가 진입 ────────────────────────────────────
def m3_enter_all():
    """최종 선정 종목 일괄 종가 진입"""
    now = datetime.now().strftime("%m/%d %H:%M")
    msg = f"<b>종가베팅 진입</b> ({now})\n"
    msg += f"기관순매수 {M3_INST_MIN}일+ | 최대 {M3_MAX_STOCKS}종목\n"
    msg += "--------------------\n\n"

    for i, code in enumerate(m3_final_list):
        d   = m3_candidate_data[code]
        qty = calc_qty(d["price"])
        entry_amount = d["price"] * qty

        msg += (f"• <b>{d['name']}</b>\n"
                f"  {d['price']:,}원  등락 {d['rate']:+.2f}%"
                f"  {qty}주  {entry_amount:,}원\n"
                f"  기관순매수 {m3_inst_days_data[code]}일/{M3_INST_DAYS}거래일\n\n")

        # 주문 딜레이 (종목별 0.5초 간격)
        delay = i * 500
        code_snap = code   # 클로저 캡처용
        QTimer.singleShot(delay, lambda c=code_snap: m3_send_order(c))

    send_telegram(msg)

def m3_send_order(code):
    """종목별 시장가 매수 주문"""
    if code not in m3_candidate_data:
        return
    d    = m3_candidate_data[code]
    qty  = calc_qty(d["price"])
    name = d["name"]

    # 이미 보유 중이면 스킵
    if code in positions:
        print(f"  [종가베팅 스킵] 이미 보유: {name}")
        return

    screen = next_screen()
    send_order_market_buy(screen, code, qty)
    print(f"  [종가베팅 진입] {name} {qty}주 시장가")

    # 포지션 등록 (익일 황사장 매매법 적용 플래그)
    positions[code] = {
        "name":          name,
        "entry_price":   d["price"],
        "qty":           qty,
        "total_qty":     qty,
        "entry_time":    datetime.now(),
        "entry_amount":  d["price"] * qty,
        "high_price":    d["price"],
        "stop_price":    d["price"] * (1 + STOP_LOSS_RATE),
        "trail_active":  False,
        "add_bought":    False,
        "add_timer":     None,
        "exit_timer":    None,
        "condition":     "종가베팅",
        "noon_entry":    False,
        "is_high_price": d["price"] > HIGH_PRICE_LIMIT,
        "is_overnight":  True,    # 익일 매매법 적용 플래그
    }
    subscribe_realtime(code)
    print(f"    포지션 등록 완료: {name}")


# =============================================================
# 모듈4: 찰나의 매매
# =============================================================
# 당일 거래량 상위 100종목 실시간 호가 구독
# 조건: 매도호가잔량 >= 매수호가잔량 x2 + 프로그램순매수
#       + 체결강도>100% + 대량체결(주가별 동적) + 매도벽 순간 붕괴
# 조건 충족 시 텔레그램 즉시 브리핑

TOP_N           = 100      # 거래량 상위 N종목

# 대량체결 기준 (주가별 동적)
BULK_LOW        = 3000     # 5만원 이하
BULK_MID        = 1000     # 5만~10만원
BULK_HIGH       = 500      # 10만원 초과
PRICE_B1        = 50000
PRICE_B2        = 100000

# 조건 기준
SELL_BUY_RATIO  = 2.0      # 매도호가잔량 / 매수호가잔량 배율
CHEGYUL_MIN     = 100.0    # 체결강도 최소 (%)
WALL_BREAK_RATE = -0.10    # 매도벽 붕괴 기준 (-10%)
ALERT_COOLDOWN  = 300      # 동일 종목 재알림 방지 (초)
M4_MAX_POSITIONS = 3       # 찰나의 매매 최대 동시 보유 종목

# 모듈4 전역 상태
m4_top100_codes = []
m4_top100_names = {}
m4_stock_cache  = {}   # {코드: {ask_qty, bid_qty, ask_qty_prev,
                        #          chegyul, price, prog_buy, last_bulk}}
m4_alerted      = {}   # {코드: datetime} 쿨다운용
m4_top100_timer = QTimer()   # 30분 갱신 타이머 (전역 - GC 방지)

def get_bulk_threshold(price):
    if price <= PRICE_B1:
        return BULK_LOW
    elif price <= PRICE_B2:
        return BULK_MID
    else:
        return BULK_HIGH

# ── top100 조회 ──────────────────────────────────
def fetch_top100():
    if not is_scan_open_m4():
        print(f"[{datetime.now().strftime('%H:%M')}] 모듈4 장외 - top100 스킵")
        return
    print(f"\n[{datetime.now().strftime('%H:%M')}] 모듈4 거래량 상위100 조회...")
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_top100)
    kiwoom.OnReceiveTrData.connect(on_tr_top100)
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "시장구분", "000")
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "정렬구분", "2")
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "관리종목포함", "0")
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "신용구분", "0")
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "거래량상위요청", "opt10030", 0, "9001")

def is_scan_open_m4():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return time(9, 0) <= now.time() <= time(15, 20)

def on_tr_top100(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "거래량상위요청":
        return
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_top100)

    global m4_top100_codes
    new_codes = []

    for i in range(TOP_N):
        try:
            code = kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, i, "종목코드").strip().lstrip('A')
            name = kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, i, "종목명").strip()
            if not code:
                break
            new_codes.append(code)
            m4_top100_names[code] = name
            if code not in m4_stock_cache:
                m4_stock_cache[code] = {
                    "ask_qty":      0,
                    "bid_qty":      0,
                    "ask_qty_prev": 0,
                    "chegyul":      0.0,
                    "price":        0,
                    "prog_buy":     0,
                    "last_bulk":    0,
                }
        except:
            break

    m4_top100_codes = new_codes
    print(f"  top100 갱신: {len(m4_top100_codes)}개")
    QTimer.singleShot(500, subscribe_m4_realtime)

def subscribe_m4_realtime():
    """top100 실시간 호가/체결 구독"""
    if not m4_top100_codes:
        return
    # 기존 구독 해제 후 재등록
    kiwoom.dynamicCall("SetRealRemove(QString, QString)", "9100", "ALL")
    codes_str = ";".join(m4_top100_codes)
    # FID: 10=현재가, 15=체결량, 41~45=매도호가잔량, 61~65=매수호가잔량
    #      228=체결강도, 291=프로그램순매수수량
    fid_list = "10;15;41;42;43;44;45;61;62;63;64;65;228;291"
    kiwoom.dynamicCall(
        "SetRealReg(QString, QString, QString, QString)",
        "9100", codes_str, fid_list, "0"
    )
    print(f"  모듈4 실시간 구독 완료: {len(m4_top100_codes)}종목")

# ── 실시간 데이터 수신 ────────────────────────────
def on_realtime_m4(code, real_type, real_data):
    """모듈4 실시간 호가/체결 처리 (on_realtime_data에서 분기)"""
    if code not in m4_top100_codes:
        return
    if not is_scan_open_m4():
        return

    cache = m4_stock_cache.get(code)
    if not cache:
        return

    if real_type == "주식호가잔량":
        # 매도호가잔량 합계 (FID 41~45)
        ask_total = 0
        for fid in [41, 42, 43, 44, 45]:
            try:
                v = kiwoom.dynamicCall(
                    "GetCommRealData(QString, int)", real_type, fid)
                ask_total += abs(int(v.strip()))
            except:
                pass

        # 매수호가잔량 합계 (FID 61~65)
        bid_total = 0
        for fid in [61, 62, 63, 64, 65]:
            try:
                v = kiwoom.dynamicCall(
                    "GetCommRealData(QString, int)", real_type, fid)
                bid_total += abs(int(v.strip()))
            except:
                pass

        cache["ask_qty_prev"] = cache["ask_qty"]
        cache["ask_qty"]      = ask_total
        cache["bid_qty"]      = bid_total
        m4_check_conditions(code, cache)

    elif real_type == "주식체결":
        try:
            price = abs(int(kiwoom.dynamicCall(
                "GetCommRealData(QString, int)", real_type, 10).strip()))
            cache["price"] = price
        except:
            pass
        try:
            cache["chegyul"] = float(kiwoom.dynamicCall(
                "GetCommRealData(QString, int)", real_type, 228).strip())
        except:
            pass
        try:
            cache["last_bulk"] = abs(int(kiwoom.dynamicCall(
                "GetCommRealData(QString, int)", real_type, 15).strip()))
        except:
            pass
        try:
            cache["prog_buy"] = int(kiwoom.dynamicCall(
                "GetCommRealData(QString, int)", real_type, 291).strip())
        except:
            pass
        m4_check_conditions(code, cache)

def m4_check_conditions(code, cache):
    """찰나의 매매 조건 체크"""
    price    = cache["price"]
    ask_qty  = cache["ask_qty"]
    bid_qty  = cache["bid_qty"]
    ask_prev = cache["ask_qty_prev"]
    chegyul  = cache["chegyul"]
    prog_buy = cache["prog_buy"]
    bulk     = cache["last_bulk"]

    if price <= 0 or bid_qty <= 0:
        return

    # 조건1: 매도잔량 >= 매수잔량 x2
    if ask_qty < bid_qty * SELL_BUY_RATIO:
        return

    # 조건2: 프로그램 순매수
    if prog_buy <= 0:
        return

    # 조건3: 체결강도 > 100%
    if chegyul <= CHEGYUL_MIN:
        return

    # 조건4: 대량체결 (주가별 동적 기준)
    if bulk < get_bulk_threshold(price):
        return

    # 조건5: 매도벽 순간 붕괴 (-10% 이상 감소)
    if ask_prev <= 0:
        return
    wall_change = (ask_qty - ask_prev) / ask_prev
    if wall_change > WALL_BREAK_RATE:
        return

    # 모든 조건 통과 -> 알림
    m4_fire_alert(code, price, ask_qty, bid_qty,
                  chegyul, prog_buy, bulk, wall_change)

def m4_fire_alert(code, price, ask_qty, bid_qty,
                  chegyul, prog_buy, bulk, wall_change):
    now = datetime.now()

    # 쿨다운 체크
    last = m4_alerted.get(code)
    if last and (now - last).total_seconds() < ALERT_COOLDOWN:
        return

    m4_alerted[code] = now
    name    = m4_top100_names.get(code, code)
    now_str = now.strftime("%H:%M:%S")
    ratio   = ask_qty / bid_qty if bid_qty > 0 else 0

    # 찰나 포지션 수 (condition == "찰나의매매" 인 것만 카운트)
    m4_position_count = sum(
        1 for p in positions.values() if p.get("condition") == "찰나의매매"
    )

    # 자동매매 진입 가능 여부 판단
    can_enter = (
        code not in positions              # 미보유
        and m4_position_count < M4_MAX_POSITIONS  # 찰나 3개 미만
        and len(positions) < MAX_POSITIONS  # 전체 15개 미만
        and is_scan_open_m4()              # 장중
    )

    msg  = f"<b>찰나의 매매 포착!</b> ({now_str})\n"
    msg += "--------------------\n"
    msg += f"• <b>{name}</b>  {price:,}원\n\n"
    msg += f"  매도잔량: {ask_qty:,}주\n"
    msg += f"  매수잔량: {bid_qty:,}주\n"
    msg += f"  잔량비율: {ratio:.1f}배\n"
    msg += f"  체결강도: {chegyul:.1f}%\n"
    msg += f"  프로그램: +{prog_buy:,}주\n"
    msg += f"  대량체결: {bulk:,}주\n"
    msg += f"  매도벽:   {wall_change:+.1%} 붕괴!\n"

    if can_enter:
        qty          = calc_qty(price)
        entry_amount = price * qty
        msg += f"\n  -> 자동매매 진입! {qty}주  {entry_amount:,}원"
    elif code in positions:
        msg += "\n  -> 이미 보유 중 (진입 스킵)"
    elif m4_position_count >= M4_MAX_POSITIONS:
        msg += f"\n  -> 찰나 최대 {M4_MAX_POSITIONS}종목 초과 (진입 스킵)"
    elif len(positions) >= MAX_POSITIONS:
        msg += f"\n  -> 전체 최대 {MAX_POSITIONS}종목 초과 (진입 스킵)"

    send_telegram(msg)
    print(f"  [찰나포착] {now_str} {name} {price:,}원 "
          f"잔량비{ratio:.1f}배 체결강도{chegyul:.0f}% 대량{bulk:,}주")

    # 자동매매 진입 실행
    if can_enter:
        QTimer.singleShot(200, lambda: m4_enter(code, name, price))


def m4_enter(code, name, price):
    """찰나의 매매 자동 진입"""
    # 진입 직전 재확인 (알림~진입 사이 상태 변화 대비)
    m4_count = sum(1 for p in positions.values()
                   if p.get("condition") == "찰나의매매")
    if code in positions:
        return
    if m4_count >= M4_MAX_POSITIONS:
        return
    if len(positions) >= MAX_POSITIONS:
        return

    qty          = calc_qty(price)
    is_high      = price > HIGH_PRICE_LIMIT
    entry_amount = price * qty
    noon_entry   = datetime.now().time() >= NOON_CUTOFF
    screen       = next_screen()

    send_order_market_buy(screen, code, qty)
    print(f"  [찰나진입] {name} {qty}주 시장가")

    # 포지션 등록 (황사장과 동일 구조, condition만 구분)
    positions[code] = {
        "name":          name,
        "entry_price":   price,
        "qty":           qty,
        "total_qty":     qty,
        "entry_time":    datetime.now(),
        "entry_amount":  entry_amount,
        "high_price":    price,
        "stop_price":    price * (1 + STOP_LOSS_RATE),
        "trail_active":  False,
        "add_bought":    False,
        "add_timer":     None,
        "exit_timer":    None,
        "condition":     "찰나의매매",
        "noon_entry":    noon_entry,
        "is_high_price": is_high,
        "is_overnight":  False,
    }

    # 실시간 체결가 구독 (트레일링/손절용)
    subscribe_realtime(code)

    # 20분 후 2차 매수 타이머
    add_timer = QTimer()
    add_timer.setSingleShot(True)
    add_timer.timeout.connect(lambda: check_add_buy(code))
    add_timer.start(ADD_BUY_MINUTES * 60 * 1000)
    positions[code]["add_timer"] = add_timer

    # 시간 기반 청산 타이머
    setup_exit_timer(code, noon_entry)

    send_telegram(
        f"<b>찰나의 매매 자동진입!</b>\n"
        f"• {name}  {qty}주  시장가\n"
        f"  진입금액: {entry_amount:,}원\n"
        f"  손절: {price*(1+STOP_LOSS_RATE):,.0f}원 (-2.5%)\n"
        f"  {'정오이후: 1시간내 청산' if noon_entry else '90분 후 청산'}"
    )


# =============================================================
# 모듈2 전일고점돌파 전용 매매 로직
# =============================================================
# 전략 요약:
# 1. 조건검색 "단타검색식전일고점돌파" 편입 시
# 2. 전일고가 조회 → 틱 단위 계산
# 3. 실시간 호가 구독 → 전일고점 ±4~5호가 잔량×가격 >= 1억 체크
# 4. 5분봉 조회 → 돌파캔들 거래량 >= 60캔들 평균 × 2배
# 5. 8% 이상 상승 후 눌림 여부 체크
# 6. 전일고점 -4틱 지정가 매수 (25만원)
# 7. 20분 후 -2% 눌림 시 25만원 추가 (총 50만원)
# 8. 손절 -2.5% / 트레일링 / 시간 청산은 황사장과 동일

GDJUM_CONDITION = "단타검색식전일고점돌파"   # 조건식명
GDJUM_TICK_MIN  = 1e8   # 호가당 최소 1억원
GDJUM_VOL_MULT  = 2.0   # 거래량 배율 (60캔들 평균 × 2배)
GDJUM_CANDLE_N  = 60    # 평균 거래량 기준 캔들 수
GDJUM_TICK_DOWN = 4     # 진입가 = 전일고점 - 4틱
GDJUM_RISE_SKIP = 0.08  # 8% 이상 상승 후 눌림 시 진입 스킵

# 전일고점돌파 종목별 상태 관리
# {코드: {
#   prev_high: 전일고가,
#   tick_size: 틱단위,
#   entry_price: 진입예정가 (전일고점-4틱),
#   max_price: 편입 후 최고가 (8% 체크용),
#   hoga_ok: 호가 조건 통과 여부,
#   vol_ok: 거래량 조건 통과 여부,
#   order_sent: 주문 발송 여부,
#   order_no: 주문번호,
#   screen: 스크린번호,
# }}
gdjum_status = {}

# 5분봉 조회 큐
gdjum_vol_queue = []
gdjum_vol_idx   = 0

# =============================================================
# 틱 단위 계산
# =============================================================
def get_tick_size(price: int) -> int:
    """주가별 틱 단위 반환"""
    if price < 1000:
        return 1
    elif price < 5000:
        return 5
    elif price < 10000:
        return 10
    elif price < 50000:
        return 50
    elif price < 100000:
        return 100
    elif price < 500000:
        return 500
    else:
        return 1000

# =============================================================
# STEP1: 조건검색 편입 시 전일고가 조회
# =============================================================
def gdjum_on_enter(code: str, condition_name: str):
    """전일고점돌파 조건검색 편입 처리"""
    if condition_name != GDJUM_CONDITION:
        return
    if code in positions:
        print(f"  [전일고점] 이미 보유: {code}")
        return
    if len(positions) >= MAX_POSITIONS:
        print(f"  [전일고점] 최대포지션 초과")
        return

    print(f"  [전일고점] 편입: {code} → 전일고가 조회...")
    gdjum_status[code] = {
        "prev_high":   0,
        "tick_size":   0,
        "entry_price": 0,
        "max_price":   0,
        "hoga_ok":     False,
        "vol_ok":      False,
        "order_sent":  False,
        "order_no":    None,
        "screen":      next_screen(),
        "name":        "",
    }

    # 전일고가 조회 (opt10001)
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_gdjum_basic)
    kiwoom.OnReceiveTrData.connect(on_tr_gdjum_basic)
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "전일고점기본정보", "opt10001", 0, "0401")

def on_tr_gdjum_basic(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "전일고점기본정보":
        return
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_gdjum_basic)

    # 어떤 코드인지 gdjum_status에서 찾기
    code = None
    for c, s in gdjum_status.items():
        if s["prev_high"] == 0 and s["name"] == "":
            code = c
            break
    if not code:
        return

    name      = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "종목명").strip()
    price_str = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "현재가").strip()
    high_str  = kiwoom.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "전일고가").strip()

    try:
        price     = abs(int(price_str))
        prev_high = abs(int(high_str))
        tick      = get_tick_size(prev_high)
        entry_p   = prev_high - (tick * GDJUM_TICK_DOWN)

        gdjum_status[code]["name"]        = name
        gdjum_status[code]["prev_high"]   = prev_high
        gdjum_status[code]["tick_size"]   = tick
        gdjum_status[code]["entry_price"] = entry_p
        gdjum_status[code]["max_price"]   = price

        print(f"  [전일고점] {name} 전일고가:{prev_high:,} "
              f"틱:{tick} 진입예정:{entry_p:,}")

        # 실시간 호가 구독
        gdjum_subscribe_hoga(code)

        # 5분봉 거래량 조회
        gdjum_vol_queue.append(code)
        if len(gdjum_vol_queue) == 1:
            QTimer.singleShot(300, lambda: gdjum_fetch_vol(0))

        send_telegram(
            f"<b>[전일고점돌파] 편입 감지!</b>\n"
            f"• {name}\n"
            f"  전일고가: {prev_high:,}원\n"
            f"  진입예정가: {entry_p:,}원 (전일고점-{GDJUM_TICK_DOWN}틱)\n"
            f"  조건 확인 중..."
        )
    except Exception as e:
        print(f"  [전일고점] 기본정보 오류: {e}")
        gdjum_status.pop(code, None)

# =============================================================
# STEP2: 실시간 호가 구독 + 잔량 체크
# =============================================================
def gdjum_subscribe_hoga(code: str):
    """전일고점 주변 호가 실시간 구독"""
    screen = gdjum_status[code]["screen"]
    # FID: 41~50=매도호가잔량(1~10), 61~70=매수호가잔량(1~10)
    #      51~60=매도호가(1~10), 71~80=매수호가(1~10)
    fid_list = ";".join([str(f) for f in range(41, 81)])
    kiwoom.dynamicCall(
        "SetRealReg(QString, QString, QString, QString)",
        screen, code, fid_list, "1"
    )
    print(f"  [전일고점] {code} 호가 실시간 구독 시작")

def gdjum_check_hoga(code: str, real_type: str):
    """전일고점 ±4~5호가 잔량 × 가격 >= 1억 체크"""
    if code not in gdjum_status:
        return
    s = gdjum_status[code]
    if s["hoga_ok"] or s["order_sent"]:
        return

    prev_high = s["prev_high"]
    tick      = s["tick_size"]
    if prev_high == 0 or tick == 0:
        return

    # 전일고점 -1틱 ~ +2틱 (총 4호가) 체크
    target_prices = [
        prev_high - tick,    # 전일고점 -1틱
        prev_high,           # 전일고점
        prev_high + tick,    # 전일고점 +1틱
        prev_high + tick*2,  # 전일고점 +2틱
    ]

    all_ok = True
    for target in target_prices:
        # 매수호가에서 해당 가격 찾기 (FID 71~80: 매수호가1~10)
        hoga_found = False
        for i, fid in enumerate(range(71, 81)):
            try:
                hoga_p_str = kiwoom.dynamicCall(
                    "GetCommRealData(QString, int)", real_type, fid)
                hoga_q_str = kiwoom.dynamicCall(
                    "GetCommRealData(QString, int)", real_type, 61 + i)
                hoga_p = abs(int(hoga_p_str.strip()))
                hoga_q = abs(int(hoga_q_str.strip()))

                if hoga_p == target:
                    amount = hoga_p * hoga_q
                    if amount >= GDJUM_TICK_MIN:
                        hoga_found = True
                    else:
                        all_ok = False
                        print(f"  [호가체크] {target:,}원 "
                              f"잔량부족: {amount/1e8:.2f}억")
                    break
            except:
                pass

        if not hoga_found:
            all_ok = False

    if all_ok:
        s["hoga_ok"] = True
        print(f"  [전일고점] {s['name']} 호가조건 통과!")
        gdjum_try_enter(code)

# =============================================================
# STEP3: 5분봉 거래량 조회
# =============================================================
def gdjum_fetch_vol(idx: int):
    global gdjum_vol_idx
    gdjum_vol_idx = idx
    if idx >= len(gdjum_vol_queue):
        gdjum_vol_queue.clear()
        return
    code = gdjum_vol_queue[idx]
    if code not in gdjum_status:
        QTimer.singleShot(300, lambda: gdjum_fetch_vol(idx + 1))
        return

    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_gdjum_vol)
    kiwoom.OnReceiveTrData.connect(on_tr_gdjum_vol)
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "틱범위", "5")   # 5분봉
    kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
    kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                       "전일고점분봉조회", "opt10080", 0, "0402")

def on_tr_gdjum_vol(screen, rqname, trcode, recordname, prev_next, *args):
    if rqname != "전일고점분봉조회":
        return
    safe_disconnect(kiwoom.OnReceiveTrData, on_tr_gdjum_vol)

    code = gdjum_vol_queue[gdjum_vol_idx] if gdjum_vol_idx < len(gdjum_vol_queue) else None
    if not code or code not in gdjum_status:
        return

    s = gdjum_status[code]

    # 최근 GDJUM_CANDLE_N개 캔들 거래량 수집
    volumes = []
    for i in range(GDJUM_CANDLE_N + 1):
        try:
            vol_str = kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, i, "거래량").strip()
            vol = abs(int(vol_str))
            volumes.append(vol)
        except:
            break

    if len(volumes) < 2:
        print(f"  [거래량] 데이터 부족")
        QTimer.singleShot(300, lambda: gdjum_fetch_vol(gdjum_vol_idx + 1))
        return

    # 최신 캔들(인덱스0) = 돌파 캔들, 나머지 = 평균 계산용
    current_vol = volumes[0]
    avg_vol     = sum(volumes[1:]) / len(volumes[1:]) if len(volumes) > 1 else 1
    ratio       = current_vol / avg_vol if avg_vol > 0 else 0

    print(f"  [거래량] {s['name']} 현재:{current_vol:,} "
          f"평균:{avg_vol:,.0f} 비율:{ratio:.1f}배")

    if ratio >= GDJUM_VOL_MULT:
        s["vol_ok"] = True
        print(f"  [전일고점] {s['name']} 거래량조건 통과! ({ratio:.1f}배)")
        gdjum_try_enter(code)
    else:
        print(f"  [전일고점] {s['name']} 거래량 부족 ({ratio:.1f}배 < {GDJUM_VOL_MULT}배)")

    QTimer.singleShot(300, lambda: gdjum_fetch_vol(gdjum_vol_idx + 1))

# =============================================================
# STEP4: 8% 이상 상승 후 눌림 체크 + 최종 진입
# =============================================================
def gdjum_update_max(code: str, current_price: int):
    """최고가 업데이트 (8% 눌림 체크용)"""
    if code not in gdjum_status:
        return
    s = gdjum_status[code]
    if current_price > s["max_price"]:
        s["max_price"] = current_price

def gdjum_is_after_spike(code: str, current_price: int) -> bool:
    """8% 이상 상승 후 눌림 여부"""
    if code not in gdjum_status:
        return False
    s         = gdjum_status[code]
    entry_p   = s["entry_price"]
    max_price = s["max_price"]
    if entry_p <= 0:
        return False
    max_rise = (max_price - entry_p) / entry_p
    return max_rise >= GDJUM_RISE_SKIP  # 8% 이상 올랐었으면 True

def gdjum_try_enter(code: str):
    """호가 + 거래량 조건 모두 통과 시 진입 시도"""
    if code not in gdjum_status:
        return
    s = gdjum_status[code]

    if s["order_sent"]:
        return
    if not (s["hoga_ok"] and s["vol_ok"]):
        return   # 두 조건 모두 통과해야 진입

    # 8% 이상 상승 후 눌림 체크
    current_price = kiwoom_realtime_cache.get(code, s["entry_price"])
    gdjum_update_max(code, current_price)

    if gdjum_is_after_spike(code, current_price):
        print(f"  [전일고점] {s['name']} 8% 이상 상승 후 눌림 → 진입 스킵")
        send_telegram(
            f"<b>[전일고점돌파] 진입 스킵</b>\n"
            f"• {s['name']}\n"
            f"  8% 이상 상승 후 눌림 감지 → 매수 안 함"
        )
        gdjum_status.pop(code, None)
        return

    # 최대 포지션 체크
    if code in positions:
        return
    if len(positions) >= MAX_POSITIONS:
        return
    if not is_m2_open():
        return

    # 진입가 계산 (전일고점 - 4틱)
    entry_p = s["entry_price"]
    qty     = calc_qty(entry_p)
    if entry_p <= 0 or qty <= 0:
        return

    # 지정가 매수 주문
    screen = s["screen"]
    result = kiwoom.dynamicCall(
        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
        ["전일고점지정가매수", screen, ACCOUNT_NUM,
         1,          # 신규매수
         code, qty,
         entry_p,    # 지정가
         "00",       # 00=지정가
         ""]
    )

    s["order_sent"] = True
    noon_entry = datetime.now().time() >= NOON_CUTOFF

    # 포지션 등록 (체결 대기 상태)
    positions[code] = {
        "name":          s["name"],
        "entry_price":   entry_p,
        "qty":           qty,
        "total_qty":     qty,
        "entry_time":    datetime.now(),
        "entry_amount":  entry_p * qty,
        "high_price":    entry_p,
        "stop_price":    entry_p * (1 + STOP_LOSS_RATE),
        "trail_active":  False,
        "add_bought":    False,
        "add_timer":     None,
        "exit_timer":    None,
        "condition":     GDJUM_CONDITION,
        "noon_entry":    noon_entry,
        "is_high_price": entry_p > HIGH_PRICE_LIMIT,
        "is_overnight":  False,
    }

    # 실시간 체결가 구독 (트레일링/손절용)
    subscribe_realtime(code)

    # 20분 후 2차 매수 타이머
    add_timer = QTimer()
    add_timer.setSingleShot(True)
    add_timer.timeout.connect(lambda: check_add_buy(code))
    add_timer.start(ADD_BUY_MINUTES * 60 * 1000)
    positions[code]["add_timer"] = add_timer

    # 시간 청산 타이머
    setup_exit_timer(code, noon_entry)

    send_telegram(
        f"<b>[전일고점돌파] 지정가 매수!</b>\n"
        f"• {s['name']}\n"
        f"  전일고가: {s['prev_high']:,}원\n"
        f"  진입가: {entry_p:,}원 (전일고점-{GDJUM_TICK_DOWN}틱)\n"
        f"  수량: {qty}주  금액: {entry_p*qty:,}원\n"
        f"  손절: {entry_p*(1+STOP_LOSS_RATE):,.0f}원 (-2.5%)\n"
        f"  호가조건: OK | 거래량: OK"
    )
    print(f"  [전일고점] {s['name']} 지정가 매수 주문! {entry_p:,}원 x {qty}주")

# =============================================================
# 실시간 데이터에서 전일고점돌파 호가/체결 분기
# =============================================================
def gdjum_on_realtime(code: str, real_type: str):
    """on_realtime_data에서 전일고점돌파 종목 분기 처리"""
    if code not in gdjum_status:
        return
    s = gdjum_status[code]
    if s["order_sent"]:
        return

    if real_type == "주식호가잔량":
        gdjum_check_hoga(code, real_type)

    elif real_type == "주식체결":
        try:
            price_str = kiwoom.dynamicCall(
                "GetCommRealData(QString, int)", real_type, 10)
            price = abs(int(price_str.strip()))
            gdjum_update_max(code, price)
            kiwoom_realtime_cache[code] = price
        except:
            pass