# -*- coding: utf-8 -*-
"""
module4_chalna.py - 찰나의 매매 + 52주 신고가 돌파
공통 인프라: 거래량 상위 200종목 실시간 구독 (10분 갱신)

[전략1] 찰나의 매매
- 조건: 매도잔량>=매수잔량x4 + 프로그램순매수 + 체결강도>100% + 대량체결
  → 위 조건을 만족하는 틱이 20초 내 3회 이상 반복돼야 신호 후보로 인정
  → 후보 확정 후 3초 대기, 그 사이 가격이 트리거가 대비 -0.5% 이상 밀리면 진입 취소(팔로우스루)
- 최대 3종목

[전략2] 52주 신고가 돌파
- 조건: 현재가 >= 250일최고가 (top100 거래량 필터 내재)
- 진입: 25만원 시장가, 진입가 -2% 추가매수 25만원
- 한도: 전체 MAX_POSITIONS 공유
"""

from datetime import datetime, timedelta
from PyQt5.QtCore import QTimer
from modules.common import *
from modules import trade_manager as tm

class Module4Chalna:
    # Kiwoom SetRealReg는 화면번호당 최대 100종목까지만 등록 가능 → 200종목을 2개 화면으로 분산
    REALTIME_SCREENS = ["9100", "9101"]

    def __init__(self, kiwoom):
        self.kiwoom      = kiwoom
        self.top100      = []
        self.names       = {}
        self.cache       = {}   # {코드: 실시간 데이터}
        self.alerted     = {}   # {코드: datetime}
        self.refresh_timer = QTimer()
        self._paused     = False  # 모듈1 스캔 중 구독 차단 플래그
        self._tr_handler = None
        self._pending_ft = set()  # 팔로우스루 대기 중인 종목(중복 예약 방지)
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
        print(f"  [모듈4] top100 갱신: {len(self.top100)}개 → 실시간 구독")
        QTimer.singleShot(500, self._subscribe)

    def pause_realtime(self):
        """모듈1 스캔 중 실시간 구독 완전 차단"""
        self._paused = True
        try:
            for screen in self.REALTIME_SCREENS:
                self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", screen, "ALL")
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
        fid_list = "10;15;41;42;43;44;45;61;62;63;64;65;228;291"
        for screen in self.REALTIME_SCREENS:
            self.kiwoom.dynamicCall("SetRealRemove(QString, QString)", screen, "ALL")
        for i, screen in enumerate(self.REALTIME_SCREENS):
            chunk = self.top100[i * 100:(i + 1) * 100]
            if not chunk:
                continue
            self.kiwoom.dynamicCall(
                "SetRealReg(QString, QString, QString, QString)",
                screen, ";".join(chunk), fid_list, "0"
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
                        # last_bulk(FID15)는 abs() 없이 부호 유지: 매도 체결(-)은
                        # 임계값 미달로 자연 필터링되어 매수 대량체결만 조건 통과
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

        # 반복 횟수 집계: 20초 윈도우 내 3회 이상 조건 충족 시에만 신호 후보로 인정
        now  = datetime.now()
        hits = cache.setdefault("bulk_hits", [])
        hits.append(now)
        cutoff = now - timedelta(seconds=BULK_COUNT_WINDOW)
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) < BULK_COUNT_REQUIRED:
            return

        cache["bulk_hits"] = []   # 후보 확정 → 카운트 리셋 (팔로우스루 통과/실패 무관하게 재시작)
        self._schedule_followthrough(code, price)

    def _schedule_followthrough(self, code: str, trigger_price: int):
        if code in self._pending_ft:
            return
        self._pending_ft.add(code)
        QTimer.singleShot(
            BULK_FOLLOWTHROUGH_WAIT * 1000,
            lambda: self._confirm_followthrough(code, trigger_price)
        )

    def _confirm_followthrough(self, code: str, trigger_price: int):
        self._pending_ft.discard(code)
        if code not in self.top100:
            return
        cache = self.cache.get(code)
        if not cache or cache["price"] <= 0:
            return

        cur_price = cache["price"]
        rate = (cur_price - trigger_price) / trigger_price
        if rate < BULK_FOLLOWTHROUGH_TOL:
            print(f"  [모듈4] {self.names.get(code, code)} 팔로우스루 실패 ({rate:+.2%}) - 진입 취소")
            return

        self._fire_alert(code, cur_price, cache["ask_qty"], cache["bid_qty"],
                         cache["chegyul"], cache["prog_buy"], cache["last_bulk"])

    def _fire_alert(self, code, price, ask_qty, bid_qty,
                    chegyul, prog_buy, bulk):
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
            qty  = tm.positions[code]["qty"]
            rate = get_day_rate(self.kiwoom, code, price)
            send_telegram(
                f"<b>찰나의 매매 자동진입!</b>\n"
                f"• {name}  {qty}주  시장가  ({rate:+.2%})\n"
                f"  진입금액: {price*qty:,}원"
            )

