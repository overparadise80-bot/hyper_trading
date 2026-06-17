# -*- coding: utf-8 -*-
"""
module2_gdjum.py - 단타검색식전일고점돌파 자동매매 (B안: CommRqData 폴링)
- SetRealReg 없음 — 호가 감시를 opt10004 CommRqData 5초 폴링으로 대체
- 모의투자 계좌(ACCOUNT_NUM)로 지정가 매수 실행
- 편입 → 일봉조회(opt10081, 전일고가) → 호가폴링(opt10004) + 거래량조회(opt10080) → 진입
"""

import os
import time
from collections import deque
from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import (
    send_telegram, GDJUM_CONDITION, GDJUM_TICK_DOWN, GDJUM_RISE_SKIP,
    GDJUM_VOL_MULT, GDJUM_CANDLE_N, MAX_POSITIONS, is_m2_open,
    get_tick_size
)
from modules import trade_manager as tm

GDJUM_TICK_MIN   = 2e7     # 호가잔량 최소 2천만원
GDJUM_ENTRY_AMT  = 500_000 # 1회 진입금액 50만원
MAX_WAIT_MIN     = 120      # 편입 후 최대 대기 (분)
HOGA_POLL_MS     = 5_000    # 호가 폴링 주기
NO_ENTRY_MINUTES = 10       # 무편입 알림 기준 (분)
HOGA_SCREEN_BASE = 620      # 호가 폴링 전용 스크린번호 범위 시작

# ★ 같은 스크립트가 중복 프로세스로 떠 있는 경우 알림이 동시에 두 번 발송되는 것을 방지
# (파일 mtime 기준 dedupe — 두 프로세스의 타이머는 거의 동시에 시작되므로 짧은 윈도우로 충분)
_NO_ENTRY_FLAG  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "gdjum_no_entry.flag")
_DEDUPE_SEC     = 30


class Module2Gdjum:

    def __init__(self, kiwoom, queue):
        self.kiwoom = kiwoom
        self.queue  = queue

        self.status  = {}   # code → status dict
        self.history = []   # {"time", "name", "action"}

        # TR 응답 역매핑: KiwoomQueue가 직렬화하므로 deque FIFO가 정확
        self._basic_q = deque()   # (code,) 순서대로 opt10001 요청됨
        self._vol_q   = deque()   # (code,) 순서대로 opt10080 요청됨

        # opt10004 응답 역매핑: screen → code
        self._hoga_screen_map     = {}
        self._hoga_screen_counter = HOGA_SCREEN_BASE

        # 무편입 알림 타이머
        self._no_entry_timer = QTimer()
        self._no_entry_timer.setSingleShot(True)
        self._no_entry_timer.timeout.connect(self._on_no_entry_timeout)

        # 호가 폴링 타이머
        self._hoga_poll_timer = QTimer()
        self._hoga_poll_timer.timeout.connect(self._enqueue_hoga_polls)

        kiwoom.OnReceiveTrData.connect(self._on_tr)

    # =========================================================
    # 외부 진입점 (condition_kiwoom_v2 → 호출)
    # =========================================================
    def start_monitoring(self):
        self._reset_no_entry_timer()
        self._hoga_poll_timer.start(HOGA_POLL_MS)
        print(f"  [전일고점] 모니터링 시작 (호가 {HOGA_POLL_MS//1000}초 폴링)")

    def stop_monitoring(self):
        self._no_entry_timer.stop()
        self._hoga_poll_timer.stop()
        print("  [전일고점] 모니터링 종료")

    def on_enter(self, code: str, now_str: str):
        if code in self.status:
            return
        if code in tm.positions or len(tm.positions) >= MAX_POSITIONS:
            return

        name        = self.kiwoom.dynamicCall(
            "GetMasterCodeName(QString)", code).strip() or code
        hoga_screen = self._alloc_hoga_screen()

        self.status[code] = {
            "name":        name,
            "prev_high":   0,
            "tick_size":   0,
            "entry_price": 0,
            "max_price":   0,
            "hoga_ok":     False,
            "vol_ok":      False,
            "order_sent":  False,
            "enter_time":  datetime.now(),
            "hoga_screen": hoga_screen,
        }
        self.history.append({"time": now_str, "name": name, "action": "편입"})
        self._reset_no_entry_timer()
        print(f"  [전일고점] 편입: {name} ({code})")

        # KiwoomQueue 직렬화 → deque FIFO로 응답 코드 추적
        self._basic_q.append(code)
        def _req():
            self.kiwoom.dynamicCall(
                "SetInputValue(QString,QString)", "종목코드", code)
            self.kiwoom.dynamicCall(
                "SetInputValue(QString,QString)", "수정주가구분", "1")
            self.kiwoom.dynamicCall(
                "CommRqData(QString,QString,int,QString)",
                "gdjum_basic", "opt10081", 0, "0601")
        self.queue.push(_req)

    def on_exit(self, code: str, now_str: str):
        s = self.status.pop(code, None)
        name = s["name"] if s else code
        self.history.append({"time": now_str, "name": name, "action": "이탈"})
        if s:
            self._hoga_screen_map.pop(s["hoga_screen"], None)
            # 주문이 나간 종목만 이탈 알림 (포지션 추적 필요)
            if s["order_sent"]:
                send_telegram(
                    f"📤 <b>[전일고점돌파] 이탈</b>\n"
                    f"• {name} ({now_str})"
                )

    # =========================================================
    # TR 수신 허브
    # =========================================================
    def _on_tr(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname == "gdjum_basic":
            self._on_tr_basic(trcode, rqname)
            self.queue.done()
        elif rqname == "gdjum_hoga":
            code = self._hoga_screen_map.get(screen)
            if code:
                self._on_tr_hoga(code, trcode, rqname)
            self.queue.done()
        elif rqname == "gdjum_vol":
            code = self._vol_q.popleft() if self._vol_q else None
            if code:
                self._on_tr_vol(code, trcode, rqname)
            self.queue.done()

    # =========================================================
    # opt10081 — 일봉차트 (index 0: 당일, index 1: 전일 → 전일고가, 진입가 계산)
    # =========================================================
    def _on_tr_basic(self, trcode: str, rqname: str):
        code = self._basic_q.popleft() if self._basic_q else None
        if not code or code not in self.status:
            return
        s = self.status[code]
        k = self.kiwoom
        name = s["name"]

        try:
            price_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                      trcode, rqname, 0, "현재가").strip()
            high_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                      trcode, rqname, 1, "고가").strip()
            price     = abs(int(price_str))
            prev_high = abs(int(high_str))
        except Exception as e:
            print(f"  [전일고점] opt10081 파싱 오류: {e}")
            self.status.pop(code, None)
            return

        if prev_high == 0:
            print(f"  [전일고점] {name} 전일고가 0 → 스킵")
            self.status.pop(code, None)
            return

        tick    = get_tick_size(prev_high)
        entry_p = prev_high - tick * GDJUM_TICK_DOWN

        s["prev_high"]   = prev_high
        s["tick_size"]   = tick
        s["entry_price"] = entry_p
        s["max_price"]   = price

        # 호가 스크린 등록 (폴링 타이머가 다음 주기에 바로 사용)
        self._hoga_screen_map[s["hoga_screen"]] = code
        print(f"  [전일고점] {name} 전일고가:{prev_high:,} 진입예정:{entry_p:,}")

        # 편입 직후 거래량 1회 조회
        self._enqueue_vol(code)

    # =========================================================
    # 호가 폴링 — opt10004 (5초 주기)
    # =========================================================
    def _enqueue_hoga_polls(self):
        """감시 중인 모든 코드의 opt10004 요청을 큐에 등록"""
        for code, s in list(self.status.items()):
            if s["order_sent"] or s["prev_high"] == 0:
                continue
            if self._is_expired(code):
                self._handle_expired(code)
                continue
            screen = s["hoga_screen"]
            self._hoga_screen_map[screen] = code
            def _req(c=code, scr=screen):
                self.kiwoom.dynamicCall(
                    "SetInputValue(QString,QString)", "종목코드", c)
                self.kiwoom.dynamicCall(
                    "CommRqData(QString,QString,int,QString)",
                    "gdjum_hoga", "opt10004", 0, scr)
            self.queue.push(_req)

    def _on_tr_hoga(self, code: str, trcode: str, rqname: str):
        if code not in self.status:
            return
        s = self.status[code]
        if s["hoga_ok"] or s["order_sent"]:
            return

        prev_high = s["prev_high"]
        tick      = s["tick_size"]
        k         = self.kiwoom

        # 매도/매수 호가 10단계 읽어서 {price: 잔량금액} 맵 생성
        hoga_map = {}
        try:
            # 현재가 업데이트 (8% 상승 체크용)
            cur_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                    trcode, rqname, 0, "현재가").strip()
            if cur_str:
                cur_price = abs(int(cur_str))
                if cur_price > s["max_price"]:
                    s["max_price"] = cur_price

            for p_field, q_field in [
                ("매도호가{}", "매도잔량{}"),
                ("매수호가{}", "매수잔량{}"),
            ]:
                for i in range(1, 11):
                    p_raw = k.dynamicCall(
                        "GetCommData(QString,QString,int,QString)",
                        trcode, rqname, 0, p_field.format(i)).strip()
                    q_raw = k.dynamicCall(
                        "GetCommData(QString,QString,int,QString)",
                        trcode, rqname, 0, q_field.format(i)).strip()
                    if not p_raw or not q_raw:
                        continue
                    price = abs(int(p_raw))
                    qty   = abs(int(q_raw))
                    if price > 0:
                        hoga_map[price] = hoga_map.get(price, 0) + price * qty
        except Exception as e:
            print(f"  [전일고점] opt10004 파싱 오류 ({s['name']}): {e}")
            return

        # 전일고점 ±틱 4단계 각각 5천만원 이상 확인
        targets = [
            prev_high - tick,
            prev_high,
            prev_high + tick,
            prev_high + tick * 2,
        ]
        all_ok = all(hoga_map.get(t, 0) >= GDJUM_TICK_MIN for t in targets)

        if all_ok:
            s["hoga_ok"] = True
            print(f"  [전일고점] {s['name']} 호가 조건 통과!")
            if not s["vol_ok"]:
                self._enqueue_vol(code)
            else:
                self._try_enter(code)

    # =========================================================
    # 거래량 조회 — opt10080 (5분봉)
    # =========================================================
    def _enqueue_vol(self, code: str):
        # 이미 이 코드의 vol 요청이 deque에 있으면 중복 방지
        if code in self._vol_q:
            return
        self._vol_q.append(code)
        def _req():
            self.kiwoom.dynamicCall(
                "SetInputValue(QString,QString)", "종목코드", code)
            self.kiwoom.dynamicCall(
                "SetInputValue(QString,QString)", "틱범위", "5")
            self.kiwoom.dynamicCall(
                "SetInputValue(QString,QString)", "수정주가구분", "1")
            self.kiwoom.dynamicCall(
                "CommRqData(QString,QString,int,QString)",
                "gdjum_vol", "opt10080", 0, "0603")
        self.queue.push(_req)

    def _on_tr_vol(self, code: str, trcode: str, rqname: str):
        if code not in self.status:
            return
        s = self.status[code]
        k = self.kiwoom

        volumes = []
        for i in range(GDJUM_CANDLE_N + 1):
            try:
                v = abs(int(k.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "거래량").strip()))
                volumes.append(v)
            except Exception:
                break

        if len(volumes) < 2:
            print(f"  [전일고점] {s['name']} 거래량 데이터 부족")
            return

        curr_vol = volumes[0]
        avg_vol  = sum(volumes[1:]) / len(volumes[1:])
        ratio    = curr_vol / avg_vol if avg_vol > 0 else 0
        print(f"  [전일고점] {s['name']} 거래량 {ratio:.1f}배 (기준 {GDJUM_VOL_MULT}배)")

        if ratio >= GDJUM_VOL_MULT:
            s["vol_ok"] = True
            print(f"  [전일고점] {s['name']} 거래량 조건 통과!")
            if s["hoga_ok"]:
                self._try_enter(code)
        else:
            s["vol_ok"] = False

    # =========================================================
    # 진입 시도
    # =========================================================
    def _try_enter(self, code: str):
        if code not in self.status:
            return
        s = self.status[code]
        if s["order_sent"] or not (s["hoga_ok"] and s["vol_ok"]):
            return
        if self._is_expired(code):
            self._handle_expired(code)
            return

        entry_p   = s["entry_price"]
        max_price = s["max_price"]

        # 8% 이상 상승 후 눌림 스킵
        if entry_p > 0 and (max_price - entry_p) / entry_p >= GDJUM_RISE_SKIP:
            send_telegram(
                f"⚠️ <b>[전일고점돌파] 진입 스킵</b>\n"
                f"• {s['name']}\n"
                f"  사유: 8% 이상 상승 후 눌림 (최고가 {max_price:,}원)"
            )
            self.status.pop(code, None)
            return

        if code in tm.positions or len(tm.positions) >= MAX_POSITIONS:
            print(f"  [전일고점] {s['name']} 포지션 한도 초과 — 스킵")
            return
        if not is_m2_open():
            print(f"  [전일고점] 장외 시간 — 주문 스킵")
            return

        elapsed_min = int(
            (datetime.now() - s["enter_time"]).total_seconds() / 60)
        qty = max(1, GDJUM_ENTRY_AMT // entry_p)

        # ★ 두 조건 모두 충족 — 이 시점이 첫 텔레그램 알림
        send_telegram(
            f"🎯 <b>[전일고점돌파] 조건 충족! 주문 진입</b>\n"
            f"• <b>{s['name']}</b>\n"
            f"  전일고가: {s['prev_high']:,}원\n"
            f"  진입가: {entry_p:,}원 (-{GDJUM_TICK_DOWN}틱)\n"
            f"  수량: {qty}주  금액: {entry_p * qty:,}원\n"
            f"  편입 후 경과: {elapsed_min}분 | 모의투자"
        )

        ok = tm.enter_position(
            code, s["name"], entry_p,
            condition=GDJUM_CONDITION,
            order_type="limit",
            limit_price=entry_p,
            entry_amount=GDJUM_ENTRY_AMT,
            add_buy=False,
        )
        if ok:
            s["order_sent"] = True
            print(f"  [전일고점] 주문 전송: {s['name']} {qty}주 @ {entry_p:,}")
        else:
            print(f"  [전일고점] 주문 실패: {s['name']} (포지션 한도 또는 장외)")

    # =========================================================
    # 만료 처리
    # =========================================================
    def _is_expired(self, code: str) -> bool:
        s = self.status.get(code)
        if not s:
            return True
        return (datetime.now() - s["enter_time"]).total_seconds() / 60 > MAX_WAIT_MIN

    def _handle_expired(self, code: str):
        s = self.status.pop(code, None)
        if not s:
            return
        self._hoga_screen_map.pop(s["hoga_screen"], None)
        print(f"  [전일고점] {s['name']} 2시간 초과 → 진입 포기")
        # 두 조건을 모두 충족했던 종목만 알림 (그 외는 조용히 종료)
        if s["hoga_ok"] and s["vol_ok"]:
            send_telegram(
                f"⏰ <b>[전일고점돌파] 진입 포기</b>\n"
                f"• {s['name']}\n"
                f"  사유: 2시간 내 주문 미체결"
            )

    # =========================================================
    # 무편입 알림 타이머
    # =========================================================
    def _reset_no_entry_timer(self):
        self._no_entry_timer.stop()
        self._no_entry_timer.start(NO_ENTRY_MINUTES * 60 * 1000)

    def _on_no_entry_timeout(self):
        try:
            if os.path.exists(_NO_ENTRY_FLAG) and \
               time.time() - os.path.getmtime(_NO_ENTRY_FLAG) < _DEDUPE_SEC:
                return  # 중복 프로세스가 이미 보냄
            with open(_NO_ENTRY_FLAG, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass
        send_telegram(
            f"⚠️ <b>[전일고점돌파]</b>\n"
            f"최근 {NO_ENTRY_MINUTES}분간 리스트에 안 떴음"
        )

    # =========================================================
    # 일일 요약
    # =========================================================
    def get_daily_summary(self) -> str:
        if not self.history:
            return "<b>[전일고점돌파]</b> 당일 편입/이탈 없음"
        lines = ["<b>[전일고점돌파] 당일 이력</b>"]
        for e in self.history:
            icon = "📌" if e["action"] == "편입" else "📤"
            lines.append(f"{icon} {e['time']} {e['action']}  {e['name']}")
        enter_cnt = sum(1 for e in self.history if e["action"] == "편입")
        exit_cnt  = sum(1 for e in self.history if e["action"] == "이탈")
        lines.append(f"\n총 편입 {enter_cnt}회 | 이탈 {exit_cnt}회")
        return "\n".join(lines)

    # =========================================================
    # 내부 유틸
    # =========================================================
    def _alloc_hoga_screen(self) -> str:
        self._hoga_screen_counter += 1
        if self._hoga_screen_counter > 699:
            self._hoga_screen_counter = HOGA_SCREEN_BASE
        return str(self._hoga_screen_counter).zfill(4)
