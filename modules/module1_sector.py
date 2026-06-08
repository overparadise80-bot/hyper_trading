# -*- coding: utf-8 -*-
"""
module1_sector.py v3 - 주도섹터 + 52주신고가 브리핑
- [PHASE1+2] 전체 종목 현재가/등락률/거래대금 TR 조회
- [PHASE3]   테마별 거래대금 가중 평균 등락률 집계 → TOP7
- [PHASE4]   프로그램 매매 조회 (opt10059)
- [PHASE5]   52주신고가 조건검색 + 상세 조회
- [PHASE6]   브리핑 생성 + HTML 갱신 + 텔레그램 전송
- ★ 매 15분마다 ngrok URL 텔레그램 자동 전송
"""

import json
import os
import requests as req
from datetime import datetime
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

    def __init__(self, kiwoom):
        self.kiwoom = kiwoom
        self._condition_list = {}
        self.reset()

    def set_condition_list(self, condition_list: dict):
        self._condition_list = condition_list

    def reset(self):
        self.stock_data      = {}
        self.scan_queue      = []
        self.scan_idx        = 0
        self.theme_ranking   = []
        self.prog_queue      = []
        self.prog_idx        = 0
        self.shingoga_codes  = []
        self.shingoga_detail = {}
        self.shingoga_idx    = 0

    # ==========================================================
    # 외부 진입점
    # ==========================================================
    def start_scan(self):
        self.reset()
        print(f"\n[모듈1] 스캔 시작 - 대상 {len(ALL_CODES)}종목")
        self._connect(self._on_tr_stock)
        self.scan_queue = list(ALL_CODES)
        QTimer.singleShot(300, lambda: self._scan_stock(0))

    def _connect(self, slot):
        try:
            self.kiwoom.OnReceiveTrData.disconnect()
        except:
            pass
        self.kiwoom.OnReceiveTrData.connect(slot)

    # ==========================================================
    # PHASE1+2: 종목별 현재가/등락률/거래대금
    # ==========================================================
    def _scan_stock(self, idx):
        self.scan_idx = idx
        if idx >= len(self.scan_queue):
            self._phase2_done()
            return
        code = self.scan_queue[idx]
        if code in self.stock_data:
            QTimer.singleShot(0, lambda: self._scan_stock(idx + 1))
            return
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "주식현재가요청", "opt10001", 0, "0110")

    def _on_tr_stock(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "주식현재가요청":
            return
        code = self.scan_queue[self.scan_idx]
        k    = self.kiwoom
        name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "종목명").strip()
        rate_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "등락율").strip()
        price_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "현재가").strip()
        amt_str   = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                   trcode, rqname, 0, "거래대금").strip()
        try:
            self.stock_data[code] = {
                "name":   name,
                "rate":   float(rate_str),
                "price":  abs(int(price_str)),
                "amount": abs(int(amt_str)) // 100000000,
                "prog":   0,
            }
        except:
            self.stock_data[code] = {
                "name": name, "rate": 0.0,
                "price": 0, "amount": 0, "prog": 0}

        if self.scan_idx % 100 == 0:
            print(f"  종목 스캔: {self.scan_idx}/{len(self.scan_queue)}")
        QTimer.singleShot(200, lambda: self._scan_stock(self.scan_idx + 1))

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
        print(f"[모듈1] 프로그램 매매 조회 ({len(self.prog_queue)}종목)...")
        self._connect(self._on_tr_prog)
        QTimer.singleShot(300, lambda: self._scan_prog(0))

    # ==========================================================
    # PHASE3: 테마별 거래대금 가중 평균 등락률 집계
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
            if total_amount > 0:
                weighted_rate = sum(
                    s["rate"] * s["amount"] for s in stocks
                ) / total_amount
            else:
                weighted_rate = sum(s["rate"] for s in stocks) / len(stocks)
            up_count = sum(1 for s in stocks if s["rate"] > 0)
            theme_perf.append({
                "theme":         tname,
                "total_amount":  total_amount,
                "weighted_rate": weighted_rate,
                "up_ratio":      up_count / len(stocks),
                "stock_count":   len(stocks),
                "stocks": sorted(stocks, key=lambda x: x["rate"], reverse=True),
            })
        self.theme_ranking = sorted(
            theme_perf, key=lambda x: x["weighted_rate"], reverse=True)
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
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "금액수량구분", "2")
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "매매구분", "0")
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "단위구분", "1")
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "프로그램매매요청", "opt10059", 0, "0120")

    def _on_tr_prog(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "프로그램매매요청":
            return
        code = self.prog_queue[self.prog_idx]
        k    = self.kiwoom
        try:
            buy_str  = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                      trcode, rqname, 0, "프로그램매수금액").strip()
            sell_str = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                      trcode, rqname, 0, "프로그램매도금액").strip()
            net = int(buy_str.replace(',','')) - int(sell_str.replace(',',''))
            if code in self.stock_data:
                self.stock_data[code]["prog"] = net
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_prog(self.prog_idx + 1))

    # ==========================================================
    # PHASE4 완료 → 테마 종목 prog 값 갱신 → 52주신고가
    # ==========================================================
    def _phase4_done(self):
        for t in self.theme_ranking:
            for s in t["stocks"]:
                s["prog"] = self.stock_data.get(s["code"], {}).get("prog", 0)

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
    # PHASE5: 52주신고가
    # ==========================================================
    def _run_shingoga(self):
        if not self._condition_list or "52주신고가" not in self._condition_list:
            print("  52주신고가 조건식 없음 - 스킵")
            self._phase5_done([])
            return
        idx = self._condition_list["52주신고가"]
        self.kiwoom.dynamicCall(
            "SendCondition(QString, QString, int, int)",
            "0201", "52주신고가", int(idx), 0)

    def _on_shingoga_condition(self, screen, code_list, condition_name, idx, prev_next):
        if condition_name != "52주신고가":
            return
        codes = [c for c in code_list.split(';') if c]
        print(f"  52주신고가 {len(codes)}개 수신")
        try:
            self.kiwoom.OnReceiveTrCondition.disconnect(self._on_shingoga_condition)
        except:
            pass
        self._phase5_done(codes)

    def _phase5_done(self, codes):
        self.shingoga_codes = codes
        self._connect(self._on_tr_shingoga)
        QTimer.singleShot(300, lambda: self._scan_shingoga(0))

    def _scan_shingoga(self, idx):
        self.shingoga_idx = idx
        if idx >= len(self.shingoga_codes):
            self._phase6_done()
            return
        code = self.shingoga_codes[idx]
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "신고가상세요청", "opt10001", 0, "0105")

    def _on_tr_shingoga(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "신고가상세요청":
            return
        code = self.shingoga_codes[self.shingoga_idx]
        k    = self.kiwoom
        try:
            name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                       trcode, rqname, 0, "종목명").strip()
            price     = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "현재가").strip()))
            open_p    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "시가").strip()))
            low_p     = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "저가").strip()))
            rate      = float(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                            trcode, rqname, 0, "등락율").strip())
            amount    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "거래대금").strip())) // 100000000
            self.shingoga_detail[code] = {
                "name":        name,
                "price":       price,
                "rate":        rate,
                "amount":      amount,
                "is_yangbong": price > open_p,
                "is_sijeo":    open_p > 0 and open_p == low_p,
            }
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_shingoga(self.shingoga_idx + 1))

    # ==========================================================
    # PHASE6: 브리핑 생성
    # ==========================================================
    def _phase6_done(self):
        try:
            self.kiwoom.OnReceiveTrData.disconnect()
        except:
            pass
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
        now     = datetime.now().strftime("%m/%d %H:%M")
        now_sec = datetime.now().strftime("%H:%M:%S")

        top_theme_codes = set()
        for t in self.theme_ranking[:THEME_TOP_N]:
            for s in t["stocks"]:
                top_theme_codes.add(s["code"])

        # ── 텔레그램 메시지1: 주도섹터
        medals = ["🥇","🥈","🥉","4위","5위","6위","7위"]
        msg1 = f"<b>📊 테마 주도섹터 TOP{THEME_TOP_N}</b>  {now}\n"
        msg1 += "거래대금 가중 등락률 기준\n"
        msg1 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, t in enumerate(self.theme_ranking[:THEME_TOP_N]):
            medal   = medals[i] if i < len(medals) else f"{i+1}위"
            amt_str = (f"{t['total_amount']//100}백억"
                       if t['total_amount'] >= 100
                       else f"{t['total_amount']}억")
            msg1 += (f"{medal} <b>{t['theme']}</b>\n"
                     f"   가중등락 <b>{t['weighted_rate']:+.2f}%</b>  "
                     f"거래대금 {amt_str}  "
                     f"상승 {t['up_ratio']*100:.0f}%\n")
            for s in t["stocks"][:THEME_STOCK_TOP]:
                if s["price"] == 0:
                    continue
                prog_str = f"  프로그램 {s['prog']:+d}억" if s["prog"] != 0 else ""
                msg1 += (f"   • {s['name']}  "
                         f"<b>{s['rate']:+.2f}%</b>  "
                         f"{s['price']:,}원  "
                         f"{s['amount']}억{prog_str}\n")
            msg1 += "\n"

        # ── 텔레그램 메시지2: 52주신고가
        filtered = []
        for code, d in self.shingoga_detail.items():
            if d["is_yangbong"] or d["is_sijeo"]:
                filtered.append({
                    "code":     code,
                    "name":     d["name"],
                    "price":    d["price"],
                    "rate":     d["rate"],
                    "amount":   d["amount"],
                    "is_sijeo": d["is_sijeo"],
                    "in_theme": code in top_theme_codes,
                })
        filtered = sorted(filtered, key=lambda x: x["amount"], reverse=True)[:15]

        msg2 = f"<b>🔝 52주 신고가</b>  {now}\n"
        msg2 += "양봉 | 🔥=주도테마 | 거래대금 순\n"
        msg2 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        for s in filtered:
            fire  = "🔥" if s["in_theme"] else "  "
            sijeo = " <b>[시=저]</b>" if s["is_sijeo"] else ""
            themes    = CODE_TO_THEMES.get(s["code"], [])
            theme_tag = f" [{'/'.join(themes[:2])}]" if themes else ""
            msg2 += (f"{fire} {s['name']}"
                     f"  <b>{s['rate']:+.2f}%</b>"
                     f"  {s['price']:,}원"
                     f"  {s['amount']}억"
                     f"{sijeo}<i>{theme_tag}</i>\n")
        if not filtered:
            msg2 += "해당 종목 없음\n"

        # ── HTML 파일 갱신
        self._update_html(now_sec)

        # ── 텔레그램 순차 전송
        send_telegram(msg1)
        QTimer.singleShot(2000, lambda: send_telegram(msg2))

        # ── ★ ngrok URL 15분마다 텔레그램 전송
        ngrok_url = self._get_ngrok_url()
        if ngrok_url:
            url_msg = (
                f"📊 <b>주도섹터 모니터 URL</b>\n"
                f"{ngrok_url}\n\n"
                f"갤럭시탭에서 위 링크를 열어두세요!\n"
                f"(15분마다 자동 새로고침)"
            )
            QTimer.singleShot(3000, lambda: send_telegram(url_msg))
            print(f"  ngrok URL 전송: {ngrok_url}")
        else:
            print("  ngrok URL 없음 - URL 전송 스킵")

        # ── 스크린샷 → 텔레그램 전송
        caption = f"<b>주도섹터 모니터</b>  {now}"
        QTimer.singleShot(4000, lambda: send_screenshot_to_telegram(caption))

        print(f"  모듈1 완료! [{now}]")

    # ==========================================================
    # HTML 파일 갱신 (브라우저 자동 새로고침)
    # ==========================================================
    def _update_html(self, scan_time: str):
        top7 = self.theme_ranking[:THEME_TOP_N]

        sectors_js = []
        for t in top7:
            stocks_js = []
            for s in t["stocks"][:THEME_STOCK_TOP]:
                if s["price"] == 0:
                    continue
                stocks_js.append({
                    "name":  s["name"],
                    "price": s["price"],
                    "rate":  round(s["rate"], 2),
                    "amt":   s["amount"],
                    "prog":  s.get("prog", 0),
                })
            sectors_js.append({
                "name":     t["theme"],
                "rate":     round(t["weighted_rate"], 2),
                "amt":      t["total_amount"],
                "up_ratio": round(t["up_ratio"], 2),
                "stocks":   stocks_js,
            })

        data_json = json.dumps(sectors_js, ensure_ascii=False)

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="900">
<title>주도섹터 모니터</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Malgun Gothic',sans-serif;background:#dceefb;padding:8px;}}
.topbar{{background:#0c447c;border-radius:8px;padding:7px 14px;display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}}
.topbar-title{{color:#e6f1fb;font-size:14px;font-weight:500;}}
.topbar-right{{display:flex;align-items:center;gap:12px;}}
.live-dot{{width:7px;height:7px;border-radius:50%;background:#4ade80;display:inline-block;margin-right:4px;}}
.live-label{{color:#4ade80;font-size:11px;font-weight:500;}}
.t-time{{color:#b5d4f4;font-size:12px;}}
.t-next{{color:#85b7eb;font-size:11px;}}
.legend{{display:flex;gap:10px;align-items:center;margin-bottom:6px;padding:4px 8px;background:#e4f0fb;border-radius:6px;}}
.legend-item{{display:flex;align-items:center;gap:4px;font-size:11px;color:#185fa5;}}
.legend-dot{{width:8px;height:8px;border-radius:2px;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:7px;}}
.card{{background:#f0f7ff;border:1px solid #b5d4f4;border-radius:8px;overflow:hidden;}}
.card-head{{background:#185fa5;padding:7px 10px;display:flex;justify-content:space-between;align-items:center;}}
.rank-badge{{background:#0c447c;color:#b5d4f4;font-size:10px;font-weight:500;padding:1px 6px;border-radius:4px;margin-right:6px;}}
.sector-name{{color:#e6f1fb;font-size:13px;font-weight:500;}}
.sector-meta{{text-align:right;}}
.sector-rate{{font-size:14px;font-weight:500;}}
.sector-sub{{color:#85b7eb;font-size:10px;}}
.card-body{{padding:5px 8px;}}
.stock-row{{padding:4px 0;border-bottom:0.5px solid #c8e0f7;}}
.stock-row:last-child{{border-bottom:none;}}
.stock-top{{display:flex;justify-content:space-between;align-items:center;}}
.sname{{font-size:12px;color:#185fa5;max-width:100px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.top1 .sname{{color:#0c447c;font-weight:500;}}
.sright{{display:flex;gap:8px;align-items:center;}}
.sprice{{font-size:11px;color:#0c447c;font-weight:500;}}
.srate{{font-size:12px;}}
.samt{{font-size:10px;color:#378add;}}
.prog-wrap{{margin-top:3px;display:flex;align-items:center;gap:4px;}}
.prog-lbl{{font-size:9px;width:32px;flex-shrink:0;text-align:right;}}
.prog-bg{{flex:1;height:5px;background:#dceefb;border-radius:3px;overflow:hidden;position:relative;}}
.prog-center{{position:absolute;left:50%;top:0;width:1px;height:5px;background:#aac8e8;}}
.prog-buy{{position:absolute;right:50%;top:0;height:5px;border-radius:3px 0 0 3px;background:#f5222d;}}
.prog-sell{{position:absolute;left:50%;top:0;height:5px;border-radius:0 3px 3px 0;background:#1677ff;}}
.footer{{margin-top:7px;background:#0c447c;border-radius:8px;padding:5px 14px;display:flex;justify-content:space-between;align-items:center;}}
.footer-txt{{color:#85b7eb;font-size:10px;}}
.footer-hi{{color:#b5d4f4;font-weight:500;}}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-title">주도섹터 · 주도주 모니터</div>
  <div class="topbar-right">
    <span><span class="live-dot"></span><span class="live-label">LIVE</span></span>
    <span class="t-time" id="cur-time">--:--:--</span>
    <span class="t-next" id="nxt">다음 스캔: --분 후</span>
  </div>
</div>
<div class="legend">
  <span class="legend-item"><span class="legend-dot" style="background:#f5222d"></span>프로그램 순매수 (←)</span>
  <span class="legend-item"><span class="legend-dot" style="background:#1677ff"></span>프로그램 순매도 (→)</span>
  <span class="legend-item" style="color:#888">| 바 너비 = 절대금액 비례 | 15분 자동갱신</span>
</div>
<div class="grid" id="grid"></div>
<div class="footer">
  <span class="footer-txt">스캔: <span class="footer-hi">{scan_time}</span></span>
  <span class="footer-txt">거래대금 가중 평균 등락률 · 테마 TOP7</span>
  <span class="footer-txt">프로그램: opt10059 당일 누적</span>
</div>

<script>
const SECTORS = {data_json};
const medals = ['🥇','🥈','🥉','4위','5위','6위','7위'];

function rc(r){{ return r>0?'#f5222d':r<0?'#1677ff':'#8c8c8c'; }}
function fr(r){{ return (r>0?'+':'')+r.toFixed(2)+'%'; }}
function fa(a){{
  if(a>=10000) return (a/10000).toFixed(1)+'조';
  if(a>=1000)  return (a/100).toFixed(0)+'백억';
  return a+'억';
}}
function fp(p){{ return p===0?'0':(p>0?'+':'')+p+'억'; }}
function fpr(p){{ return p.toLocaleString()+'원'; }}

function render(){{
  const maxP = Math.max(...SECTORS.flatMap(s=>s.stocks.map(st=>Math.abs(st.prog))));
  document.getElementById('grid').innerHTML = SECTORS.map((sec,si)=>{{
    const rows = [...sec.stocks].sort((a,b)=>b.rate-a.rate).map((st,i)=>{{
      const pct = maxP>0 ? Math.min(48,Math.round(Math.abs(st.prog)/maxP*48)) : 0;
      const buy  = st.prog>0 ? `<div class="prog-buy"  style="width:${{pct}}%"></div>` : '';
      const sell = st.prog<0 ? `<div class="prog-sell" style="width:${{pct}}%"></div>` : '';
      const pc   = st.prog===0?'#aaa':st.prog>0?'#f5222d':'#1677ff';
      return `<div class="stock-row${{i===0?' top1':''}}">
        <div class="stock-top">
          <span class="sname">${{st.name}}</span>
          <div class="sright">
            <span class="sprice">${{fpr(st.price)}}</span>
            <span class="srate" style="color:${{rc(st.rate)}}">${{fr(st.rate)}}</span>
            <span class="samt">${{fa(st.amt)}}</span>
          </div>
        </div>
        <div class="prog-wrap">
          <span class="prog-lbl" style="color:${{pc}}">${{fp(st.prog)}}</span>
          <div class="prog-bg"><div class="prog-center"></div>${{buy}}${{sell}}</div>
        </div>
      </div>`;
    }}).join('');
    return `<div class="card">
      <div class="card-head">
        <div><span class="rank-badge">${{medals[si]||si+1+'위'}}</span><span class="sector-name">${{sec.name}}</span></div>
        <div class="sector-meta">
          <div class="sector-rate"