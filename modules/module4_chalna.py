# -*- coding: utf-8 -*-
"""
module4_chalna.py - 찰나의 매매 + 52주 신고가 돌파
공통 인프라: 거래량 상위 100종목 실시간 구독 (10분 갱신)

[전략1] 찰나의 매매
- 조건: 매도잔량>=매수잔량x2 + 프로그램순매수 + 체결강도>100% + 대량체결 + 매도벽붕괴
- 최대 3종목

[전략2] 52주 신고가 돌파
- 조건: 현재가 >= 250일최고가 (top100 거래량 필터 내재)
- 진입: 25만원 시장가, 진입가 -2% 추가매수 25만원
- 한도: 전체 MAX_POSITIONS 공유
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
        self.alerted_h52 = {}   # {코드: datetime} — 신고가 알림 쿨다운
        self.high52      = {}   # {코드: 250일최고가}
        self._h52_queue  = []
        self._h52_idx    = 0
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
        self.refresh_timer.start(10 * 60 * 1000)
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
        self.top100     = new_codes
        self._h52_queue = list(new_codes)
        self._h52_idx   = 0
        self.high52     = {}
        print(f"  [모듈4] top100 갱신: {len(self.top100)}개 → 52주최고가 조회 시작")
        QTimer.singleShot(500, self._fetch_next_high52)

    def _fetch_next_high52(self):
        if self._h52_idx >= len(self._h52_queue):
            print(f"  [모듈4] 52주최고가 {len(self.high52)}개 로드 완료 → 실시간 구독")
            QTimer.singleShot(500, self._subscribe)
            return
        code = self._h52_queue[self._h52_idx]
        self._tr_handler = self._on_tr_high52
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "M4고가조회", "opt10001", 0, "9101")

    def _on_tr_high52(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "M4고가조회":
            return
        self._tr_handler = None
        # ── 필드명 확인용 임시 덤프 (확인 후 삭제) ──────────────────
        if self._h52_idx == 0:
            for fname in ["250최고", "250최고가", "연중최고", "52주최고", "52주최고가"]:
                try:
                    v = self.kiwoom.dynamicCall(
                        "GetCommData(QString,QString,int,QString)",
                        trcode, rqname, 0, fname).strip()
                    print(f"  [opt10001 필드확인] '{fname}' = '{v}'")
                except Exception as e:
                    print(f"  [opt10001 필드확인] '{fname}' 오류: {e}")
        # ─────────────────────────────────────────────────────────────
        try:
            raw = self.kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, 0, "250최고").strip()
            price = abs(int(raw))
            if price > 0:
                self.high52[self._h52_queue[self._h52_idx]] = price
        except:
            pass
        self._h52_idx += 1
        QTimer.singleShot(200, self._fetch_next_high52)

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
        if real_type == "주식체결" and cache.get("price", 0) > 0:
            self._check_high52(code, cache["price"], cache.get("last_bulk", 0))

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

        # 해석 A: 사라진 매도잔량(ask_prev - ask_qty)이 매수호가 잔량(bid_qty)의
        # WALL_BREAK_RATE 이상이어야 매도벽 붕괴로 인정
        wall_decrease = ask_prev - ask_qty
        wall_change   = wall_decrease / bid_qty  # 알림/로그용 비율(매수잔량 대비)
        if wall_decrease <= 0 or wall_change < WALL_BREAK_RATE:
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
            qty = max(1, 500_000 // price)   # 50만원 기준, 50만원 이상 종목은 1주
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
        ok = tm.enter_position(code, name, price, "찰나의매매", "market",
                               entry_amount=500_000, add_buy=False)
        if ok:
            qty = tm.positions[code]["qty"]
            send_telegram(
                f"<b>찰나의 매매 자동진입!</b>\n"
                f"• {name}  {qty}주  시장가\n"
                f"  진입금액: {price*qty:,}원"
            )

    # =========================================================
    # 52주 신고가 돌파 스캔 + 자동진입
    # =========================================================
    def _check_high52(self, code: str, price: int, bulk: int):
        high52 = self.high52.get(code, 0)
        if high52 <= 0 or price < high52:
            return

        now  = datetime.now()
        last = self.alerted_h52.get(code)
        if last and (now - last).total_seconds() < ALERT_COOLDOWN:
            return
        self.alerted_h52[code] = now

        name    = self.names.get(code, code)
        now_str = now.strftime("%H:%M:%S")
        rate    = (price - high52) / high52

        h52_count = sum(1 for p in tm.positions.values()
                        if p.get("condition") == "신고가돌파")
        can_enter = (
            code not in tm.positions
            and h52_count < H52_MAX_POSITIONS
            and len(tm.positions) < MAX_POSITIONS
            and is_m4_open()
        )

        qty  = calc_qty(price)
        msg  = f"<b>📈 52주 신고가 돌파!</b> ({now_str})\n"
        msg += f"• <b>{name}</b>  {price:,}원\n"
        msg += f"  52주최고: {high52:,}원  ({rate:+.2%} 돌파)\n"
        msg += f"  거래량상위 top100\n"

        if can_enter:
            msg += f"\n  → 자동 진입! {qty}주  {price * qty:,}원"
            msg += f"\n  (추매: 진입가 -2% 시 +{qty}주)"
        elif code in tm.positions:
            msg += "\n  → 이미 보유 중"
        elif h52_count >= H52_MAX_POSITIONS:
            msg += f"\n  → 신고가 최대 {H52_MAX_POSITIONS}종목 초과"
        else:
            msg += f"\n  → 전체 포지션 한도 초과 ({len(tm.positions)}/{MAX_POSITIONS})"

        send_telegram(msg)
        print(f"  [모듈4-신고가] {name} {price:,}원 (52주최고 {high52:,} | {rate:+.2%})")

        if can_enter:
            QTimer.singleShot(200, lambda: self._enter_high52(code, name, price))

    def _enter_high52(self, code: str, name: str, price: int):
        h52_count = sum(1 for p in tm.positions.values()
                        if p.get("condition") == "신고가돌파")
        if code in tm.positions or h52_count >= H52_MAX_POSITIONS or len(tm.positions) >= MAX_POSITIONS:
            return
        ok = tm.enter_position(code, name, price, "신고가돌파")
        if ok:
            qty = tm.positions[code]["qty"]
            send_telegram(
                f"<b>신고가 자동진입!</b>\n"
                f"• {name}  {qty}주  시장가\n"
                f"  진입금액: {price * qty:,}원"
            )