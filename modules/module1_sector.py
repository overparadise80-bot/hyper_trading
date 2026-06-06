# -*- coding: utf-8 -*-
"""
module1_sector.py - 주도섹터 + 52주신고가 브리핑
- 9:10~15:10 / 15분 주기
- 업종 TOP7 + 주도주 TOP4 (등락률 조건 없음, 거래대금 표시)
- 테마 TOP7 + 주도주 TOP4
- 52주신고가 (양봉 or 시=저, 주도섹터 포함 시 불꽃)
"""

import json
from datetime import datetime
from PyQt5.QtCore import QTimer
from modules.common import *

# sector_map.json 로드
with open("sector_map.json", "r", encoding="utf-8") as f:
    SECTOR_MAP = json.load(f)

UPJONG_MAP = {
    "005": "음식료품", "006": "섬유의복", "007": "종이목재",
    "008": "화학",     "009": "의약품",   "010": "비금속광물",
    "011": "철강금속", "012": "기계",     "013": "전기전자",
    "014": "의료정밀", "015": "운수장비", "016": "유통업",
    "017": "전기가스업","018": "건설업",  "019": "운수창고",
    "020": "통신업",   "021": "금융업",   "022": "은행",
    "024": "증권",     "025": "보험",     "026": "서비스업",
}
UPJONG_CODES = list(UPJONG_MAP.keys())

class Module1Sector:
    def __init__(self, kiwoom):
        self.kiwoom = kiwoom
        self.reset()

    def reset(self):
        self.upjong_data          = {}
        self.upjong_idx           = 0
        self.upjong_stock_queue   = []
        self.upjong_stock_idx     = 0
        self.upjong_stock_results = {}  # {업종코드: [{name, rate, price, amount}]}
        self.theme_stock_map      = {}
        self.stock_name_map       = {}
        self.stock_rate_map       = {}  # {코드: {rate, price, amount}}
        self.theme_scan_queue     = []
        self.theme_scan_idx       = 0
        self.shingoga_codes       = []
        self.shingoga_detail      = {}
        self.shingoga_idx         = 0
        self.top7_upjong_codes    = []

    # ==========================================================
    # 외부 진입점
    # ==========================================================
    def start_scan(self):
        self.reset()
        print("\n[모듈1] 업종 스캔 시작...")
        self._connect(self._on_tr_upjong)
        QTimer.singleShot(500, lambda: self._scan_upjong(0))

    def _connect(self, slot):
        try:
            self.kiwoom.OnReceiveTrData.disconnect()
        except:
            pass
        self.kiwoom.OnReceiveTrData.connect(slot)

    # ==========================================================
    # PHASE1: 업종 등락률
    # ==========================================================
    def _scan_upjong(self, idx):
        self.upjong_idx = idx
        if idx >= len(UPJONG_CODES):
            self._phase1_done()
            return
        code = UPJONG_CODES[idx]
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "업종코드", code)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "업종현재가요청", "opt20001", 0, "0101")

    def _on_tr_upjong(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "업종현재가요청":
            return
        code = UPJONG_CODES[self.upjong_idx]
        rate = self.kiwoom.dynamicCall(
            "GetCommData(QString,QString,int,QString)",
            trcode, rqname, 0, "등락률").strip()
        try:
            self.upjong_data[code] = {
                "name": UPJONG_MAP[code],
                "rate": float(rate)
            }
        except:
            pass
        QTimer.singleShot(300, lambda: self._scan_upjong(self.upjong_idx + 1))

    def _phase1_done(self):
        sorted_u = sorted(self.upjong_data.items(),
                          key=lambda x: x[1]['rate'], reverse=True)
        self.top7_upjong_codes = [c for c, _ in sorted_u[:M1_SECTOR_TOP]]
        print(f"  TOP7: {[self.upjong_data[c]['name'] for c in self.top7_upjong_codes]}")

        for uc in self.top7_upjong_codes:
            for sc in SECTOR_MAP.get(uc, []):
                self.upjong_stock_queue.append((uc, sc))

        print(f"[모듈1] 업종 종목 스캔 ({len(self.upjong_stock_queue)}개)...")
        self._connect(self._on_tr_upjong_stock)
        QTimer.singleShot(300, lambda: self._scan_upjong_stock(0))

    # ==========================================================
    # PHASE2: 업종별 종목 (거래대금 포함)
    # ==========================================================
    def _scan_upjong_stock(self, idx):
        self.upjong_stock_idx = idx
        if idx >= len(self.upjong_stock_queue):
            self._phase2_done()
            return
        uc, sc = self.upjong_stock_queue[idx]
        seen = set()
        if sc in seen:
            QTimer.singleShot(0, lambda: self._scan_upjong_stock(idx + 1))
            return
        seen.add(sc)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", sc)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "주식기본정보요청", "opt10001", 0, "0103")

    def _on_tr_upjong_stock(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "주식기본정보요청":
            return
        uc, sc = self.upjong_stock_queue[self.upjong_stock_idx]
        k = self.kiwoom
        name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "종목명").strip()
        rate_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "등락율").strip()
        price_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "현재가").strip()
        amt_str   = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "거래대금").strip()
        try:
            rate   = float(rate_str)
            price  = abs(int(price_str))
            amount = abs(int(amt_str)) // 100000000  # 억원 단위
            self.upjong_stock_results.setdefault(uc, []).append({
                "name": name, "rate": rate,
                "price": price, "amount": amount
            })
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_upjong_stock(self.upjong_stock_idx + 1))

    def _phase2_done(self):
        print("[모듈1] 테마 스캔...")
        self._connect(self._on_tr_theme)
        QTimer.singleShot(300, self._build_theme_map)

    # ==========================================================
    # PHASE3: 테마
    # ==========================================================
    def _build_theme_map(self):
        result = self.kiwoom.dynamicCall("GetThemeGroupList(int)", 0)
        themes = [t for t in result.split(';') if t]
        for theme in themes:
            parts = theme.split('|')
            if len(parts) < 2:
                continue
            code, name = parts[0], parts[1]
            stocks_raw = self.kiwoom.dynamicCall("GetThemeGroupCode(QString)", code)
            codes = [s.replace('A', '') for s in stocks_raw.split(';') if s]
            for sc in codes:
                if sc not in self.stock_name_map:
                    self.stock_name_map[sc] = self.kiwoom.dynamicCall(
                        "GetMasterCodeName(QString)", sc)
                self.theme_scan_queue.append((name, sc))
            self.theme_stock_map[name] = codes
        print(f"  테마 대상: {len(self.theme_scan_queue)}개")
        QTimer.singleShot(300, lambda: self._scan_theme_stock(0))

    def _scan_theme_stock(self, idx):
        self.theme_scan_idx = idx
        if idx >= len(self.theme_scan_queue):
            self._phase3_done()
            return
        _, sc = self.theme_scan_queue[idx]
        if sc in self.stock_rate_map:
            QTimer.singleShot(0, lambda: self._scan_theme_stock(idx + 1))
            return
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", sc)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "테마주식정보요청", "opt10001", 0, "0104")

    def _on_tr_theme(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "테마주식정보요청":
            return
        _, sc     = self.theme_scan_queue[self.theme_scan_idx]
        k         = self.kiwoom
        rate_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "등락율").strip()
        price_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "현재가").strip()
        amt_str   = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "거래대금").strip()
        try:
            self.stock_rate_map[sc] = {
                "rate":   float(rate_str),
                "price":  abs(int(price_str)),
                "amount": abs(int(amt_str)) // 100000000
            }
        except:
            self.stock_rate_map[sc] = {"rate": 0.0, "price": 0, "amount": 0}
        if self.theme_scan_idx % 100 == 0:
            print(f"  테마 진행: {self.theme_scan_idx}/{len(self.theme_scan_queue)}")
        QTimer.singleShot(200, lambda: self._scan_theme_stock(self.theme_scan_idx + 1))

    def _phase3_done(self):
        print("[모듈1] 52주신고가 조건검색...")
        try:
            self.kiwoom.OnReceiveTrData.disconnect()
        except:
            pass
        try:
            self.kiwoom.OnReceiveTrCondition.disconnect(self._on_shingoga_condition)
        except:
            pass
        self.kiwoom.OnReceiveTrCondition.connect(self._on_shingoga_condition)
        QTimer.singleShot(300, self._run_shingoga)

    # ==========================================================
    # PHASE4: 52주신고가
    # ==========================================================
    def _run_shingoga(self, condition_list: dict = None):
        self._condition_list = condition_list
        if not hasattr(self, '_condition_list') or not self._condition_list:
            print("  52주신고가 조건식 없음")
            self._phase4_done([])
            return
        if "52주신고가" not in self._condition_list:
            self._phase4_done([])
            return
        idx = self._condition_list["52주신고가"]
        self.kiwoom.dynamicCall("SendCondition(QString, QString, int, int)",
                                "0201", "52주신고가", int(idx), 0)

    def set_condition_list(self, condition_list: dict):
        self._condition_list = condition_list

    def _on_shingoga_condition(self, screen, code_list, condition_name, idx, prev_next):
        if condition_name != "52주신고가":
            return
        codes = [c for c in code_list.split(';') if c]
        print(f"  52주신고가 {len(codes)}개 수신")
        try:
            self.kiwoom.OnReceiveTrCondition.disconnect(self._on_shingoga_condition)
        except:
            pass
        self._phase4_done(codes)

    def _phase4_done(self, codes):
        self.shingoga_codes = codes
        self._connect(self._on_tr_shingoga)
        QTimer.singleShot(300, lambda: self._scan_shingoga(0))

    def _scan_shingoga(self, idx):
        self.shingoga_idx = idx
        if idx >= len(self.shingoga_codes):
            self._phase5_done()
            return
        code = self.shingoga_codes[idx]
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                "신고가상세요청", "opt10001", 0, "0105")

    def _on_tr_shingoga(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "신고가상세요청":
            return
        code = self.shingoga_codes[self.shingoga_idx]
        k    = self.kiwoom
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
        amt_str   = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "거래대금").strip()
        try:
            price  = abs(int(price_str))
            open_p = abs(int(open_str))
            low_p  = abs(int(low_str))
            self.shingoga_detail[code] = {
                "name":        name,
                "price":       price,
                "open":        open_p,
                "low":         low_p,
                "rate":        float(rate_str),
                "amount":      abs(int(amt_str)) // 100000000,
                "is_yangbong": price > open_p,
                "is_sijeo":    open_p == low_p,
            }
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_shingoga(self.shingoga_idx + 1))

    def _phase5_done(self):
        try:
            self.kiwoom.OnReceiveTrData.disconnect()
        except:
            pass
        print("[모듈1] 브리핑 생성...")
        self._build_and_send()

    # ==========================================================
    # 브리핑 생성
    # ==========================================================
    def _build_and_send(self):
        now = datetime.now().strftime("%m/%d %H:%M")

        sorted_u = sorted(self.upjong_data.items(),
                          key=lambda x: x[1]['rate'], reverse=True)
        top7_u   = [c for c, _ in sorted_u[:M1_SECTOR_TOP]]

        # 테마 TOP7
        theme_perf = []
        for tname, codes in self.theme_stock_map.items():
            valid = [
                {"name": self.stock_name_map.get(sc, sc),
                 "rate": self.stock_rate_map[sc]["rate"],
                 "price": self.stock_rate_map[sc]["price"],
                 "amount": self.stock_rate_map[sc]["amount"]}
                for sc in codes if sc in self.stock_rate_map
            ]
            if valid:
                avg = sum(s["rate"] for s in valid) / len(valid)
                theme_perf.append({
                    "theme": tname, "avg": avg,
                    "stocks": sorted(valid, key=lambda x: x["rate"], reverse=True)
                })
        top7_t = sorted(theme_perf, key=lambda x: x["avg"], reverse=True)[:M1_SECTOR_TOP]

        # 52주신고가 필터
        sector_stock_set = set()
        for uc in self.top7_upjong_codes:
            sector_stock_set.update(SECTOR_MAP.get(uc, []))

        filtered = []
        for code, d in self.shingoga_detail.items():
            if d["is_yangbong"] or d["is_sijeo"]:
                filtered.append({
                    "code":      code,
                    "name":      d["name"],
                    "price":     d["price"],
                    "rate":      d["rate"],
                    "amount":    d["amount"],
                    "is_sijeo":  d["is_sijeo"],
                    "in_sector": code in sector_stock_set,
                })
        filtered = sorted(filtered, key=lambda x: x["rate"], reverse=True)[:15]

        # ── 메시지1: 업종 주도섹터
        msg1  = f"<b>업종 주도섹터 TOP7</b> ({now})\n"
        msg1 += "등락률 | 거래대금(억) 기준\n"
        msg1 += "--------------------\n\n"
        for i, uc in enumerate(top7_u, 1):
            d      = self.upjong_data[uc]
            stocks = sorted(
                self.upjong_stock_results.get(uc, []),
                key=lambda x: x['rate'], reverse=True
            )
            e = "1위" if i==1 else "2위" if i==2 else "3위" if i==3 else f"{i}위"
            msg1 += f"{e} <b>{d['name']}</b> ({d['rate']:+.2f}%)\n"
            for s in stocks[:M1_STOCK_TOP]:
                msg1 += (f"  • {s['name']}  "
                         f"<b>{s['rate']:+.2f}%</b>  "
                         f"{s['price']:,}원  "
                         f"{s['amount']}억\n")
            if not stocks:
                msg1 += "  • 종목 없음\n"
            msg1 += "\n"

        # ── 메시지2: 테마 주도섹터
        msg2  = f"<b>테마 주도섹터 TOP7</b> ({now})\n"
        msg2 += "등락률 | 거래대금(억) 기준\n"
        msg2 += "--------------------\n\n"
        for i, t in enumerate(top7_t, 1):
            e = "1위" if i==1 else "2위" if i==2 else "3위" if i==3 else f"{i}위"
            msg2 += f"{e} <b>{t['theme']}</b> (평균 {t['avg']:+.2f}%)\n"
            for s in t["stocks"][:M1_STOCK_TOP]:
                msg2 += (f"  • {s['name']}  "
                         f"<b>{s['rate']:+.2f}%</b>  "
                         f"{s['price']:,}원  "
                         f"{s['amount']}억\n")
            msg2 += "\n"

        # ── 메시지3: 52주신고가
        msg3  = f"<b>52주 신고가</b> ({now})\n"
        msg3 += "양봉 | 불=주도섹터 | 거래대금(억)\n"
        msg3 += "--------------------\n\n"
        for s in filtered:
            fire  = "🔥" if s["in_sector"] else "  "
            sijeo = " <b>[시=저]</b>" if s["is_sijeo"] else ""
            msg3 += (f"{fire} {s['name']}"
                     f"  <b>{s['rate']:+.2f}%</b>"
                     f"  {s['price']:,}원"
                     f"  {s['amount']}억{sijeo}\n")
        if not filtered:
            msg3 += "해당 종목 없음\n"

        print("  텔레그램 전송...")
        send_telegram(msg1)
        QTimer.singleShot(1500, lambda: send_telegram(msg2))
        QTimer.singleShot(3000, lambda: send_telegram(msg3))
        print(f"  모듈1 브리핑 완료! [{now}]")
