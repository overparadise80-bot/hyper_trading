# -*- coding: utf-8 -*-
"""
module3_closing.py - 종가베팅
- 15:18 1회 스캔
- 급등후조정반등 조건식 + 등락률 5% 미만 + 양봉 + 윗꼬리 필터
- 기관 순매수 10거래일 중 5일 이상
- 최대 5종목 종가 진입
"""

from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import *
from modules import trade_manager as tm

class Module3Closing:
    def __init__(self, kiwoom, condition_list: dict):
        self.kiwoom         = kiwoom
        self.condition_list = condition_list
        self.timer          = QTimer()
        self.reset()

    def reset(self):
        self.candidate_codes = []
        self.candidate_idx   = 0
        self.candidate_data  = {}
        self.inst_queue      = []
        self.inst_idx        = 0
        self.inst_days_data  = {}
        self.final_list      = []

    def setup_timer(self):
        now    = datetime.now()
        target = now.replace(hour=M3_SCAN_HOUR, minute=M3_SCAN_MINUTE,
                             second=0, microsecond=0)
        if now >= target:
            print("15:18 이미 지남 - 모듈3 스킵")
            return
        ms = int((target - now).total_seconds() * 1000)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.start_scan)
        self.timer.start(ms)
        print(f"모듈3 15:18 타이머 설정 ({ms//1000}초 후)")

    def start_scan(self):
        if datetime.now().weekday() >= 5:
            return
        self.reset()
        print("\n[모듈3] 15:18 종가베팅 스캔 시작...")
        send_telegram("<b>모듈3 종가베팅 스캔 시작</b> (15:18)")

        if M3_CONDITION not in self.condition_list:
            send_telegram(f"모듈3: '{M3_CONDITION}' 조건식 없음")
            return

        try:
            self.kiwoom.OnReceiveTrCondition.disconnect(self._on_condition)
        except:
            pass
        self.kiwoom.OnReceiveTrCondition.connect(self._on_condition)
        cidx = self.condition_list[M3_CONDITION]
        self.kiwoom.dynamicCall("SendCondition(QString, QString, int, int)",
                                "0301", M3_CONDITION, int(cidx), 0)

    def _on_condition(self, screen, code_list, condition_name, idx, prev_next):
        if condition_name != M3_CONDITION:
            return
        try:
            self.kiwoom.OnReceiveTrCondition.disconnect(self._on_condition)
        except:
            pass
        self.candidate_codes = [c for c in code_list.split(';') if c]
        print(f"  조건식 결과: {len(self.candidate_codes)}개")
        if not self.candidate_codes:
            self._send_empty_briefing("조건검색 결과 없음")
            return
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_basic)
        except:
            pass
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_basic)
        QTimer.singleShot(300, lambda: self._scan_basic(0))

    def _scan_basic(self, idx: int):
        self.candidate_idx = idx
        if idx >= len(self.candidate_codes):
            self._basic_done()
            return
        code = self.candidate_codes[idx]
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "모듈3기본정보", "opt10001", 0, "0701")

    def _on_tr_basic(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "모듈3기본정보":
            return
        code = self.candidate_codes[self.candidate_idx]
        k    = self.kiwoom
        name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "종목명").strip()
        price_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "현재가").strip()
        open_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "시가").strip()
        high_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "고가").strip()
        rate_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "등락율").strip()
        try:
            price  = abs(int(price_str))
            open_p = abs(int(open_str))
            high_p = abs(int(high_str))
            rate   = float(rate_str)

            if rate >= M3_RATE_LIMIT:
                pass
            elif price <= open_p:
                pass
            else:
                body = price - open_p
                tail = high_p - price
                if body > 0 and tail < body * M3_TAIL_MULT:
                    self.candidate_data[code] = {
                        "name": name, "price": price, "rate": rate
                    }
                    print(f"  [통과] {name} {rate:+.2f}%")
        except Exception as e:
            print(f"  [오류] {code}: {e}")

        QTimer.singleShot(200, lambda: self._scan_basic(self.candidate_idx+1))

    def _basic_done(self):
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_basic)
        except:
            pass
        passed = list(self.candidate_data.keys())
        if not passed:
            self._send_empty_briefing("기본 필터 통과 종목 없음\n(등락률 5%미만 + 양봉 + 윗꼬리 조건)")
            return
        self.inst_queue = passed
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_inst)
        except:
            pass
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_inst)
        QTimer.singleShot(300, lambda: self._scan_inst(0))

    def _scan_inst(self, idx: int):
        self.inst_idx = idx
        if idx >= len(self.inst_queue):
            self._inst_done()
            return
        code = self.inst_queue[idx]
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "금액수량구분", "1")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "매매구분", "0")
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "모듈3기관조회", "opt10059", 0, "0702")

    def _on_tr_inst(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "모듈3기관조회":
            return
        code = self.inst_queue[self.inst_idx]
        days = 0
        for i in range(M3_INST_DAYS):
            try:
                v = int(self.kiwoom.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "기관계").strip().replace(',','').replace('+',''))
                if v > 0:
                    days += 1
            except:
                break
        self.inst_days_data[code] = days
        print(f"  기관 {self.candidate_data[code]['name']}: {days}일")
        QTimer.singleShot(300, lambda: self._scan_inst(self.inst_idx+1))

    def _inst_done(self):
        try:
            self.kiwoom.OnReceiveTrData.disconnect(self._on_tr_inst)
        except:
            pass
        passed = sorted(
            [c for c in self.inst_queue if self.inst_days_data.get(c,0) >= M3_INST_MIN],
            key=lambda c: self.candidate_data[c]["rate"]
        )[:M3_MAX_STOCKS]

        if not passed:
            self._send_empty_briefing(f"기관 순매수 {M3_INST_MIN}일+ 조건 통과 종목 없음")
            return

        self.final_list = passed
        self._enter_all()

    def _send_empty_briefing(self, reason: str):
        now = datetime.now().strftime("%m/%d %H:%M")
        msg = (f"<b>📊 종가베팅 브리핑</b>  {now}\n"
               f"━━━━━━━━━━━━━━━━━━━━\n\n"
               f"해당 종목 없음\n"
               f"<i>{reason}</i>")
        print(f"[모듈3] {reason}")
        send_telegram(msg)

    def _enter_all(self):
        now = datetime.now().strftime("%m/%d %H:%M")
        msg = f"<b>종가베팅 진입</b> ({now})\n기관순매수 {M3_INST_MIN}일+\n--------------------\n\n"

        for i, code in enumerate(self.final_list):
            d   = self.candidate_data[code]
            qty = calc_qty(d["price"])
            msg += (f"• <b>{d['name']}</b>\n"
                    f"  {d['price']:,}원  {d['rate']:+.2f}%  {qty}주\n"
                    f"  기관순매수 {self.inst_days_data[code]}일/{M3_INST_DAYS}거래일\n\n")
            delay = i * 500
            c = code
            QTimer.singleShot(delay, lambda code=c: self._send_order(code))

        send_telegram(msg)

    def _send_order(self, code: str):
        if code not in self.candidate_data:
            return
        d   = self.candidate_data[code]
        ok  = tm.enter_position(
            code, d["name"], d["price"],
            condition="종가베팅",
            order_type="market"
        )
        if ok and code in tm.positions:
            tm.positions[code]["is_overnight"] = True
