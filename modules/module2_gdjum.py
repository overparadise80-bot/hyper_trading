# -*- coding: utf-8 -*-
"""
module2_gdjum.py - 단타검색식전일고점돌파 실시간 감시 (모니터링 전용)
- 편입 시 종목명 텔레그램 브리핑
- 10분간 편입 없으면 "리스트에 안 떴다" 브리핑
- 실매매 없음
"""

from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import send_telegram

NO_ENTRY_MINUTES = 10   # 무편입 알림 기준 (분)

class Module2Gdjum:
    def __init__(self, kiwoom):
        self.kiwoom      = kiwoom
        self.status      = {}   # code → {"name": str, "time": str}
        self._tr_handler = None
        self._pending    = []   # TR 대기 큐

        self._no_entry_timer = QTimer()
        self._no_entry_timer.setSingleShot(True)
        self._no_entry_timer.timeout.connect(self._on_no_entry_timeout)

        self.kiwoom.OnReceiveTrData.connect(self._on_tr_dispatch)

    # =========================================================
    # 외부 진입점
    # =========================================================
    def start_monitoring(self):
        """register_realtime_conditions 직후 호출 — 10분 타이머 시작"""
        self._reset_timer()
        print(f"  [전일고점] {NO_ENTRY_MINUTES}분 감시 타이머 시작")

    def on_enter(self, code: str, now_str: str):
        """조건검색 편입 — 종목명 조회 후 브리핑"""
        if code in self.status:
            return
        self.status[code] = {"name": "", "time": now_str}
        self._reset_timer()
        self._pending.append(code)
        if len(self._pending) == 1:
            self._fetch_name(code)

    def on_exit(self, code: str, now_str: str):
        name = self.status.pop(code, {}).get("name", code)
        if name:
            send_telegram(f"📤 <b>[전일고점돌파] 이탈</b>\n• {name} ({now_str})")

    # =========================================================
    # 10분 타이머
    # =========================================================
    def _reset_timer(self):
        self._no_entry_timer.stop()
        self._no_entry_timer.start(NO_ENTRY_MINUTES * 60 * 1000)

    def _on_no_entry_timeout(self):
        send_telegram(
            f"⚠️ <b>[전일고점돌파]</b>\n"
            f"최근 {NO_ENTRY_MINUTES}분간 리스트에 안 떴음"
        )
        self._reset_timer()

    # =========================================================
    # 종목명 TR 조회
    # =========================================================
    def _fetch_name(self, code: str):
        self._tr_handler = self._on_tr_name
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "전일고점종목명", "opt10001", 0, "0601")

    def _on_tr_dispatch(self, screen, rqname, trcode, recordname, prev_next, *args):
        if self._tr_handler:
            self._tr_handler(screen, rqname, trcode, recordname, prev_next, *args)

    def _on_tr_name(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "전일고점종목명":
            return
        self._tr_handler = None
        if not self._pending:
            return

        code = self._pending.pop(0)
        if code not in self.status:
            self._next_pending()
            return

        name = self.kiwoom.dynamicCall(
            "GetCommData(QString,QString,int,QString)",
            trcode, rqname, 0, "종목명"
        ).strip()
        now_str = self.status[code]["time"]
        self.status[code]["name"] = name or code

        send_telegram(
            f"📌 <b>[전일고점돌파] 편입!</b>\n"
            f"• {name}\n"
            f"  ({now_str})"
        )
        print(f"  [전일고점] 편입 브리핑: {name} ({now_str})")
        self._next_pending()

    def _next_pending(self):
        if self._pending:
            QTimer.singleShot(300, lambda: self._fetch_name(self._pending[0]))

    # =========================================================
    # 모니터링 전용 — 호가/가격 무시
    # =========================================================
    def on_realtime_hoga(self, code: str, real_type: str):
        pass

    def on_realtime_price(self, code: str, price: int):
        pass
