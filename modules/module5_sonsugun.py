# -*- coding: utf-8 -*-
"""
module5_sonsugun.py - 모듈5: 손수건 매매
장중 30분마다 거래대금 상위 100종목(opt10032) × 주도섹터 주도주 교집합 계산
- 교집합 종목 → 우유병🚀 마킹 (모듈1 UI/텔레그램 반영)
- 교집합이 가장 많은 섹터 = 최강 주도섹터
- 매시 정각: 교집합 종목 네이버 뉴스 브리핑 (09~15시)
- 15:05 종가 베팅 브리핑 텔레그램 전송 (수동 확인 후 베팅)
"""

import os
import re
import io
import requests
import matplotlib
matplotlib.use('Agg')   # Qt 이벤트 루프와 충돌 방지
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime, timedelta
from PyQt5.QtCore import QTimer
from modules.common import send_telegram, TELEGRAM_TOKEN, CHAT_ID

SCREEN_CHART  = "0801"
CHART_CANDLES = 30   # 15분봉 30개 (약 1.5거래일)

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_NEWS_URL      = "https://openapi.naver.com/v1/search/news.json"

NEWS_START_HOUR = 9
NEWS_END_HOUR   = 15   # 15시 정각까지 (15:05 브리핑 전)

SCREEN_NO     = "0800"
REFRESH_MS    = 30 * 60 * 1000   # 30분마다 갱신
BRIEFING_HOUR = 15
BRIEFING_MIN  = 5
SECTOR_TOP_N  = 7


class Module5Sonsugun:

    def __init__(self, kiwoom, queue, module1):
        self.kiwoom  = kiwoom
        self._queue  = queue
        self.module1 = module1
        self.top100_codes = set()
        self.top100_data  = {}      # {코드: {name, amount_eok, rate, rank}}
        self._briefing_date = None
        self._busy = False
        self.kiwoom.OnReceiveTrData.connect(self._on_tr)

    def start(self):
        print("[모듈5] 손수건매매 시작")
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._fetch_top100)
        self._refresh_timer.start(REFRESH_MS)
        QTimer.singleShot(5000, self._fetch_top100)
        self._schedule_briefing()
        self._schedule_chart_briefing()
        self._schedule_next_news()

        # 차트 조회용 상태
        self._chart_queue  = []   # [(code, name, rate)] 대기 목록
        self._chart_busy   = False

    # ----------------------------------------------------------
    # opt10032: 거래대금상위요청 - 거래대금 상위 100 조회
    # ----------------------------------------------------------
    def _fetch_top100(self):
        if self._busy:
            return
        self._busy = True
        print(f"[모듈5] {datetime.now().strftime('%H:%M')} 거래대금 상위 100 조회")

        def _do():
            k = self.kiwoom
            k.dynamicCall("SetInputValue(QString,QString)", "시장구분", "000")
            k.dynamicCall("SetInputValue(QString,QString)", "관리종목포함", "0")
            k.dynamicCall("CommRqData(QString,QString,int,QString)",
                          "손수건거래대금상위", "opt10032", 0, SCREEN_NO)

        self._queue.push(_do)

    def _on_tr(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname == "손수건15분봉":
            self._on_tr_chart(trcode, rqname)
            return
        if rqname != "손수건거래대금상위":
            return
        k   = self.kiwoom
        cnt = min(k.dynamicCall("GetRepeatCnt(QString,QString)", trcode, rqname), 100)

        codes, data = set(), {}
        for i in range(cnt):
            try:
                code   = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                        trcode, rqname, i, "종목코드").strip().lstrip('A')
                name   = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                        trcode, rqname, i, "종목명").strip()
                amount = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                               trcode, rqname, i, "거래대금").strip() or "0"))
                rate   = float(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                             trcode, rqname, i, "등락율").strip() or "0")
                if code:
                    codes.add(code)
                    data[code] = {
                        "name":       name,
                        "amount_eok": amount // 100_000_000,
                        "rate":       rate,
                        "rank":       i + 1,
                    }
            except:
                pass

        self.top100_codes = codes
        self.top100_data  = data
        self._busy = False
        self._queue.done()

        milk      = self.get_milk_codes()
        top_sec, n = self.get_top_sector()
        sec_name  = top_sec["theme"] if top_sec else "-"
        print(f"  [모듈5] 대금 상위 {len(codes)}개 | 교집합 {len(milk)}개 | 최강섹터: {sec_name}({n}개)")

    # ----------------------------------------------------------
    # 교집합 / 최강 섹터
    # ----------------------------------------------------------
    def get_milk_codes(self) -> set:
        """주도섹터 TOP7 종목 중 거래대금 상위 100에 포함된 코드 집합"""
        if not self.module1.theme_ranking or not self.top100_codes:
            return set()
        leader = {
            s["code"]
            for t in self.module1.theme_ranking[:SECTOR_TOP_N]
            for s in t["stocks"]
        }
        return leader & self.top100_codes

    def get_top_sector(self):
        """교집합 종목 수가 가장 많은 섹터 반환 → (sector_dict, count)"""
        if not self.module1.theme_ranking or not self.top100_codes:
            return None, 0
        best, best_n = None, 0
        for t in self.module1.theme_ranking[:SECTOR_TOP_N]:
            n = sum(1 for s in t["stocks"] if s["code"] in self.top100_codes)
            if n > best_n:
                best_n, best = n, t
        return best, best_n

    # ----------------------------------------------------------
    # 15:05 브리핑
    # ----------------------------------------------------------
    def _schedule_briefing(self):
        now    = datetime.now()
        target = now.replace(hour=BRIEFING_HOUR, minute=BRIEFING_MIN, second=0, microsecond=0)
        diff   = int((target - now).total_seconds() * 1000)
        if diff > 0:
            QTimer.singleShot(diff, self._send_briefing)
            print("[모듈5] 15:05 브리핑 타이머 설정")

    def _send_briefing(self):
        today = datetime.now().date()
        if self._briefing_date == today:
            return
        self._briefing_date = today

        top_sec, count = self.get_top_sector()
        milk_codes     = self.get_milk_codes()
        now_str        = datetime.now().strftime("%m/%d %H:%M")

        if not top_sec or count == 0:
            send_telegram(
                f"<b>🚀 [손수건] 15:05 브리핑</b>  {now_str}\n"
                "교집합 종목 없음 - 오늘 베팅 대상 없음"
            )
            return

        milk_stocks = sorted(
            [s for s in top_sec["stocks"] if s["code"] in milk_codes],
            key=lambda s: self.top100_data.get(s["code"], {}).get("rank", 999)
        )

        msg = (
            f"<b>🚀 [손수건] 15:05 종가 베팅 브리핑</b>  {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 최강 주도섹터: <b>{top_sec['theme']}</b>\n"
            f"   평균등락 {top_sec['avg_rate']:+.2f}%  교집합 {count}종목\n\n"
            f"<b>💰 종가 베팅 대상 (거래대금 순)</b>\n"
        )
        for s in milk_stocks[:10]:
            t = self.top100_data.get(s["code"], {})
            msg += (
                f"  🚀 {s['name']}  "
                f"{s['rate']:+.2f}%  "
                f"{s['price']:,}원  "
                f"대금{t.get('rank','?')}위  {t.get('amount_eok', 0)}억\n"
            )
        msg += "\n━━━━━━━━━━━━━━━━━━━━\n확인 후 종가 베팅 진행하세요"
        send_telegram(msg)

    # ----------------------------------------------------------
    # 매시 정각 뉴스 브리핑 (09:00 ~ 15:00)
    # ----------------------------------------------------------
    def _schedule_next_news(self):
        now    = datetime.now()
        # 다음 정각 계산
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        diff = int((next_hour - now).total_seconds() * 1000)
        QTimer.singleShot(diff, self._on_news_tick)
        print(f"[모듈5] 다음 뉴스 브리핑: {next_hour.strftime('%H:%M')}")

    def _on_news_tick(self):
        hour = datetime.now().hour
        if NEWS_START_HOUR <= hour <= NEWS_END_HOUR:
            self._send_news_briefing()
        self._schedule_next_news()   # 다음 정각 예약

    def _fetch_naver_news(self, stock_name: str) -> list:
        """네이버 뉴스 API로 종목명 검색 → 최신 2개 반환"""
        try:
            resp = requests.get(
                NAVER_NEWS_URL,
                headers={
                    "X-Naver-Client-Id":     NAVER_CLIENT_ID,
                    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
                },
                params={"query": f"{stock_name} 주식", "display": 2, "sort": "date"},
                timeout=5,
            )
            items = resp.json().get("items", [])
            result = []
            for it in items:
                title = re.sub(r"<[^>]+>", "", it.get("title", ""))   # HTML 태그 제거
                pub   = it.get("pubDate", "")[:16]                     # "Mon, 16 Jun 2026"
                result.append(f"  📰 {title}  <i>{pub}</i>")
            return result
        except Exception as e:
            print(f"  [모듈5] 네이버 뉴스 오류({stock_name}): {e}")
            return []

    def _send_news_briefing(self):
        milk_codes = self.get_milk_codes()
        top_sec, count = self.get_top_sector()
        now_str = datetime.now().strftime("%m/%d %H:%M")

        if not milk_codes or not top_sec:
            send_telegram(
                f"<b>🚀 [손수건] {now_str} 뉴스</b>\n"
                "교집합 종목 없음"
            )
            return

        # 최강 섹터 교집합 종목 (거래대금 순)
        milk_stocks = sorted(
            [s for s in top_sec["stocks"] if s["code"] in milk_codes],
            key=lambda s: self.top100_data.get(s["code"], {}).get("rank", 999)
        )

        msg = (
            f"<b>🚀 [손수건] {now_str} 뉴스 브리핑</b>\n"
            f"🏆 {top_sec['theme']}  {top_sec['avg_rate']:+.2f}%  교집합 {count}종목\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )

        for s in milk_stocks[:10]:
            news_items = self._fetch_naver_news(s["name"])
            if not news_items:
                continue
            t = self.top100_data.get(s["code"], {})
            msg += (
                f"\n🚀 <b>{s['name']}</b>  "
                f"{s['rate']:+.2f}%  대금{t.get('rank','?')}위\n"
            )
            msg += "\n".join(news_items) + "\n"

        send_telegram(msg)

    # ----------------------------------------------------------
    # 15:02 차트 브리핑 (15:05 텍스트 브리핑 직전)
    # ----------------------------------------------------------
    def _schedule_chart_briefing(self):
        now    = datetime.now()
        target = now.replace(hour=15, minute=2, second=0, microsecond=0)
        diff   = int((target - now).total_seconds() * 1000)
        if diff > 0:
            QTimer.singleShot(diff, self._start_chart_briefing)
            print("[모듈5] 15:02 차트 브리핑 타이머 설정")

    def _start_chart_briefing(self):
        milk_codes = self.get_milk_codes()
        top_sec, _ = self.get_top_sector()
        if not milk_codes or not top_sec:
            return

        milk_stocks = sorted(
            [s for s in top_sec["stocks"] if s["code"] in milk_codes],
            key=lambda s: self.top100_data.get(s["code"], {}).get("rank", 999)
        )

        self._chart_queue = [
            (s["code"], s["name"], s["rate"])
            for s in milk_stocks[:5]
        ]
        print(f"[모듈5] 차트 브리핑 시작: {len(self._chart_queue)}종목")
        self._fetch_next_chart()

    def _fetch_next_chart(self):
        if not self._chart_queue or self._chart_busy:
            return
        self._chart_busy = True
        code, name, rate = self._chart_queue[0]

        def _do():
            k = self.kiwoom
            k.dynamicCall("SetInputValue(QString,QString)", "종목코드", code)
            k.dynamicCall("SetInputValue(QString,QString)", "틱범위", "15")
            k.dynamicCall("SetInputValue(QString,QString)", "수정주가구분", "1")
            k.dynamicCall("CommRqData(QString,QString,int,QString)",
                          "손수건15분봉", "opt10080", 0, SCREEN_CHART)

        self._queue.push(_do)

    def _on_tr_chart(self, trcode, rqname):
        if not self._chart_queue:
            self._queue.done()
            return

        code, name, rate = self._chart_queue.pop(0)
        k   = self.kiwoom
        cnt = min(k.dynamicCall("GetRepeatCnt(QString,QString)", trcode, rqname), CHART_CANDLES)

        candles = []
        for i in range(cnt):
            try:
                close  = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                               trcode, rqname, i, "현재가").strip() or "0"))
                open_p = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                               trcode, rqname, i, "시가").strip() or "0"))
                high   = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                               trcode, rqname, i, "고가").strip() or "0"))
                low    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                               trcode, rqname, i, "저가").strip() or "0"))
                volume = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                               trcode, rqname, i, "거래량").strip() or "0"))
                time_s = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                       trcode, rqname, i, "체결시간").strip()
                if close > 0:
                    candles.append({
                        "open": open_p, "high": high,
                        "low": low, "close": close,
                        "volume": volume, "time": time_s,
                    })
            except:
                pass

        self._queue.done()
        self._chart_busy = False

        if candles:
            candles.reverse()   # 오래된 것 → 최신 순
            t100 = self.top100_data.get(code, {})
            caption = (
                f"🚀 <b>{name}</b>  {rate:+.2f}%  "
                f"대금{t100.get('rank','?')}위  {t100.get('amount_eok',0)}억\n"
                f"15분봉 ({len(candles)}캔들)"
            )
            img_bytes = self._draw_chart(candles, name, rate)
            if img_bytes:
                self._send_photo(img_bytes, caption)

        if self._chart_queue:
            QTimer.singleShot(500, self._fetch_next_chart)

    # ----------------------------------------------------------
    # matplotlib 캔들차트 생성 (HTS 스타일)
    # ----------------------------------------------------------
    def _draw_chart(self, candles: list, name: str, rate: float) -> bytes:
        try:
            fig, (ax1, ax2) = plt.subplots(
                2, 1, figsize=(10, 6),
                gridspec_kw={"height_ratios": [3, 1]},
                facecolor="#ffffff"
            )
            for ax in (ax1, ax2):
                ax.set_facecolor("#ffffff")
                ax.tick_params(colors="#333333", labelsize=8)
                ax.grid(axis="y", color="#e0e0e0", linewidth=0.5, linestyle="-")
                ax.grid(axis="x", color="#e0e0e0", linewidth=0.5, linestyle="-")
                ax.set_axisbelow(True)
                for spine in ax.spines.values():
                    spine.set_edgecolor("#cccccc")

            # ── 캔들 그리기
            for i, c in enumerate(candles):
                up    = c["close"] >= c["open"]
                color = "#cc0000" if up else "#0033cc"
                # 심지
                ax1.plot([i, i], [c["low"], c["high"]], color=color, linewidth=0.9, zorder=2)
                # 몸통
                body_h = max(abs(c["close"] - c["open"]), c["close"] * 0.0008)
                ax1.bar(i, body_h, bottom=min(c["open"], c["close"]),
                        color=color, width=0.6, linewidth=0, zorder=3)
                # 거래량
                ax2.bar(i, c["volume"], color=color, width=0.6,
                        linewidth=0, alpha=0.8)

            # ── 이동평균선 (5, 20, 75)
            closes = [c["close"] for c in candles]
            ma_styles = [
                (5,  "#ff0000", 1.0, "MA5"),
                (20, "#aa00cc", 1.0, "MA20"),
                (75, "#222222", 1.2, "MA75"),
            ]
            for period, color, lw, label in ma_styles:
                if len(closes) >= period:
                    ma = [
                        sum(closes[max(0, j - period + 1):j + 1]) /
                        min(j + 1, period)
                        for j in range(len(closes))
                    ]
                    ax1.plot(range(len(ma)), ma, color=color,
                             linewidth=lw, label=label, zorder=4)

            # ── 범례
            ax1.legend(loc="upper left", fontsize=7, framealpha=0.8,
                       facecolor="#ffffff", edgecolor="#cccccc")

            # ── X축 시간 레이블
            step   = max(1, len(candles) // 6)
            ticks  = list(range(0, len(candles), step))
            labels = []
            for i in ticks:
                t = candles[i]["time"]
                if len(t) >= 8:
                    labels.append(f"{t[4:6]}:{t[6:8]}")
                else:
                    labels.append("")
            ax2.set_xticks(ticks)
            ax2.set_xticklabels(labels, color="#333333", fontsize=8)
            ax1.set_xticks([])

            # ── 제목
            color_title = "#cc0000" if rate >= 0 else "#0033cc"
            sign = "▲" if rate >= 0 else "▼"
            ax1.set_title(
                f"{name}  {sign} {abs(rate):.2f}%  15분봉",
                color=color_title, fontsize=12, fontweight="bold", pad=8,
                loc="left"
            )
            ax1.yaxis.set_tick_params(labelsize=8)
            ax2.set_ylabel("거래량", color="#666666", fontsize=8)
            ax1.set_xlim(-0.5, len(candles) - 0.5)
            ax2.set_xlim(-0.5, len(candles) - 0.5)

            plt.tight_layout(pad=1.2)
            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=130, facecolor="#ffffff",
                        bbox_inches="tight")
            buf.seek(0)
            img_bytes = buf.read()
            plt.close(fig)
            return img_bytes

        except Exception as e:
            print(f"  [모듈5] 차트 생성 오류: {e}")
            return None

    # ----------------------------------------------------------
    # 텔레그램 이미지 전송
    # ----------------------------------------------------------
    def _send_photo(self, img_bytes: bytes, caption: str):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            requests.post(
                url,
                data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("chart.png", img_bytes, "image/png")},
                timeout=30,
            )
            print(f"  [모듈5] 차트 전송 완료")
        except Exception as e:
            print(f"  [모듈5] 차트 전송 오류: {e}")
