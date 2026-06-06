# -*- coding: utf-8 -*-
"""
module2_hwasa.py - 단타검색식황사장 자동매매
- 실시간 조건검색 편입 즉시 시장가 진입
- 25만원 / 20분 눌림 추가매수 / 트레일링 / 시간청산
"""

from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import *
from modules import trade_manager as tm

class Module2Hwasa:
    def __init__(self, kiwoom):
        self.kiwoom    = kiwoom
        self.cache     = {}   # {코드: {name, time, notified}}
        self.detail_queue = []
        self.detail_idx   = 0

    def on_enter(self, code: str, now_str: str):
        """조건검색 편입 시 호출"""
        if code in self.cache and self.cache[code].get("notified"):
            return
        self.cache[code] = {"time": now_str, "notified": False}
        print(f"  [황사장] 편입: {code} ({now_str})")
        self.detail_queue.append(code)
        if len(self.detail_queue) == 1:
            QTimer.singleShot(200, lambda: self._fetch_detail(0))

    def on_exit(self, code: str, now_str: str):
        """조건검색 이탈 시 호출"""
        name = self.cache.get(code, {}).get("name", code)
        self.cache.pop(code, None)
        send_telegram(
            f"<b>조건검색 이탈</b> ({now_str})\n"
            f"황사장\n• {name}"
        )

    def _fetch_detail(self, idx: int):
        self.detail_idx = idx
        if idx >= len(self.detail_queue):
            self.detail_queue.clear()
            return
        code = self.detail_queue[idx]
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_detail)
        except:
            pass
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_detail)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "황사장편입상세", "opt10001", 0, "0501")

    def _on_tr_detail(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "황사장편입상세":
            return
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_detail)
        except:
            pass
        code      = self.detail_queue[self.detail_idx]
        k         = self.kiwoom
        name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "종목명").strip()
        price_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "현재가").strip()
        open_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "시가").strip()
        low_str   = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "저가").strip()
        rate_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "등락율").strip()
        try:
            price    = abs(int(price_str))
            open_p   = abs(int(open_str))
            low_p    = abs(int(low_str))
            rate     = float(rate_str)
            is_sijeo = (open_p == low_p)

            if code in self.cache:
                self.cache[code]["name"]     = name
                self.cache[code]["notified"] = True

            now_s     = self.cache.get(code, {}).get("time", "?")
            sijeo_tag = "  <b>[시=저]</b>" if is_sijeo else ""

            send_telegram(
                f"<b>단타 조건검색 편입!</b> ({now_s})\n"
                f"황사장\n--------------------\n"
                f"• <b>{name}</b>{sijeo_tag}\n"
                f"  {price:,}원  <b>{rate:+.2f}%</b>"
            )

            # 자동매매 진입
            QTimer.singleShot(300, lambda: self._try_enter(code, name, price))

        except Exception as e:
            print(f"  [황사장] 상세 오류: {e}")

        QTimer.singleShot(300, lambda: self._fetch_detail(self.detail_idx + 1))

    def _try_enter(self, code: str, name: str, price: int):
        if not is_m2_open():
            return
        if code in tm.positions:
            return

        # 찰나 포지션 수 체크
        m4_count = sum(1 for p in tm.positions.values()
                       if p.get("condition") == "찰나의매매")

        ok = tm.enter_position(
            code, name, price,
            condition=AUTO_TRADE_CONDITION,
            order_type="market"
        )
        if ok:
            pos          = tm.positions[code]
            entry_p      = pos["entry_price"]
            qty          = pos["qty"]
            noon_entry   = pos["noon_entry"]
            hold_min     = 60 if noon_entry else HOLD_MINUTES
            send_telegram(
                f"<b>자동매매 진입! [황사장]</b>\n"
                f"• {name}  {qty}주  시장가\n"
                f"  진입금액: {entry_p*qty:,}원\n"
                f"  손절: {entry_p*(1+STOP_LOSS_RATE):,.0f}원 (-2.5%)\n"
                f"  {'정오이후: 1시간내 청산' if noon_entry else f'{hold_min}분 후 청산'}"
            )
