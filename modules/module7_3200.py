# -*- coding: utf-8 -*-
"""
module7_3200.py - 0150 조건검색 "3분200억거래대금" 실시간 스캔 자동매매
- 편입 즉시 opt10001로 현재가 조회 후 시장가 1차 25만원 진입
- 2차 추매(-2% 눌림 25만원) / 트레일링 스탑 / 시간청산은 trade_manager 공통 로직 사용
- 손절만 모듈7 전용 -2% 적용 (공통 STOP_LOSS_RATE -2.5%와 별도)
"""

from collections import deque
from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import (
    send_telegram, M7_CONDITION, M7_SCREEN, M7_STOP_LOSS_RATE,
    MAX_POSITIONS, is_m7_open, calc_qty
)
from modules import trade_manager as tm

TR_SCREEN        = "0704"   # opt10001 현재가 조회 전용 스크린
NO_ENTRY_MINUTES = 10


class Module7Scan3200:

    def __init__(self, kiwoom, queue):
        self.kiwoom = kiwoom
        self.queue  = queue

        self.status  = {}   # code → {"name", "enter_time"}
        self.history = []   # {"time", "name", "action"}

        self._basic_q = deque()   # opt10001 요청 순서 (FIFO)

        self._no_entry_timer = QTimer()
        self._no_entry_timer.setSingleShot(True)
        self._no_entry_timer.timeout.connect(self._on_no_entry_timeout)

        kiwoom.OnReceiveTrData.connect(self._on_tr)

    # =========================================================
    # 외부 진입점 (condition_kiwoom_v2 → 호출)
    # =========================================================
    def start_monitoring(self):
        self._reset_no_entry_timer()
        print("  [3분200억] 모니터링 시작")

    def stop_monitoring(self):
        self._no_entry_timer.stop()
        print("  [3분200억] 모니터링 종료")

    def on_enter(self, code: str, now_str: str):
        if not is_m7_open():
            return
        if code in self.status:
            return
        if code in tm.positions or len(tm.positions) >= MAX_POSITIONS:
            return

        name = self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code).strip() or code
        self.status[code] = {"name": name, "enter_time": datetime.now()}
        self.history.append({"time": now_str, "name": name, "action": "편입"})
        self._reset_no_entry_timer()
        print(f"  [3분200억] 편입: {name} ({code}) → 즉시 진입 시도")

        self._basic_q.append(code)
        def _req(c=code):
            self.kiwoom.dynamicCall("SetInputValue(QString,QString)", "종목코드", c)
            self.kiwoom.dynamicCall(
                "CommRqData(QString,QString,int,QString)",
                "m7_basic", "opt10001", 0, TR_SCREEN)
        self.queue.push(_req)

    def on_exit(self, code: str, now_str: str):
        s = self.status.pop(code, None)
        name = s["name"] if s else code
        self.history.append({"time": now_str, "name": name, "action": "이탈"})

    # =========================================================
    # TR 수신 — opt10001 (현재가) → 즉시 시장가 진입
    # =========================================================
    def _on_tr(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "m7_basic":
            return
        code = self._basic_q.popleft() if self._basic_q else None
        self.queue.done()

        if not code or code not in self.status:
            return   # 조회 도중 조건 이탈 — 진입 안 함
        s    = self.status.pop(code)
        name = s["name"]

        try:
            price_str = self.kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, 0, "현재가").strip()
            price = abs(int(price_str))
        except Exception as e:
            print(f"  [3분200억] {name} 현재가 조회 오류: {e}")
            return

        if price <= 0:
            print(f"  [3분200억] {name} 현재가 미수신 — 진입 스킵")
            return
        if code in tm.positions or len(tm.positions) >= MAX_POSITIONS:
            print(f"  [3분200억] {name} 포지션 한도 초과 — 스킵")
            return
        if not is_m7_open():
            print(f"  [3분200억] 장외 시간 — 주문 스킵")
            return

        ok = tm.enter_position(
            code, name, price,
            condition=M7_CONDITION,
            order_type="market",
            stop_loss_rate=M7_STOP_LOSS_RATE,
        )
        if not ok:
            print(f"  [3분200억] {name} 진입 실패")
            return

        qty = calc_qty(price)
        send_telegram(
            f"🎯 <b>[3분200억] 즉시 진입</b>\n"
            f"• <b>{name}</b>  {price:,}원  {qty}주\n"
            f"  손절 {M7_STOP_LOSS_RATE:+.1%} | -2%눌림 추매 | 트레일링 적용"
        )
        print(f"  [3분200억] 진입 — 시장가 {qty}주 @ {price:,}")

    # =========================================================
    # 무편입 알림 타이머
    # =========================================================
    def _reset_no_entry_timer(self):
        self._no_entry_timer.stop()
        self._no_entry_timer.start(NO_ENTRY_MINUTES * 60 * 1000)

    def _on_no_entry_timeout(self):
        send_telegram(
            f"⚠️ <b>[3분200억]</b>\n"
            f"최근 {NO_ENTRY_MINUTES}분간 리스트에 안 떴음"
        )
        self._reset_no_entry_timer()

    # =========================================================
    # 일일 요약
    # =========================================================
    def get_daily_summary(self) -> str:
        if not self.history:
            return "<b>[3분200억]</b> 당일 편입/이탈 없음"
        lines = ["<b>[3분200억] 당일 이력</b>"]
        for e in self.history:
            icon = "📌" if e["action"] == "편입" else "📤"
            lines.append(f"{icon} {e['time']} {e['action']}  {e['name']}")
        enter_cnt = sum(1 for e in self.history if e["action"] == "편입")
        exit_cnt  = sum(1 for e in self.history if e["action"] == "이탈")
        lines.append(f"\n총 편입 {enter_cnt}회 | 이탈 {exit_cnt}회")
        return "\n".join(lines)
