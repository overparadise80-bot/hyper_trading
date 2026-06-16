# -*- coding: utf-8 -*-
"""
module2_hwasa.py - 단타검색식황사장 실시간 감시 + 분할매수 자동매매
- 편입 시 종목명 텔레그램 브리핑 (GetMasterCodeName — 즉시, TR 큐 불필요)
- 10분간 편입 없으면 "리스트에 안 떴다" 브리핑
- 편입 후 ENTRY_HOLD_MIN분 동안 이탈 없이 고점에 머물면 1차 진입 (시장가)
  → 이후 눌림 시 2차 추가매수는 trade_manager의 공통 로직(ADD_BUY_RATE) 사용
"""

from PyQt5.QtCore import QTimer
from modules.common import send_telegram, AUTO_TRADE_CONDITION
from modules import trade_manager as tm

NO_ENTRY_MINUTES = 10
ENTRY_HOLD_MIN   = 7   # 편입 후 이탈 없이 버틴 시간 — 충족 시 1차 진입

class Module2Hwasa:
    def __init__(self, kiwoom, queue):
        self.kiwoom  = kiwoom
        self.status  = {}   # code → {"name": str, "time": str}
        self.history = []   # {"time": str, "name": str, "action": "편입"|"이탈"}
        self._entry_timers = {}   # code → QTimer (ENTRY_HOLD_MIN분 미이탈 확인용)

        self._no_entry_timer = QTimer()
        self._no_entry_timer.setSingleShot(True)
        self._no_entry_timer.timeout.connect(self._on_no_entry_timeout)

    # =========================================================
    # 외부 진입점
    # =========================================================
    def start_monitoring(self):
        self._reset_timer()
        print(f"  [황사장] {NO_ENTRY_MINUTES}분 감시 타이머 시작")

    def stop_monitoring(self):
        self._no_entry_timer.stop()
        for t in self._entry_timers.values():
            t.stop()
        self._entry_timers.clear()
        print(f"  [황사장] 무편입 알림 타이머 종료")

    def on_enter(self, code: str, now_str: str):
        if code in self.status:
            return
        name = self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code).strip() or code
        self.status[code] = {"name": name, "time": now_str}
        self.history.append({"time": now_str, "name": name, "action": "편입"})
        self._reset_timer()
        send_telegram(
            f"📌 <b>[황사장] 편입!</b> ({now_str})\n"
            f"• <b>{name}</b>"
        )
        print(f"  [황사장] 편입 브리핑: {name} ({now_str})")

        t = QTimer()
        t.setSingleShot(True)
        t.timeout.connect(lambda c=code: self._check_entry(c))
        t.start(ENTRY_HOLD_MIN * 60 * 1000)
        self._entry_timers[code] = t

    def on_exit(self, code: str, now_str: str):
        timer = self._entry_timers.pop(code, None)
        if timer:
            timer.stop()
        name = self.status.pop(code, {}).get("name", code)
        if name:
            self.history.append({"time": now_str, "name": name, "action": "이탈"})
            send_telegram(f"📤 <b>[황사장] 이탈</b>\n• {name} ({now_str})")

    # =========================================================
    # ENTRY_HOLD_MIN분 미이탈 확인 → 1차 진입 (시장가)
    # =========================================================
    def _check_entry(self, code: str):
        self._entry_timers.pop(code, None)
        if code not in self.status:
            return   # 그 사이 이탈함 — 진입 안 함
        name = self.status[code]["name"]

        try:
            price = abs(int(
                self.kiwoom.dynamicCall("GetMasterLastPrice(QString)", code).strip()))
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            print(f"  [황사장] {name} 현재가 조회 실패 — 진입 스킵")
            return

        ok = tm.enter_position(code, name, price, condition=AUTO_TRADE_CONDITION)
        if ok:
            send_telegram(
                f"🎯 <b>[황사장] {ENTRY_HOLD_MIN}분 미이탈 — 1차 진입</b>\n"
                f"• <b>{name}</b>  {price:,}원  | 모의투자"
            )
            print(f"  [황사장] 진입: {name} @ {price:,}")
        else:
            print(f"  [황사장] {name} 진입 실패 (포지션 한도/장외 등)")

    def get_daily_summary(self) -> str:
        if not self.history:
            return "<b>[황사장]</b> 당일 편입/이탈 없음"
        lines = ["<b>[황사장] 당일 이력</b>"]
        for e in self.history:
            icon = "📌" if e["action"] == "편입" else "📤"
            lines.append(f"{icon} {e['time']} {e['action']}  {e['name']}")
        enter_cnt = sum(1 for e in self.history if e["action"] == "편입")
        exit_cnt  = sum(1 for e in self.history if e["action"] == "이탈")
        lines.append(f"\n총 편입 {enter_cnt}회 | 이탈 {exit_cnt}회")
        return "\n".join(lines)

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
