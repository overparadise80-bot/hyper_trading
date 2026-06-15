# -*- coding: utf-8 -*-
"""
module2_hwasa.py - 단타검색식황사장 실시간 감시 (모니터링 전용)
- 편입 시 종목명 텔레그램 브리핑 (GetMasterCodeName — 즉시, TR 큐 불필요)
- 10분간 편입 없으면 "리스트에 안 떴다" 브리핑
- 실매매 없음
"""

from PyQt5.QtCore import QTimer
from modules.common import send_telegram, AUTO_TRADE_CONDITION

NO_ENTRY_MINUTES = 10

class Module2Hwasa:
    def __init__(self, kiwoom, queue):
        self.kiwoom = kiwoom
        self.status = {}   # code → {"name": str, "time": str}

        self._no_entry_timer = QTimer()
        self._no_entry_timer.setSingleShot(True)
        self._no_entry_timer.timeout.connect(self._on_no_entry_timeout)

    # =========================================================
    # 외부 진입점
    # =========================================================
    def start_monitoring(self):
        self._reset_timer()
        print(f"  [황사장] {NO_ENTRY_MINUTES}분 감시 타이머 시작")

    def on_enter(self, code: str, now_str: str):
        if code in self.status:
            return
        # GetMasterCodeName: 로컬 OCX 캐시 즉시 반환, TR 큐 불필요
        name = self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code).strip() or code
        self.status[code] = {"name": name, "time": now_str}
        self._reset_timer()
        send_telegram(
            f"📌 <b>[황사장] 편입!</b> ({now_str})\n"
            f"• <b>{name}</b>"
        )
        print(f"  [황사장] 편입 브리핑: {name} ({now_str})")

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
