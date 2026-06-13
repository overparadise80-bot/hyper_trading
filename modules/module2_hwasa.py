# -*- coding: utf-8 -*-
"""
module2_hwasa.py - 단타검색식황사장 실시간 감시 (모니터링 전용)
- 편입 시 종목명/현재가/등락률 텔레그램 브리핑
- 10분간 편입 없으면 "리스트에 안 떴다" 브리핑
- 실매매 없음
"""

from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import send_telegram, AUTO_TRADE_CONDITION

NO_ENTRY_MINUTES = 10

class Module2Hwasa:
    def __init__(self, kiwoom, queue):
        self.kiwoom      = kiwoom
        self._queue      = queue
        self.status      = {}   # code → {"name": str, "time": str}
        self._tr_handler = None
        self._pending    = []

        self._no_entry_timer = QTimer()
        self._no_entry_timer.setSingleShot(True)
        self._no_entry_timer.timeout.connect(self._on_no_entry_timeout)

        self.kiwoom.OnReceiveTrData.connect(self._on_tr_dispatch)

    # =========================================================
    # 외부 진입점
    # =========================================================
    def start_monitoring(self):
        self._reset_timer()
        print(f"  [황사장] {NO_ENTRY_MINUTES}분 감시 타이머 시작")

    def on_enter(self, code: str, now_str: str):
        if code in self.status:
            return
        self.status[code] = {"name": "", "time": now_str}
        self._reset_timer()
        self._pending.append(code)
        if len(self._pending) == 1:
            self._fetch_detail(code)

    def on_exit(self, code: str, now_str: str):
        name = self.status.pop(code, {}).get("name", code)
        if name:
            send_telegram(f"📤 <b>[황사장] 이탈</b>\n• {name} ({now_str})")

    # =========================================================
    # 10분 타이머
    # =========================================================
    def _reset_timer(self):
        self._no_entry_timer.stop()
        self._no_entry_timer.start(NO_ENTRY_MINUTES * 60 * 1000)

    def _on_no_entry_timeout(self):
        send_telegram(
            f"⚠️ <b>[황사장]</b>\n"
            f"최근 {NO_ENTRY_MINUTES}분간 리스트에 안 떴음"
        )
        self._reset_timer()

    # =========================================================
    # 종목 상세 TR 조회 (opt10001)
    # =========================================================
    def _fetch_detail(self, code: str):
        self._tr_handler = self._on_tr_detail
        def _do(c=code):
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", c)
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                    "황사장편입상세", "opt10001", 0, "0501")
        self._queue.push(_do)

    def _on_tr_dispatch(self, screen, rqname, trcode, recordname, prev_next, *args):
        if self._tr_handler:
            self._tr_handler(screen, rqname, trcode, recordname, prev_next, *args)

    def _on_tr_detail(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "황사장편입상세":
            return
        self._tr_handler = None
        if not self._pending:
            self._queue.done()
            return

        code = self._pending.pop(0)
        if code not in self.status:
            self._queue.done()
            self._next_pending()
            return

        k = self.kiwoom
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
        except Exception:
            price, rate, is_sijeo = 0, 0.0, False

        now_str   = self.status[code]["time"]
        self.status[code]["name"] = name or code
        sijeo_tag = "  <b>[시=저]</b>" if is_sijeo else ""

        send_telegram(
            f"📌 <b>[황사장] 편입!</b> ({now_str})\n"
            f"• <b>{name}</b>{sijeo_tag}\n"
            f"  {price:,}원  <b>{rate:+.2f}%</b>"
        )
        print(f"  [황사장] 편입 브리핑: {name} ({now_str})")
        self._queue.done()
        self._next_pending()

    def _next_pending(self):
        if self._pending:
            self._fetch_detail(self._pending[0])
