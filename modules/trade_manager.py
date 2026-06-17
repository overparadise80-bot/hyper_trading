# -*- coding: utf-8 -*-
"""
trade_manager.py - 공통 매매 관리
모든 모듈이 공유하는 포지션/진입/청산/트레일링 로직
"""

from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import *

# =============================================================
# 공유 포지션 저장소
# =============================================================
positions         = {}   # {코드: 포지션 dict}
kiwoom_realtime_cache = {}   # {코드: 현재가}
realtime_subscribed   = set()
pending_orders        = {}

# 스크린번호 카운터
_screen_counter = 300

def next_screen() -> str:
    global _screen_counter
    _screen_counter += 1
    if _screen_counter > 9999:
        _screen_counter = 300
    return str(_screen_counter).zfill(4)

# kiwoom 객체 (condition_kiwoom.py에서 주입)
_kiwoom = None

def set_kiwoom(k):
    global _kiwoom
    _kiwoom = k

# =============================================================
# 실시간 체결가 구독
# =============================================================
def subscribe_realtime(code: str):
    if code in realtime_subscribed or _kiwoom is None:
        return
    screen = next_screen()
    _kiwoom.dynamicCall(
        "SetRealReg(QString, QString, QString, QString)",
        screen, code, "10;13", "1"
    )
    realtime_subscribed.add(code)

def unsubscribe_realtime(code: str):
    if code not in realtime_subscribed or _kiwoom is None:
        return
    _kiwoom.dynamicCall("SetRealRemove(QString, QString)", "ALL", code)
    realtime_subscribed.discard(code)

# =============================================================
# 주문
# =============================================================
def send_order_market_buy(screen: str, code: str, qty: int) -> int:
    if _kiwoom is None:
        return -1
    return _kiwoom.dynamicCall(
        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
        ["시장가매수", screen, ACCOUNT_NUM, 1, code, qty, 0, "03", ""]
    )

def send_order_market_sell(screen: str, code: str, qty: int, reason: str = ""):
    if _kiwoom is None or code not in positions:
        return
    name = positions[code]["name"]
    result = _kiwoom.dynamicCall(
        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
        ["시장가매도", screen, ACCOUNT_NUM, 2, code, qty, 0, "03", ""]
    )
    print(f"  매도주문: {name} {qty}주 [{reason}] 결과:{result}")

def send_order_limit_buy(screen: str, code: str, qty: int, price: int) -> int:
    if _kiwoom is None:
        return -1
    return _kiwoom.dynamicCall(
        "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
        ["지정가매수", screen, ACCOUNT_NUM, 1, code, qty, price, "00", ""]
    )

# =============================================================
# 진입
# =============================================================
def enter_position(code: str, name: str, price: int,
                   condition: str, order_type: str = "market",
                   limit_price: int = 0,
                   entry_amount: int = 0,
                   add_buy: bool = True) -> bool:
    """
    포지션 진입
    order_type  : "market" or "limit"
    limit_price : 지정가 진입 시 가격
    entry_amount: 0이면 공통 ENTRY_AMOUNT 사용, 양수면 해당 금액 기준으로 수량 계산
    add_buy     : False면 2차 추가매수 타이머를 등록하지 않음
    """
    if code in positions:
        return False
    if len(positions) >= MAX_POSITIONS:
        return False
    if not is_m2_open():
        return False

    base_amount  = entry_amount if entry_amount > 0 else ENTRY_AMOUNT
    is_high      = price > HIGH_PRICE_LIMIT
    qty          = 1 if is_high else max(1, base_amount // price)
    noon_entry   = datetime.now().time() >= NOON_CUTOFF
    calc_amount  = price * qty
    screen       = next_screen()

    if order_type == "market":
        send_order_market_buy(screen, code, qty)
    else:
        if limit_price <= 0:
            limit_price = price
        send_order_limit_buy(screen, code, qty, limit_price)
        calc_amount = limit_price * qty

    actual_price = limit_price if order_type == "limit" else price

    positions[code] = {
        "name":          name,
        "entry_price":   actual_price,
        "qty":           qty,
        "total_qty":     qty,
        "entry_time":    datetime.now(),
        "entry_amount":  calc_amount,
        "high_price":    actual_price,
        "stop_price":    actual_price * (1 + STOP_LOSS_RATE),
        "trail_active":  False,
        "add_bought":    False,
        "add_timer":     None,
        "exit_timer":    None,
        "condition":     condition,
        "noon_entry":    noon_entry,
        "is_high_price": is_high,
        "is_overnight":  False,
    }

    subscribe_realtime(code)
    if add_buy:
        setup_add_timer(code)
    setup_exit_timer(code, noon_entry)

    return True

# =============================================================
# 2차 추가매수
# =============================================================
def setup_add_timer(code: str):
    t = QTimer()
    t.setSingleShot(True)
    t.timeout.connect(lambda: check_add_buy(code))
    t.start(ADD_BUY_MINUTES * 60 * 1000)
    if code in positions:
        positions[code]["add_timer"] = t

def check_add_buy(code: str):
    if code not in positions:
        return
    pos = positions[code]
    if pos["add_bought"]:
        return

    entry_price   = pos["entry_price"]
    current_price = kiwoom_realtime_cache.get(code, entry_price)
    rate          = (current_price - entry_price) / entry_price

    if rate <= ADD_BUY_RATE:
        is_high  = pos["is_high_price"]
        add_qty  = 1 if is_high else max(1, ADD_AMOUNT // current_price)
        screen   = next_screen()
        send_order_market_buy(screen, code, add_qty)

        pos["add_bought"]   = True
        pos["total_qty"]   += add_qty
        pos["entry_amount"] += current_price * add_qty
        pos["entry_price"]  = pos["entry_amount"] // pos["total_qty"]
        pos["stop_price"]   = pos["entry_price"] * (1 + STOP_LOSS_RATE)

        send_telegram(
            f"<b>2차 추가매수!</b>\n"
            f"• {pos['name']}  {add_qty}주  시장가\n"
            f"  현재가 {current_price:,}원  ({rate:+.2f}%)\n"
            f"  평균단가: {pos['entry_price']:,}원\n"
            f"  총 {pos['total_qty']}주 / {pos['entry_amount']:,}원"
        )

# =============================================================
# 청산
# =============================================================
def exit_position(code: str, reason: str = "청산"):
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
        f"  경과시간: {elapsed}분\n"
        f"  매매사유: {pos['condition']}"
    )

    if pos.get("add_timer"):
        pos["add_timer"].stop()
    if pos.get("exit_timer"):
        pos["exit_timer"].stop()
    unsubscribe_realtime(code)
    positions.pop(code, None)

# =============================================================
# 시간 기반 청산 타이머
# =============================================================
def setup_exit_timer(code: str, noon_entry: bool):
    if noon_entry:
        ms     = 60 * 60 * 1000
        reason = "1시간경과(정오후)"
    else:
        ms     = HOLD_MINUTES * 60 * 1000
        reason = "1시간30분경과"

    t = QTimer()
    t.setSingleShot(True)
    t.timeout.connect(lambda: exit_position(code, reason))
    t.start(ms)
    if code in positions:
        positions[code]["exit_timer"] = t

# =============================================================
# 14:50 일괄청산
# =============================================================
force_exit_timer = QTimer()

def setup_force_exit_timer():
    now    = datetime.now()
    target = now.replace(hour=14, minute=50, second=0, microsecond=0)
    if now >= target:
        return
    ms = int((target - now).total_seconds() * 1000)
    force_exit_timer.setSingleShot(True)
    force_exit_timer.timeout.connect(force_exit_all)
    force_exit_timer.start(ms)
    print(f"14:50 일괄청산 타이머 설정 ({ms//1000}초 후)")

def force_exit_all():
    if not positions:
        return
    print(f"\n[14:50] 일괄 청산 ({len(positions)}종목)")
    send_telegram(f"<b>14:50 일괄청산</b> ({len(positions)}종목)")
    for code in list(positions.keys()):
        exit_position(code, "14:50일괄청산")

# =============================================================
# 실시간 체결가 처리 (트레일링 / 손절)
# =============================================================
def on_realtime_price(code: str, real_type: str, kiwoom):
    """condition_kiwoom.py의 on_realtime_data에서 호출"""
    if real_type != "주식체결":
        return
    if code not in positions:
        return

    try:
        price_str = kiwoom.dynamicCall(
            "GetCommRealData(QString, int)", real_type, 10)
        price = abs(int(price_str.strip()))
    except:
        return

    kiwoom_realtime_cache[code] = price

    pos          = positions[code]
    entry_price  = pos["entry_price"]
    high_price   = pos["high_price"]
    trail_active = pos["trail_active"]

    # 고점 갱신
    if price > high_price:
        pos["high_price"] = price
        high_price = price

    rate = (price - entry_price) / entry_price

    # 트레일링 스탑 활성화
    if not trail_active and rate >= TRAIL_ACTIVATE:
        pos["trail_active"] = True
        pos["stop_price"]   = entry_price
        trail_active = True
        print(f"  [트레일링ON] {pos['name']} +{rate:.1%}")
        send_telegram(
            f"<b>트레일링 스탑 활성화!</b>\n"
            f"• {pos['name']}\n"
            f"  현재가: {price:,}원  ({rate:+.2%})\n"
            f"  스탑로스 → 진입가 {entry_price:,}원으로 이동"
        )

    # 트레일링 스탑 비율 동적 계산
    if trail_active:
        trail_rate = -0.03
        for threshold, t_rate in TRAIL_STOP:
            if rate >= threshold:
                trail_rate = t_rate
                break
        new_stop = high_price * (1 + trail_rate)
        if new_stop > pos["stop_price"]:
            pos["stop_price"] = new_stop

    # 손절 / 트레일링 발동
    if price <= pos["stop_price"]:
        reason = "트레일링스탑" if trail_active else "손절"
        exit_position(code, reason)

# =============================================================
# 체결 이벤트 (진입가 업데이트)
# =============================================================
def on_chejan(gubun: str, kiwoom):
    if gubun != "0":
        return
    code       = kiwoom.dynamicCall("GetChejanData(int)", 9001).strip().lstrip('A')
    order_type = kiwoom.dynamicCall("GetChejanData(int)", 905).strip()
    exec_price = kiwoom.dynamicCall("GetChejanData(int)", 910).strip()
    exec_qty   = kiwoom.dynamicCall("GetChejanData(int)", 911).strip()
    name       = kiwoom.dynamicCall("GetChejanData(int)", 302).strip()

    try:
        ep = int(exec_price)
        eq = int(exec_qty)
        if ep <= 0 or eq <= 0:
            return
    except:
        return

    if code in positions and "매수" in order_type:
        pos = positions[code]
        pos["entry_price"] = ep
        pos["stop_price"]  = ep * (1 + STOP_LOSS_RATE)
        pos["high_price"]  = ep
        print(f"  [체결확인] {name} 매수 {eq}주 @ {ep:,}원")
