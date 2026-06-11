# -*- coding: utf-8 -*-
"""
module4_chalna.py - 찰나의 매매
- 거래량 상위 100종목 실시간 호가 구독
- 조건: 매도잔량>=매수잔량x2 + 프로그램순매수 + 체결강도>100% + 대량체결 + 매도벽붕괴
- 최대 3종목 자동매매
"""

from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import *
from modules import trade_manager as tm

class Module4Chalna:
    def __init__(self, kiwoom):
        self.kiwoom      = kiwoom
        self.top100      = []
        self.names       = {}
        self.cache       = {}   # {코드: 실시간 데이터}
        self.alerted     = {}   # {코드: datetime}
        self.refresh_timer = QTimer()
        self._paused     = False  # 모듈1 스캔 중 구독 차단 플래그
        self._tr_handler = None
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_dispatch)

    def _on_tr_dispatch(self, screen, rqname, trcode, recordname, prev_next, *args):
        if self._tr_handler:
            self._tr_handler(screen, rqname, trcode, recordname, prev_next, *args)

    def start(self):
        self._fetch_top100()
        self.refresh_timer.timeout.connect(self._fetch_top100)
        self.refresh_timer.start(30 * 60 * 1000)
        print("[모듈4] 찰나의 매매 시작")

    def _fetch_top100(self):
        if not is_m4_open():
            return
        self._tr_handler = self._on_tr_top100
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "시장구분", "000")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "정렬구분", "2")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "관리종목포함", "0")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "신용구분", "0")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "거래량상위요청", "opt10030", 0, "9001")

    def _on_tr_top100(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "거래량상위요청":
            return
        self._tr_handler = None
        new_codes = []
        for i in range(TOP_N):
            try:
                code = self.kiwoom.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "종목코드").strip().lstrip('A')
                name = self.kiwoom.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "종목명").strip()
                if not code:
                    break
                new_codes.append(code)
                self.names[code] = name
                if code not in self.cache:
                    self.cache[code] = {
                        "ask_qty": 0, "bid_qty": 0, "ask_qty_prev": 0,
                        "chegyul": 0.0, "price": 0, "prog_buy": 0, "last_bulk": 0
                    }
            except:
                break
        self.top100 = new_codes
        print(f"  [모듈4] top100 갱신: {len(self.top100)}개")
        QTimer.singleShot(500, self._subscribe)

    def pause_realtime(self):
        """모듈1 스캔 중 실시간 구독 완전 차단"""
        self._paused = True
        try:
            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "9100", "ALL")
        except:
            pass
        print("  [모듈4] 실시간 구독 일시 중단 (스캔 중)")

    def resume_realtime(self):
        """모듈1 스캔 완료 후 실시간 구독 복구"""
        self._paused = False
        QTimer.singleShot(2000, self._subscribe)

    def _subscribe(self):
        if self._paused:
            return
        if not self.top100:
            return
        self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", "9100", "ALL")
        codes_str = ";".join(self.top100)
        fid_list  = "10;15;41;42;43;44;45;61;62;63;64;65;228;291"
        self.kiwoom.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            "9100", codes_str, fid_list, "0"
        )

    def on_realtime(self, code: str, real_type: str):
        if code not in self.top100 or not is_m4_open():
            return
        cache = self.cache.get(code)
        if not cache:
            return
        k = self.kiwoom

        if real_type == "주식호가잔량":
            ask = sum(abs(int(k.dynamicCall("GetCommRealData(QString, int)",
                                             real_type, f).strip() or "0"))
                      for f in [41,42,43,44,45])
            bid = sum(abs(int(k.dynamicCall("GetCommRealData(QString, int)",
                                             real_type, f).strip() or "0"))
                      for f in [61,62,63,64,65])
            cache["ask_qty_prev"] = cache["ask_qty"]
            cache["ask_qty"]      = ask
            cache["bid_qty"]      = bid

        elif real_type == "주식체결":
            for fid, key in [(10,"price"),(228,"chegyul"),(15,"last_bulk"),(291,"prog_buy")]:
                try:
                    v = k.dynamicCall("GetCommRealData(QString, int)", real_type, fid).strip()
                    if key == "price":
                        cache[key] = abs(int(v))
                    elif key == "chegyul":
                        cache[key] = float(v)
                    else:
                        cache[key] = int(v)
                except:
                    pass

        self._check(code, cache)

    def _get_bulk_threshold(self, price: int) -> int:
        if price <= PRICE_B1:   return BULK_LOW
        elif price <= PRICE_B2: return BULK_MID
        else:                   return BULK_HIGH

    def _check(self, code: str, cache: dict):
        price    = cache["price"]
        ask_qty  = cache["ask_qty"]
        bid_qty  = cache["bid_qty"]
        ask_prev = cache["ask_qty_prev"]
        chegyul  = cache["chegyul"]
        prog_buy = cache["prog_buy"]
        bulk     = cache["last_bulk"]

        if price <= 0 or bid_qty <= 0 or ask_prev <= 0:
            return
        if ask_qty < bid_qty * SELL_BUY_RATIO:    return
        if prog_buy <= 0:                          return
        if chegyul <= CHEGYUL_MIN:                 return
        if bulk < self._get_bulk_threshold(price): return

        wall_change = (ask_qty - ask_prev) / ask_prev
        if wall_change > WALL_BREAK_RATE:
            return

        self._fire_alert(code, price, ask_qty, bid_qty,
                         chegyul, prog_buy, bulk, wall_change)

    def _fire_alert(self, code, price, ask_qty, bid_qty,
                    chegyul, prog_buy, bulk, wall_change):
        now = datetime.now()
        last = self.alerted.get(code)
        if last and (now - last).total_seconds() < ALERT_COOLDOWN:
            return
        self.alerted[code] = now

        name    = self.names.get(code, code)
        now_str = now.strftime("%H:%M:%S")
        ratio   = ask_qty / bid_qty if bid_qty > 0 else 0

        m4_count = sum(1 for p in tm.positions.values()
                       if p.get("condition") == "찰나의매매")
        can_enter = (
            code not in tm.positions
            and m4_count < M4_MAX_POSITIONS
            and len(tm.positions) < MAX_POSITIONS
            and is_m4_open()
        )

        msg  = f"<b>찰나의 매매 포착!</b> ({now_str})\n"
        msg += f"• <b>{name}</b>  {price:,}원\n\n"
        msg += f"  매도잔량: {ask_qty:,}주\n"
        msg += f"  매수잔량: {bid_qty:,}주\n"
        msg += f"  잔량비율: {ratio:.1f}배\n"
        msg += f"  체결강도: {chegyul:.1f}%\n"
        msg += f"  프로그램: +{prog_buy:,}주\n"
        msg += f"  대량체결: {bulk:,}주\n"
        msg += f"  매도벽: {wall_change:+.1%} 붕괴!\n"

        if can_enter:
            qty = calc_qty(price)
            msg += f"\n  → 자동매매 진입! {qty}주  {price*qty:,}원"
        elif code in tm.positions:
            msg += "\n  → 이미 보유 중"
        elif m4_count >= M4_MAX_POSITIONS:
            msg += f"\n  → 찰나 최대 {M4_MAX_POSITIONS}종목 초과"

        send_telegram(msg)

        if can_enter:
            QTimer.singleShot(200, lambda: self._enter(code, name, price))

    def _enter(self, code: str, name: str, price: int):
        m4_count = sum(1 for p in tm.positions.values()
                       if p.get("condition") == "찰나의매매")
        if code in tm.positions or m4_count >= M4_MAX_POSITIONS:
            return
        ok = tm.enter_position(code, name, price, "찰나의매매", "market")
        if ok:
            qty = tm.positions[code]["qty"]
            send_telegram(
                f"<b>찰나의 매매 자동진입!</b>\n"
                f"• {name}  {qty}주  시장가\n"
                f"  진입금액: {price*qty:,}원"
            )
