# -*- coding: utf-8 -*-
"""
module2_gdjum.py - 단타검색식전일고점돌파 자동매매
- 호가 조건: 전일고점 ±4호가 각 5천만원 이상 (1억 → 5천만 하향)
- 거래량 조건: 돌파캔들 >= 60캔들 평균 × 2배
- 8% 이상 상승 후 눌림 스킵
- 편입 후 2시간 초과 시 진입 포기
- 전일고점 -4틱 지정가 진입
"""

from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import *
from modules import trade_manager as tm

# ★ 호가 잔량 기준 (1억 → 5천만)
GDJUM_TICK_MIN_NEW = 5e7   # 5천만원

# ★ 편입 후 최대 대기 시간 (분)
GDJUM_MAX_WAIT_MIN = 120   # 2시간

class Module2Gdjum:
    def __init__(self, kiwoom):
        self.kiwoom    = kiwoom
        self.status    = {}
        self.vol_queue = []
        self.vol_idx   = 0

    def on_enter(self, code: str, now_str: str):
        """조건검색 편입 시 호출"""
        if code in tm.positions:
            return
        if len(tm.positions) >= MAX_POSITIONS:
            return
        print(f"  [전일고점] 편입: {code}")
        self.status[code] = {
            "prev_high":   0,
            "tick_size":   0,
            "entry_price": 0,
            "max_price":   0,
            "hoga_ok":     False,
            "vol_ok":      False,
            "order_sent":  False,
            "name":        "",
            "screen":      tm.next_screen(),
            "enter_time":  datetime.now(),  # ★ 편입 시각 기록
        }
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_basic)
        except:
            pass
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_basic)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "전일고점기본정보", "opt10001", 0, "0601")

    def on_exit(self, code: str, now_str: str):
        name = self.status.get(code, {}).get("name", code)
        self.status.pop(code, None)
        send_telegram(f"<b>조건검색 이탈</b> ({now_str})\n전일고점돌파\n• {name}")

    def _on_tr_basic(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "전일고점기본정보":
            return
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_basic)
        except:
            pass
        code = next((c for c, s in self.status.items()
                     if s["prev_high"] == 0 and s["name"] == ""), None)
        if not code:
            return
        k         = self.kiwoom
        name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "종목명").strip()
        price_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "현재가").strip()
        high_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "전일고가").strip()
        try:
            price     = abs(int(price_str))
            prev_high = abs(int(high_str))
            tick      = get_tick_size(prev_high)
            entry_p   = prev_high - (tick * GDJUM_TICK_DOWN)

            s = self.status[code]
            s["name"]        = name
            s["prev_high"]   = prev_high
            s["tick_size"]   = tick
            s["entry_price"] = entry_p
            s["max_price"]   = price

            print(f"  [전일고점] {name} 전일고가:{prev_high:,} 진입예정:{entry_p:,}")

            self._subscribe_hoga(code)

            self.vol_queue.append(code)
            if len(self.vol_queue) == 1:
                QTimer.singleShot(300, lambda: self._fetch_vol(0))

            send_telegram(
                f"<b>[전일고점돌파] 편입 감지!</b>\n"
                f"• {name}\n"
                f"  전일고가: {prev_high:,}원\n"
                f"  진입예정: {entry_p:,}원 (-{GDJUM_TICK_DOWN}틱)\n"
                f"  ⏱ 2시간 내 타점 미도달 시 포기\n"
                f"  조건 확인 중..."
            )
        except Exception as e:
            print(f"  [전일고점] 기본정보 오류: {e}")
            self.status.pop(code, None)

    def _subscribe_hoga(self, code: str):
        s      = self.status[code]
        screen = s["screen"]
        fids   = ";".join([str(f) for f in range(41, 81)])
        self.kiwoom.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            screen, code, fids, "1"
        )

    def _is_expired(self, code: str) -> bool:
        """편입 후 2시간 초과 여부 체크"""
        s = self.status.get(code)
        if not s:
            return True
        elapsed = (datetime.now() - s["enter_time"]).total_seconds() / 60
        return elapsed > GDJUM_MAX_WAIT_MIN

    def on_realtime_hoga(self, code: str, real_type: str):
        if code not in self.status:
            return
        s = self.status[code]
        if s["hoga_ok"] or s["order_sent"]:
            return

        # ★ 2시간 초과 체크
        if self._is_expired(code):
            name = s.get("name", code)
            print(f"  [전일고점] {name} 2시간 초과 → 진입 포기")
            send_telegram(
                f"<b>[전일고점돌파] 진입 포기</b>\n"
                f"• {name}\n"
                f"  사유: 편입 후 2시간 내 타점 미도달"
            )
            self.status.pop(code, None)
            return

        prev_high = s["prev_high"]
        tick      = s["tick_size"]
        if prev_high == 0:
            return

        targets = [
            prev_high - tick,
            prev_high,
            prev_high + tick,
            prev_high + tick * 2,
        ]

        all_ok = True
        for target in targets:
            found = False
            for i, fid in enumerate(range(71, 81)):
                try:
                    hp = abs(int(self.kiwoom.dynamicCall(
                        "GetCommRealData(QString, int)", real_type, fid).strip()))
                    hq = abs(int(self.kiwoom.dynamicCall(
                        "GetCommRealData(QString, int)", real_type, 61+i).strip()))
                    if hp == target:
                        found = True
                        # ★ 기준 5천만원으로 변경
                        if hp * hq < GDJUM_TICK_MIN_NEW:
                            all_ok = False
                        break
                except:
                    pass
            if not found:
                all_ok = False

        if all_ok:
            s["hoga_ok"] = True
            print(f"  [전일고점] {s['name']} 호가조건 통과!")
            self._try_enter(code)

    def on_realtime_price(self, code: str, price: int):
        if code not in self.status:
            return
        s = self.status[code]
        if price > s["max_price"]:
            s["max_price"] = price

    def _fetch_vol(self, idx: int):
        self.vol_idx = idx
        if idx >= len(self.vol_queue):
            self.vol_queue.clear()
            return
        code = self.vol_queue[idx]
        if code not in self.status:
            QTimer.singleShot(300, lambda: self._fetch_vol(idx+1))
            return
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_vol)
        except:
            pass
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_vol)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "틱범위", "5")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "전일고점분봉조회", "opt10080", 0, "0602")

    def _on_tr_vol(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "전일고점분봉조회":
            return
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_vol)
        except:
            pass
        if self.vol_idx >= len(self.vol_queue):
            return
        code = self.vol_queue[self.vol_idx]
        if code not in self.status:
            return
        s = self.status[code]

        volumes = []
        for i in range(GDJUM_CANDLE_N + 1):
            try:
                v = abs(int(self.kiwoom.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "거래량").strip()))
                volumes.append(v)
            except:
                break

        if len(volumes) >= 2:
            curr_vol = volumes[0]
            avg_vol  = sum(volumes[1:]) / len(volumes[1:])
            ratio    = curr_vol / avg_vol if avg_vol > 0 else 0
            print(f"  [거래량] {s['name']} {ratio:.1f}배")
            if ratio >= GDJUM_VOL_MULT:
                s["vol_ok"] = True
                print(f"  [전일고점] {s['name']} 거래량조건 통과!")
                self._try_enter(code)

        QTimer.singleShot(300, lambda: self._fetch_vol(self.vol_idx+1))

    def _try_enter(self, code: str):
        if code not in self.status:
            return
        s = self.status[code]
        if s["order_sent"]:
            return
        if not (s["hoga_ok"] and s["vol_ok"]):
            return

        # ★ 2시간 초과 체크
        if self._is_expired(code):
            name = s.get("name", code)
            send_telegram(
                f"<b>[전일고점돌파] 진입 포기</b>\n"
                f"• {name}\n"
                f"  사유: 편입 후 2시간 내 타점 미도달"
            )
            self.status.pop(code, None)
            return

        # 8% 이상 상승 후 눌림 체크
        entry_p   = s["entry_price"]
        max_price = s["max_price"]
        if entry_p > 0 and (max_price - entry_p) / entry_p >= GDJUM_RISE_SKIP:
            send_telegram(
                f"<b>[전일고점돌파] 진입 스킵</b>\n"
                f"• {s['name']}  8% 이상 상승 후 눌림"
            )
            self.status.pop(code, None)
            return

        if code in tm.positions or len(tm.positions) >= MAX_POSITIONS:
            return
        if not is_m2_open():
            return

        qty = calc_qty(entry_p)
        ok  = tm.enter_position(
            code, s["name"], entry_p,
            condition=GDJUM_CONDITION,
            order_type="limit",
            limit_price=entry_p
        )
        if ok:
            s["order_sent"] = True
            elapsed_min = int(
                (datetime.now() - s["enter_time"]).total_seconds() / 60
            )
            send_telegram(
                f"<b>[전일고점돌파] 지정가 매수!</b>\n"
                f"• {s['name']}\n"
                f"  전일고가: {s['prev_high']:,}원\n"
                f"  진입가: {entry_p:,}원 (-{GDJUM_TICK_DOWN}틱)\n"
                f"  수량: {qty}주  금액: {entry_p*qty:,}원\n"
                f"  편입 후 경과: {elapsed_min}분\n"
                f"  호가: OK | 거래량: OK"
            )