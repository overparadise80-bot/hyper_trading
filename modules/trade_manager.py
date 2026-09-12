# -*- coding: utf-8 -*-
"""
trade_manager.py - 공통 매매 관리
모든 모듈이 공유하는 포지션/진입/청산/트레일링 로직
"""

import os
import json
from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import *
from modules import sheets_writer

OVERNIGHT_FILE = os.path.join("logs", "overnight_positions.json")

# =============================================================
# 공유 포지션 저장소
# =============================================================
positions         = {}   # {코드: 포지션 dict}
trade_log         = []   # 당일 전체 진입 이력 (positions에서 pop되어도 유지, dict 참조 공유)
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
                   add_buy: bool = True,
                   stop_loss_rate: float = None,
                   overnight: bool = False,
                   allow_trailing: bool = True,
                   skip_time_gate: bool = False) -> bool:
    """
    포지션 진입
    order_type     : "market" or "limit"
    limit_price    : 지정가 진입 시 가격
    entry_amount   : 0이면 공통 ENTRY_AMOUNT 사용, 양수면 해당 금액 기준으로 수량 계산
    add_buy        : False면 2차 추가매수(실시간 -2% 눌림 체크)를 비활성화
    stop_loss_rate : None이면 공통 STOP_LOSS_RATE 사용, 지정 시 해당 포지션에만 적용
    overnight      : True면 당일 시간기반 청산 타이머를 걸지 않고 익영업일 시가청산 대상으로 저장
    allow_trailing : False면 실시간 트레일링 스탑 활성화를 건너뜀 (손절만 적용)
    skip_time_gate : True면 is_m2_open() 매매시간 체크를 건너뜀 (14:00 이후 진입하는 전략용)
    """
    if code in positions:
        return False
    if len(positions) >= MAX_POSITIONS:
        return False
    if not skip_time_gate and not is_m2_open():
        return False

    base_amount  = entry_amount if entry_amount > 0 else ENTRY_AMOUNT
    is_high      = price > HIGH_PRICE_LIMIT
    qty          = 1 if is_high else max(1, base_amount // price)
    noon_entry   = datetime.now().time() >= NOON_CUTOFF
    calc_amount  = price * qty
    screen       = next_screen()
    stop_rate    = stop_loss_rate if stop_loss_rate is not None else STOP_LOSS_RATE

    if order_type == "market":
        send_order_market_buy(screen, code, qty)
    else:
        if limit_price <= 0:
            limit_price = price
        send_order_limit_buy(screen, code, qty, limit_price)
        calc_amount = limit_price * qty

    actual_price = limit_price if order_type == "limit" else price

    positions[code] = {
        "code":          code,
        "name":          name,
        "entry_price":   actual_price,
        "qty":           qty,
        "total_qty":     qty,
        "entry_time":    datetime.now(),
        "entry_amount":  calc_amount,
        "high_price":    actual_price,
        "stop_price":    actual_price * (1 + stop_rate),
        "stop_loss_rate": stop_rate,
        "trail_active":  False,
        "allow_trailing": allow_trailing,
        "add_bought":    False,
        "add_buy_enabled": add_buy,
        "exit_timer":    None,
        "condition":     condition,
        "noon_entry":    noon_entry,
        "is_high_price": is_high,
        "is_overnight":  overnight,
        "status":        "OPEN",
        "exit_price":    None,
        "exit_time":     None,
        "exit_reason":   None,
        "pnl_rate":      None,
        "pnl_amount":    None,
    }

    trade_log.append(positions[code])
    subscribe_realtime(code)
    if overnight:
        _save_overnight_position(code)
    else:
        setup_exit_timer(code, noon_entry)

    return True

# =============================================================
# 2차 추가매수 (실시간 체결가 기준 — 손절과 동일하게 틱마다 체크)
# =============================================================
def do_add_buy(code: str, current_price: int, rate: float):
    pos      = positions[code]
    is_high  = pos["is_high_price"]
    add_qty  = 1 if is_high else max(1, ADD_AMOUNT // current_price)
    screen   = next_screen()
    send_order_market_buy(screen, code, add_qty)

    pos["add_bought"]   = True
    pos["total_qty"]   += add_qty
    pos["entry_amount"] += current_price * add_qty
    pos["entry_price"]  = pos["entry_amount"] // pos["total_qty"]
    pos["stop_price"]   = pos["entry_price"] * (1 + pos["stop_loss_rate"])

    send_telegram(
        f"📉 <b>[{pos['condition']}] 눌림 추매</b>\n"
        f"• <b>{pos['name']}</b>  {add_qty}주  시장가\n"
        f"  추매가: {current_price:,}원  ({rate:+.2%})\n"
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

    pos["status"]     = "CLOSED"
    pos["exit_price"] = cur_price
    pos["exit_time"]  = datetime.now()
    pos["exit_reason"] = reason
    pos["pnl_rate"]    = pnl_rate
    pos["pnl_amount"]  = pnl_amount

    send_telegram(
        f"<b>자동매매 청산 [{reason}]</b>\n"
        f"• {pos['name']}\n"
        f"  매수단가: {entry_price:,}원\n"
        f"  매도단가: {cur_price:,}원\n"
        f"  수익률: <b>{pnl_rate:+.2%}</b>\n"
        f"  수익금액: {pnl_amount:+,.0f}원\n"
        f"  경과시간: {elapsed}분\n"
        f"  매매사유: {pos['condition']}"
    )

    try:
        sheets_writer.write_trade_record(pos)
    except Exception as e:
        print(f"  [trade_manager] 시트 기록 오류: {e}")

    if pos.get("exit_timer"):
        pos["exit_timer"].stop()
    unsubscribe_realtime(code)
    positions.pop(code, None)
    if pos.get("is_overnight"):
        _clear_overnight_position(code)

# =============================================================
# 익영업일 시가청산 (종가베팅 등 overnight=True 포지션)
# - condition_kiwoom_v2.py는 매일 08:00에 새 프로세스로 재시작되므로
#   positions 딕셔너리(메모리)가 초기화됨 → 파일로 영속화 후 기동 시 복원
# =============================================================
def _load_overnight_file() -> dict:
    if os.path.exists(OVERNIGHT_FILE):
        try:
            with open(OVERNIGHT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _write_overnight_file(data: dict):
    try:
        os.makedirs(os.path.dirname(OVERNIGHT_FILE), exist_ok=True)
        with open(OVERNIGHT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [trade_manager] 익일청산 파일 저장 오류: {e}")

def _save_overnight_position(code: str):
    pos  = positions.get(code)
    if not pos:
        return
    data = _load_overnight_file()
    data[code] = {
        "name":        pos["name"],
        "qty":         pos["total_qty"],
        "entry_price": pos["entry_price"],
        "entry_date":  datetime.now().strftime("%Y-%m-%d"),
        "entry_time":  pos["entry_time"].isoformat(),
        "condition":   pos["condition"],
    }
    _write_overnight_file(data)

def _clear_overnight_position(code: str):
    data = _load_overnight_file()
    if code in data:
        data.pop(code)
        _write_overnight_file(data)

def load_and_schedule_overnight_exit():
    """condition_kiwoom_v2.py 기동 시 1회 호출 — 전날 종가베팅 물량을 복원하고 09:01 시가청산 예약"""
    data = _load_overnight_file()
    if not data:
        return
    today   = datetime.now().strftime("%Y-%m-%d")
    pending = {c: d for c, d in data.items() if d.get("entry_date") != today}
    if not pending:
        return

    print(f"[trade_manager] 익일시가청산 대상 {len(pending)}종목 복원")
    for code, d in pending.items():
        if code in positions:
            continue
        entry_time_str = d.get("entry_time")
        if entry_time_str:
            try:
                entry_time = datetime.fromisoformat(entry_time_str)
            except ValueError:
                entry_time = datetime.strptime(d["entry_date"], "%Y-%m-%d")
        else:
            entry_time = datetime.strptime(d["entry_date"], "%Y-%m-%d")
        positions[code] = {
            "code":          code,
            "name":          d["name"],
            "entry_price":   d["entry_price"],
            "qty":           d["qty"],
            "total_qty":     d["qty"],
            "entry_time":    entry_time,
            "entry_amount":  d["entry_price"] * d["qty"],
            "high_price":    d["entry_price"],
            "stop_price":    0,
            "stop_loss_rate": 0,
            "trail_active":  False,
            "allow_trailing": False,
            "add_bought":    True,
            "add_buy_enabled": False,
            "exit_timer":    None,
            "condition":     d.get("condition", "종가베팅"),
            "noon_entry":    False,
            "is_high_price": False,
            "is_overnight":  True,
            "status":        "OPEN",
            "exit_price":    None,
            "exit_time":     None,
            "exit_reason":   None,
            "pnl_rate":      None,
            "pnl_amount":    None,
        }
        subscribe_realtime(code)

    _schedule_open_exit(list(pending.keys()))

def _schedule_open_exit(codes: list):
    now    = datetime.now()
    target = now.replace(hour=9, minute=1, second=0, microsecond=0)
    if now >= target:
        QTimer.singleShot(3000, lambda: _run_open_exit(codes))
        return
    diff = int((target - now).total_seconds() * 1000)
    QTimer.singleShot(diff, lambda: _run_open_exit(codes))
    print(f"[trade_manager] 익일시가청산 타이머 설정 (09:01, {diff // 1000}초 후)")

def _run_open_exit(codes: list):
    print(f"[trade_manager] 익일시가청산 실행 ({len(codes)}종목)")
    for code in codes:
        if code in positions:
            exit_position(code, "익일시가청산")

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

    # 트레일링 스탑 활성화 (allow_trailing=False인 포지션은 건너뜀 — 손절만 적용)
    if pos.get("allow_trailing", True) and not trail_active and rate >= TRAIL_ACTIVATE:
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

    # 2차 추가매수 (손절보다 먼저 체크 — 동일 -2% 기준이어도 추매가 우선 발동)
    if not pos["add_bought"] and pos["add_buy_enabled"] and rate <= ADD_BUY_RATE:
        do_add_buy(code, price, rate)
        pos = positions[code]

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
        if not pos.get("add_bought"):
            # 1차 매수 체결: 실체결가로 진입가 확정
            pos["entry_price"] = ep
            pos["stop_price"]  = ep * (1 + pos["stop_loss_rate"])
            pos["high_price"]  = ep
        else:
            # 2차 추가매수 체결: do_add_buy에서 계산한 가중평균 유지, stop_price만 갱신
            pos["stop_price"] = pos["entry_price"] * (1 + pos["stop_loss_rate"])
        print(f"  [체결확인] {name} 매수 {eq}주 @ {ep:,}원")

# =============================================================
# 당일 매매 결과 요약 (진입~청산 전체 이력, positions pop 후에도 유지)
# =============================================================
def get_trade_summary_text() -> str:
    if not trade_log:
        return "오늘 진입한 매매가 없습니다."

    lines      = []
    total_pnl  = 0
    closed_cnt = 0
    open_cnt   = 0
    win_cnt    = 0
    lose_cnt   = 0

    for i, pos in enumerate(trade_log, 1):
        entry_time = pos["entry_time"].strftime("%H:%M")
        if pos.get("status") == "CLOSED":
            closed_cnt += 1
            pnl_rate   = pos["pnl_rate"]
            pnl_amount = pos["pnl_amount"]
            total_pnl += pnl_amount
            if pnl_amount >= 0:
                win_cnt += 1
            else:
                lose_cnt += 1
            exit_time = pos["exit_time"].strftime("%H:%M")
            lines.append(
                f"{i}. {pos['name']} [{pos['condition']}]\n"
                f"   {entry_time}→{exit_time}  {pos['entry_price']:,}→{pos['exit_price']:,}원\n"
                f"   {pnl_rate:+.2%} / {pnl_amount:+,.0f}원  ({pos['exit_reason']})"
            )
        else:
            open_cnt += 1
            cur_price = kiwoom_realtime_cache.get(pos["code"], pos["entry_price"])
            rate      = (cur_price - pos["entry_price"]) / pos["entry_price"]
            lines.append(
                f"{i}. {pos['name']} [{pos['condition']}]  <b>보유중</b>\n"
                f"   진입 {entry_time}  {pos['entry_price']:,}→{cur_price:,}원\n"
                f"   평가손익: {rate:+.2%}"
            )

    header = (
        f"총 {len(trade_log)}건 진입 (청산 {closed_cnt} / 보유중 {open_cnt})\n"
        f"승 {win_cnt} 패 {lose_cnt}\n"
        f"누적손익(청산분): {total_pnl:+,.0f}원\n"
        f"{'─' * 20}"
    )
    return header + "\n\n" + "\n\n".join(lines)
