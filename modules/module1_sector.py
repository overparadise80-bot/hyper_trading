# -*- coding: utf-8 -*-
"""
module1_sector.py v3 - 주도섹터 + 52주신고가 브리핑
- [PHASE1+2] 전체 종목 현재가/등락률/거래대금 TR 조회
- [PHASE3]   테마별 단순 평균 등락률 집계 → TOP7
- [PHASE4]   프로그램 매매 조회 (opt10059)
- [PHASE5]   52주신고가 조건검색 + 상세 조회
- [PHASE6]   브리핑 생성 + HTML 갱신 + 텔레그램 전송
- ★ 매 15분마다 ngrok URL 텔레그램 자동 전송
"""

import json
import os
import requests as req
from datetime import datetime, timedelta
from PyQt5.QtCore import QTimer
from modules.common import *
from screenshot_sender import send_screenshot_to_telegram

with open("theme_map_v2.json", "r", encoding="utf-8") as f:
    THEME_MAP = json.load(f)

with open("code_to_themes_v2.json", "r", encoding="utf-8") as f:
    CODE_TO_THEMES = json.load(f)

ALL_CODES        = list(CODE_TO_THEMES.keys())
THEME_MIN_STOCKS = 5
THEME_TOP_N      = 7
THEME_STOCK_TOP  = 5
HTML_OUTPUT      = "monitor.html"


class Module1Sector:

    def __init__(self, kiwoom, queue):
        self.kiwoom = kiwoom
        self._queue = queue
        self._condition_list = {}
        self._tr_handler   = None
        self._cond_handler = None
        self.mod5 = None   # Module5Sonsugun 참조 (set_sonsugun으로 주입)
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_dispatch)
        self.kiwoom.OnReceiveTrCondition.connect(self._on_cond_dispatch)
        self.reset()

    def set_sonsugun(self, mod5):
        self.mod5 = mod5

    def set_condition_list(self, condition_list: dict):
        self._condition_list = condition_list

    def reset(self):
        self.stock_data      = {}
        self._batches        = []
        self.scan_idx        = 0
        self.theme_ranking   = []
        self.prog_queue      = []
        self.prog_idx        = 0
        self._prog_done      = False
        self.shingoga_codes  = []
        self.shingoga_detail = {}
        self.shingoga_idx    = 0
        self._tr_handler     = None
        self._on_complete    = None

    # ==========================================================
    # 외부 진입점
    # ==========================================================
    def start_scan(self, on_complete=None):
        self.reset()
        self._on_complete = on_complete
        print(f"\n[모듈1] 스캔 시작 - 대상 {len(ALL_CODES)}종목")
        self._tr_handler = self._on_kw_data
        self._batches = [ALL_CODES[i:i+100] for i in range(0, len(ALL_CODES), 100)]
        QTimer.singleShot(300, lambda: self._scan_batch(0))

    def _on_tr_dispatch(self, screen, rqname, trcode, recordname, prev_next, *args):
        if self._tr_handler:
            self._tr_handler(screen, rqname, trcode, recordname, prev_next, *args)

    def _on_cond_dispatch(self, screen, code_list, condition_name, idx, prev_next):
        if self._cond_handler:
            self._cond_handler(screen, code_list, condition_name, idx, prev_next)

    # ==========================================================
    # PHASE1+2: CommKwRqData 배치 조회 (100종목씩)
    # ==========================================================
    def _scan_batch(self, idx):
        self.scan_idx = idx
        if idx >= len(self._batches):
            self._phase2_done()
            return
        batch = self._batches[idx]
        print(f"  배치 스캔: {idx * 100}/{len(ALL_CODES)} ({idx + 1}/{len(self._batches)}배치)")
        self._queue.push(lambda b=batch: self.kiwoom.dynamicCall(
            "CommKwRqData(QString, bool, int, int, QString, QString)",
            ";".join(b), False, len(b), 0, "복수종목조회", "0301"
        ))

    def _on_kw_data(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "복수종목조회":
            return
        cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
        k   = self.kiwoom
        for i in range(cnt):
            try:
                code   = k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "종목코드").strip()
                name   = k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "종목명").strip()
                rate   = float(k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "등락율").strip())
                price  = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "현재가").strip()))
                open_p = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "시가").strip() or "0"))
                high_p = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "고가").strip() or "0"))
                low_p  = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "저가").strip() or "0"))
                volume = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)", trcode, rqname, i, "거래량").strip() or "0"))
                amount = (volume * price) // 100_000_000  # 거래량×현재가 → 억원
                prev_close = int(price / (1 + rate / 100)) if rate != -100 and price > 0 else price
                self.stock_data[code] = {
                    "name": name, "rate": rate, "price": price,
                    "open": open_p, "high": high_p, "low": low_p,
                    "amount": amount, "prog": 0,
                    "prev_close": prev_close,
                }
            except:
                pass
        next_idx = self.scan_idx + 1
        self._queue.done()
        self._scan_batch(next_idx)

    # ==========================================================
    # PHASE2 완료 → 테마 집계 → 프로그램 매매 조회
    # ==========================================================
    def _phase2_done(self):
        print(f"[모듈1] 종목 스캔 완료 ({len(self.stock_data)}개). 테마 집계...")
        self._calc_theme_ranking()

        prog_codes = []
        for t in self.theme_ranking[:THEME_TOP_N]:
            for s in t["stocks"][:THEME_STOCK_TOP]:
                if s["code"] not in prog_codes:
                    prog_codes.append(s["code"])
        self.prog_queue = prog_codes
        self._prog_done = False
        self._phase4_done()

    # ==========================================================
    # PHASE3: 테마별 단순 평균 등락률 집계
    # ==========================================================
    def _calc_theme_ranking(self):
        theme_perf = []
        for tname, d in THEME_MAP.items():
            stocks = [
                {"code": c, **self.stock_data[c]}
                for c in d["codes"] if c in self.stock_data
            ]
            if len(stocks) < THEME_MIN_STOCKS:
                continue
            total_amount = sum(s["amount"] for s in stocks)
            avg_rate = sum(s["rate"] for s in stocks) / len(stocks)
            up_count = sum(1 for s in stocks if s["rate"] > 0)
            theme_perf.append({
                "theme":        tname,
                "total_amount": total_amount,
                "avg_rate":     avg_rate,
                "up_ratio":     up_count / len(stocks),
                "stock_count":  len(stocks),
                "stocks": sorted(stocks, key=lambda x: x["rate"], reverse=True),
            })
        self.theme_ranking = sorted(
            theme_perf, key=lambda x: x["avg_rate"], reverse=True)
        top3 = [t["theme"][:8] for t in self.theme_ranking[:3]]
        print(f"  테마 집계 완료. TOP3: {top3}")

    # ==========================================================
    # PHASE4: 프로그램 매매 조회 (opt10059)
    # ==========================================================
    def _scan_prog(self, idx):
        self.prog_idx = idx
        if idx >= len(self.prog_queue):
            self._phase4_done()
            return
        code = self.prog_queue[idx]
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "금액수량구분", "2")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "매매구분", "0")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "단위구분", "1")
        ret = self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "프로그램매매요청", "opt10059", 0, "0120")
        if ret != 0:
            # TR 오류 → 대기 없이 다음 종목으로
            print(f"  opt10059 오류({ret}): {code} 스킵")
            QTimer.singleShot(0, lambda: self._scan_prog(self.prog_idx + 1))

    def _on_tr_prog(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "프로그램매매요청":
            return
        code = self.prog_queue[self.prog_idx]
        k    = self.kiwoom
        try:
            buy_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                      trcode, rqname, 0, "프로그램매수수량").strip()
            sell_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                      trcode, rqname, 0, "프로그램매도수량").strip()
            net = int(buy_str.replace(',','')) - int(sell_str.replace(',',''))
            if code in self.stock_data:
                self.stock_data[code]["prog"] = net  # 순매수 주수 (양수=순매수, 음수=순매도)
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_prog(self.prog_idx + 1))

    def _prog_timeout(self):
        if not self._prog_done:
            print(f"  [경고] Phase4 타임아웃 → Phase5 강제 진행 ({self.prog_idx}/{len(self.prog_queue)})")
            self._phase4_done()

    # ==========================================================
    # PHASE4 완료 → 테마 종목 prog 값 갱신 → 52주신고가
    # ==========================================================
    def _phase4_done(self):
        if self._prog_done:
            return
        self._prog_done = True
        for t in self.theme_ranking:
            for s in t["stocks"]:
                s["prog"] = self.stock_data.get(s["code"], {}).get("prog", 0)

        self._tr_handler = None
        print("[모듈1] 52주신고가 조건검색...")
        try:
            self.kiwoom.OnReceiveTrCondition.disconnect(self._on_shingoga_condition)
        except:
            pass
        self.kiwoom.OnReceiveTrCondition.connect(self._on_shingoga_condition)
        self._run_shingoga()

    # ==========================================================
    # PHASE5: 52주신고가
    # ==========================================================
    def _run_shingoga(self):
        if not self._condition_list or "52주신고가" not in self._condition_list:
            print("  52주신고가 조건식 없음 - 스킵")
            self._phase5_done([])
            return
        idx = self._condition_list["52주신고가"]
        self._queue.push(lambda: self.kiwoom.dynamicCall(
            "SendCondition(QString, QString, int, int)",
            "0201", "52주신고가", int(idx), 0))

    def _on_shingoga_condition(self, screen, code_list, condition_name, idx, prev_next):
        if condition_name != "52주신고가":
            return
        codes = [c for c in code_list.split(';') if c]
        print(f"  52주신고가 {len(codes)}개 수신")
        try:
            self.kiwoom.OnReceiveTrCondition.disconnect(self._on_shingoga_condition)
        except:
            pass
        self._queue.done()
        self._phase5_done(codes)

    def _phase5_done(self, codes):
        self.shingoga_codes = codes
        self._tr_handler = self._on_tr_shingoga
        self._scan_shingoga(0)

    def _scan_shingoga(self, idx):
        self.shingoga_idx = idx
        if idx >= len(self.shingoga_codes):
            self._phase6_done()
            return
        code = self.shingoga_codes[idx]
        def _do(c=code):
            self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", c)
            self.kiwoom.dynamicCall("CommRqData(QString, QString, int, QString)",
                                    "신고가상세요청", "opt10001", 0, "0105")
        self._queue.push(_do)

    def _on_tr_shingoga(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "신고가상세요청":
            return
        code = self.shingoga_codes[self.shingoga_idx]
        k    = self.kiwoom
        try:
            name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                       trcode, rqname, 0, "종목명").strip()
            price     = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "현재가").strip() or "0"))
            open_p    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "시가").strip() or "0"))
            low_p     = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "저가").strip() or "0"))
            rate      = float(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                            trcode, rqname, 0, "등락율").strip() or "0")
            amount    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "거래대금").strip() or "0")) // 100000000
            self.shingoga_detail[code] = {
                "name":        name,
                "price":       price,
                "rate":        rate,
                "amount":      amount,
                "is_yangbong": price > open_p,
                "is_sijeo":    open_p > 0 and open_p == low_p,
            }
            print(f"  신고가 상세: {code} {name} {price:,}원 {rate:+.2f}%")
        except Exception as e:
            print(f"  신고가 상세 오류: {code} {e}")
        next_idx = self.shingoga_idx + 1
        self._queue.done()
        self._scan_shingoga(next_idx)

    # ==========================================================
    # PHASE6: 브리핑 생성
    # ==========================================================
    def _phase6_done(self):
        self._tr_handler = None
        print("[모듈1] 브리핑 생성...")
        self._build_and_send()

    # ==========================================================
    # ★ ngrok URL 조회
    # ==========================================================
    def _get_ngrok_url(self) -> str:
        """ngrok 터널 URL 조회 (localhost:4040 API)"""
        try:
            resp = req.get("http://localhost:4040/api/tunnels", timeout=3)
            tunnels = resp.json().get("tunnels", [])
            for t in tunnels:
                url = t.get("public_url", "")
                if url.startswith("https://"):
                    return url + "/monitor.html"
        except:
            pass
        return ""

    def _build_and_send(self):
        # 재시작 직후 중복 전송 방지: 마지막 전송 후 12분 이내면 스킵
        now_dt   = datetime.now()
        ts_file  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "logs", "m1_last_sent.txt")
        try:
            with open(ts_file, "r") as f:
                last_ts = datetime.fromisoformat(f.read().strip())
            if (now_dt - last_ts) < timedelta(minutes=12):
                elapsed = int((now_dt - last_ts).total_seconds() / 60)
                print(f"  [스킵] 마지막 전송 {elapsed}분 전 - 중복 전송 방지")
                if self._on_complete:
                    QTimer.singleShot(1000, self._on_complete)
                return
        except Exception:
            pass
        with open(ts_file, "w") as f:
            f.write(now_dt.isoformat())

        now     = now_dt.strftime("%m/%d %H:%M")
        now_sec = now_dt.strftime("%H:%M:%S")

        top_theme_codes = set()
        for t in self.theme_ranking[:THEME_TOP_N]:
            for s in t["stocks"]:
                top_theme_codes.add(s["code"])

        # 모듈5 교집합 코드 (없으면 빈 set)
        milk_codes = self.mod5.get_milk_codes() if self.mod5 else set()

        # ── 텔레그램 메시지1: 주도섹터
        medals = ["🥇","🥈","🥉","4위","5위","6위","7위"]
        msg1 = f"<b>📊 테마 주도섹터 TOP{THEME_TOP_N}</b>  {now}\n"
        msg1 += "단순 평균 등락률 기준\n"
        msg1 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, t in enumerate(self.theme_ranking[:THEME_TOP_N]):
            medal   = medals[i] if i < len(medals) else f"{i+1}위"
            amt_str = (f"{t['total_amount']//100}백억"
                       if t['total_amount'] >= 100
                       else f"{t['total_amount']}억")
            msg1 += (f"{medal} <b>{t['theme']}</b>\n"
                     f"   등락 <b>{t['avg_rate']:+.2f}%</b>  "
                     f"거래대금 {amt_str}  "
                     f"상승 {t['up_ratio']*100:.0f}%\n")
            for s in t["stocks"][:THEME_STOCK_TOP]:
                if s["price"] == 0:
                    continue
                prog_str = f"  프로그램 {s['prog']:+d}억" if s["prog"] != 0 else ""
                milk_str = "🍼" if s["code"] in milk_codes else ""
                msg1 += (f"   • {milk_str}{s['name']}  "
                         f"<b>{s['rate']:+.2f}%</b>  "
                         f"{s['price']:,}원  "
                         f"{s['amount']}억{prog_str}\n")
            msg1 += "\n"

        # ── 텔레그램 메시지2: 52주신고가 (섹터별 그룹)
        top_theme_names = {t["theme"] for t in self.theme_ranking[:THEME_TOP_N]}

        # 섹터별로 종목 그룹핑 (1종목이 여러 섹터에 속할 수 있음)
        from collections import defaultdict
        theme_groups = defaultdict(list)
        for code, d in self.shingoga_detail.items():
            themes = CODE_TO_THEMES.get(code, [])
            if themes:
                for th in themes:
                    theme_groups[th].append({
                        "name": d["name"],
                        "rate": d["rate"],
                    })
            else:
                theme_groups["기타"].append({
                    "name": d["name"],
                    "rate": d["rate"],
                })

        # 주도섹터 먼저, 이후 종목수 많은 순
        sorted_themes = sorted(
            theme_groups.keys(),
            key=lambda t: (0 if t in top_theme_names else 1, -len(theme_groups[t]))
        )

        msg2 = f"<b>🔝 52주 신고가</b>  {now}\n"
        msg2 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for th in sorted_themes:
            stocks = sorted(theme_groups[th], key=lambda x: x["rate"], reverse=True)
            fire   = "🔥 " if th in top_theme_names else ""
            msg2  += f"<b>{fire}{th}</b> (종목수: {len(stocks)})\n"
            for s in stocks:
                msg2 += f"  [{s['rate']:+.1f}%] {s['name']}\n"
            msg2 += "\n"
        if not theme_groups:
            msg2 += "해당 종목 없음\n"

        # ── HTML 파일 갱신
        self._update_html(now_sec)

        # ── 텔레그램 순차 전송
        send_telegram(msg1)
        QTimer.singleShot(2000, lambda: send_telegram(msg2))

        # ── 스크린샷 + ngrok URL 함께 텔레그램 전송
        ngrok_url = self._get_ngrok_url()
        caption = f"<b>주도섹터 모니터</b>  {now}"
        QTimer.singleShot(3000, lambda: send_screenshot_to_telegram(caption, ngrok_url=ngrok_url))

        print(f"  모듈1 완료! [{now}]")
        if self._on_complete:
            QTimer.singleShot(1000, self._on_complete)

    # ==========================================================
    # HTML 파일 갱신 (브라우저 자동 새로고침)
    # ==========================================================
    def _update_html(self, scan_time: str):
        top7 = self.theme_ranking[:THEME_TOP_N]
        milk_codes = self.mod5.get_milk_codes() if self.mod5 else set()

        sectors_js = []
        for t in top7:
            stocks_js = []
            for s in t["stocks"][:THEME_STOCK_TOP]:
                if s["price"] == 0:
                    continue
                    # 전일종가 역산 → 시가율/고가율/저가율 계산
                prev = s["price"] / (1 + s["rate"] / 100) if s["rate"] != -100 else s["price"]
                def to_rate(p):
                    return round((p - prev) / prev * 100, 2) if prev > 0 and p > 0 else 0
                stocks_js.append({
                    "code":       s["code"],
                    "name":       s["name"],
                    "price":      s["price"],
                    "prev_close": s.get("prev_close", s["price"]),
                    "rate":       round(s["rate"], 2),
                    "open_rate":  to_rate(s.get("open", 0)),
                    "high_rate":  to_rate(s.get("high", 0)),
                    "low_rate":   to_rate(s.get("low", 0)),
                    "amt":        s["amount"],
                    "prog":       s.get("prog", 0),
                    "milk":       s["code"] in milk_codes,
                })
            sectors_js.append({
                "name":     t["theme"],
                "rate":     round(t["avg_rate"], 2),
                "amt":      t["total_amount"],
                "up_ratio": round(t["up_ratio"], 2),
                "stocks":   stocks_js,
            })

        data_json = json.dumps(sectors_js, ensure_ascii=False)

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<meta http-equiv="refresh" content="900">
<title>주도섹터 모니터</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Malgun Gothic',sans-serif;background:#dceefb;padding:6px;}}
.topbar{{background:#0c447c;border-radius:8px;padding:8px 12px;display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;}}
.topbar-title{{color:#e6f1fb;font-size:14px;font-weight:600;white-space:nowrap;}}
.topbar-right{{display:flex;align-items:center;gap:10px;}}
.live-dot{{width:7px;height:7px;border-radius:50%;background:#4ade80;display:inline-block;margin-right:3px;}}
.live-label{{color:#4ade80;font-size:11px;font-weight:600;}}
.t-time{{color:#b5d4f4;font-size:12px;}}
.t-next{{color:#85b7eb;font-size:10px;}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;}}
@media(min-width:900px){{.grid{{grid-template-columns:repeat(auto-fit,minmax(300px,1fr));}}}}
.card{{background:#f0f7ff;border:1px solid #b5d4f4;border-radius:8px;overflow:hidden;}}
.card-head{{background:#185fa5;padding:9px 12px;display:flex;justify-content:space-between;align-items:center;}}
.rank-badge{{background:#0c447c;color:#b5d4f4;font-size:12px;font-weight:500;padding:2px 9px;border-radius:4px;margin-right:7px;}}
.sector-name{{color:#e6f1fb;font-size:16px;font-weight:700;}}
.sector-rate{{font-size:16px;font-weight:700;padding:3px 11px;border-radius:4px;background:#dceefb;min-width:66px;text-align:center;}}
.up-bar{{display:none;}}
.card-body{{padding:4px 10px;}}
.stock-row{{padding:7px 0;border-bottom:0.5px solid #c8e0f7;}}
.stock-row:last-child{{border-bottom:none;}}
.stock-info{{display:flex;justify-content:space-between;align-items:center;}}
.sname{{font-size:13px;color:#111;font-weight:700;max-width:100px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.sright{{display:flex;gap:5px;align-items:baseline;}}
.sprice{{font-size:12px;color:#0c447c;margin-bottom:1px;}}
.srate{{font-size:13px;font-weight:700;}}
.samt{{font-size:11px;color:#378add;}}
.candle-wrap{{position:relative;height:8px;background:#dceefb;border-radius:2px;margin:3px 0 2px;overflow:hidden;}}
.candle-fill{{position:absolute;top:1px;height:6px;border-radius:1px;}}
.candle-center{{position:absolute;left:50%;top:0;width:2px;height:8px;background:#111;transform:translateX(-50%);}}
.prog-row{{font-size:10px;height:13px;}}
.footer{{margin-top:6px;background:#0c447c;border-radius:8px;padding:6px 12px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px;}}
.footer-txt{{color:#85b7eb;font-size:10px;}}
.footer-hi{{color:#b5d4f4;font-weight:500;}}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-title">주도섹터 · 주도주</div>
  <div class="topbar-right">
    <span><span class="live-dot"></span><span class="live-label">LIVE</span></span>
    <span class="t-time" id="cur-time">--:--:--</span>
    <span class="t-next" id="nxt">다음: --분</span>
  </div>
</div>
<div class="grid" id="grid"></div>
<div class="footer">
  <span class="footer-txt">스캔: <span class="footer-hi">{scan_time}</span></span>
  <span class="footer-txt">단순 평균 · 테마 TOP7</span>
  <span class="footer-txt">PR = 프로그램 주수</span>
</div>

<script>
const SECTORS = {data_json};
const medals = ['🥇','🥈','🥉','4위','5위','6위','7위'];
const MAX_RATE = 30;

// 실시간 가격 캐시 {{코드: {{price, rate}}}}
let livePrice = {{}};

function fetchLivePrices() {{
  fetch('/prices.json?t=' + Date.now())
    .then(r => r.json())
    .then(d => {{ livePrice = d.prices || {{}}; render(); }})
    .catch(() => {{}});
}}
setInterval(fetchLivePrices, 15000);
fetchLivePrices();

function rc(r){{ return r>0?'#f5222d':r<0?'#1677ff':'#8c8c8c'; }}
function fr(r){{ return (r>0?'+':'')+r.toFixed(2)+'%'; }}
function fa(a){{
  if(a>=10000) return Math.floor(a/10000)+'조';
  if(a>=1000)  return Math.floor(a/100)+'백억';
  return a+'억';
}}
function fpr(p){{ return p.toLocaleString()+'원'; }}

function toX(r){{ return Math.max(0, Math.min(100, 50 + r / MAX_RATE * 50)); }}
function candleBar(st){{
  const c = st.rate >= st.open_rate ? '#f5222d' : '#1677ff';
  const x1 = toX(Math.min(st.open_rate, st.rate));
  const x2 = toX(Math.max(st.open_rate, st.rate));
  const xL = toX(st.low_rate);
  const xH = toX(st.high_rate);
  const bodyW = Math.max(x2 - x1, 0.5);
  return `<div class="candle-wrap">
    <div style="position:absolute;top:3px;left:${{xL}}%;width:${{xH-xL}}%;height:2px;background:${{c}};opacity:0.5"></div>
    <div style="position:absolute;top:1px;left:${{x1}}%;width:${{bodyW}}%;height:6px;background:${{c}};border-radius:1px"></div>
    <div class="candle-center"></div>
  </div>`;
}}

function progLabel(prog){{
  if(prog === 0) return '';
  const sign  = prog > 0 ? '+' : '';
  const color = prog > 0 ? '#f5222d' : '#1677ff';
  return `<div class="prog-row" style="color:${{color}}">PR ${{sign}}${{Math.abs(prog).toLocaleString()}}주</div>`;
}}

function render(){{
  document.getElementById('grid').innerHTML = SECTORS.map((sec,si)=>{{
    const upPct = Math.round(sec.up_ratio * 100);
    const rows = [...sec.stocks].sort((a,b)=>b.rate-a.rate).map((st)=>{{
      const live  = livePrice[st.code];
      const price = live ? live.price : st.price;
      const rate  = live ? live.rate  : st.rate;
      const dst   = Object.assign({{}}, st, {{price, rate}});
      const milkTag = st.milk ? '<span style="font-size:12px;margin-right:2px">🍼</span>' : '';
      return `<div class="stock-row">
        <div class="stock-info">
          <span class="sname">${{milkTag}}${{st.name}}</span>
          <div class="sright">
            <span class="srate" style="color:${{rc(rate)}}">${{fr(rate)}}</span>
            <span class="samt">${{fa(st.amt)}}</span>
          </div>
        </div>
        <span class="sprice">${{fpr(price)}}</span>
        ${{candleBar(dst)}}
        ${{progLabel(st.prog)}}
      </div>`;
    }}).join('');
    return `<div class="card">
      <div class="card-head">
        <div><span class="rank-badge">${{medals[si]||si+1+'위'}}</span><span class="sector-name">${{sec.name}}</span></div>
        <span class="sector-rate" style="color:${{rc(sec.rate)}}">${{fr(sec.rate)}}</span>
      </div>
      <div class="up-bar">
        <span>상승종목 ${{upPct}}%</span>
        <span>거래대금 ${{fa(sec.amt)}}</span>
      </div>
      <div class="card-body">${{rows}}</div>
    </div>`;
  }}).join('');
}}
render();
setInterval(render, 5000);

function tick(){{
  const now = new Date();
  document.getElementById('cur-time').textContent =
    now.toLocaleTimeString('ko-KR',{{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}});
  const min = now.getMinutes() % 15;
  const sec = now.getSeconds();
  const remain = (14 - min) * 60 + (60 - sec);
  const rm = Math.floor(remain / 60);
  const rs = remain % 60;
  document.getElementById('nxt').textContent = `다음: ${{rm}}분${{rs}}초`;
}}
tick();
setInterval(tick, 1000);
</script>
</body>
</html>"""

        with open(HTML_OUTPUT, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"  HTML 갱신: {HTML_OUTPUT}")