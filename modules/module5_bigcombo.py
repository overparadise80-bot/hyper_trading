# -*- coding: utf-8 -*-
"""
big_combo.py - 모듈5: 빅이벤트 갭 콤보 매매
대상: 주식선물 보유 종목 (238개)
조건:
  1. 갭상승 3% 이상 OR 시가=저가 (가산점 부여)
  2. 전일고점 근접/돌파 OR 52주신고가
  3. 프로그램 순매수 3만주 이상
  4. 3분봉 연속 양봉 4캔들 이상
  5. 일봉 거래량 >= 60일 평균 거래량 × 70%
  6. Gemini 뉴스 검색 → 빅이벤트 판별 후 자동 진입
진입: 1차 100만원 시장가, 2차 30분내 -2% 눌림 시 50만원 (텔레그램 승인)
청산: 손절 -2.5%, 14:50 일괄 청산
스캔: 09:00 ~ 11:00
"""

import os
import requests
from datetime import datetime, time
from PyQt5.QtCore import QTimer
from modules.common import (
    send_telegram, ACCOUNT_NUM, STOP_LOSS_RATE,
    FORCE_EXIT_ALL, get_tick_size, calc_qty
)
from modules import trade_manager as tm

# =============================================================
# 설정값
# =============================================================
BC_START         = time(9, 0)
BC_END           = time(11, 0)
BC_SCAN_INTERVAL = 5 * 60 * 1000     # 5분마다 스캔
BC_ENTRY_AMOUNT  = 1000000           # 1차 진입 100만원
BC_ADD_AMOUNT    = 500000            # 2차 추가 50만원
BC_ADD_RATE      = -0.02             # 2차 추가 조건: -2% 눌림
BC_ADD_MINUTES   = 30                # 2차 추가 대기 시간
BC_PROG_MIN_QTY  = 30000            # 프로그램 순매수 최소 3만주
BC_CANDLE_MIN    = 4                 # 3분봉 연속 양봉 최소 4캔들
BC_GAP_RATE      = 0.03             # 갭상승 기준 3%
BC_VOL_RATIO     = 0.70             # 60일 평균 거래량 대비 70%
BC_VOL_DAYS      = 60               # 거래량 평균 기준 일수
BC_HIGH_MARGIN   = 0.02             # 전일고점 근접 기준 2% 이내
BC_MAX_POSITIONS = 5                # 최대 동시 보유 종목
BC_SCREEN_BASE   = "0700"           # 스크린 번호 베이스

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
)

# =============================================================
# 주식선물 대상 종목명 리스트
# =============================================================
FUTURES_STOCKS = [
    "BNK금융지주","CJ","CJ ENM","CJ대한통운","CJ제일제당",
    "DB손해보험","DL","DL이앤씨","DN오토모티브","F&F",
    "GKL","GS","GS건설","GS리테일","HD마린솔루션","HD마린엔진",
    "HD한국조선해양","HD현대","HD현대일렉트릭","HD현대중공업",
    "HLB","HL만도","HMM","HPSP","ISC","JB금융지주","JYP Ent.",
    "KB금융지주","KCC","KT","KT&G","LG","LG디스플레이","LG생활건강",
    "LG씨엔에스","LG에너지솔루션","LG유플러스","LG이노텍","LG전자",
    "LG화학","LIG넥스원","LS","LS ELECTRIC","NAVER","NH투자증권",
    "OCI홀딩스","POSCO홀딩스","S-Oil","SK","SKC","SK스퀘어",
    "SK아이이테크놀로지","SK이노베이션","SK케미칼","SK텔레콤",
    "SK하이닉스","SOOP","TKG휴켐스","강원랜드","고려아연","고영",
    "골프존","금양","금호석유화학","금호타이어","기아","기업은행",
    "네이처셀","넥스틴","넷마블","녹십자","녹십자홀딩스","농심",
    "대상","대우건설","대웅","대웅제약","대한유화","대한전선",
    "대한항공","더블유게임즈","덕산네오룩스","동국제약","동서",
    "동원산업","동원시스템즈","동진쎄미켐","동화기업","두산",
    "두산로보틱스","두산밥캣","두산에너빌리티","두산테스나",
    "디앤디파마텍","레인보우로보틱스","로보티즈","롯데쇼핑",
    "롯데웰푸드","롯데정밀화학","롯데지주","롯데칠성음료","롯데케미칼",
    "리노공업","메가스터디교육","메디톡스","메리츠금융지주",
    "미래에셋증권","미스토홀딩스","미원상사","미원에스씨","보로노이",
    "산일전기","삼성E&A","삼성SDI","삼성SDS","삼성물산","삼성생명",
    "삼성에피스홀딩스","삼성전기","삼성전자","삼성중공업","삼성증권",
    "삼성카드","삼성화재","삼양식품","삼천당제약","세방전지",
    "세아베스틸지주","세아제강지주","셀트리온","셀트리온제약",
    "솔브레인","솔브레인홀딩스","스튜디오드래곤","신성델타테크",
    "신세계","신한금융지주","실리콘투","심텍","씨아이에스","씨에스윈드",
    "씨젠","아모레퍼시픽","아모레퍼시픽홀딩스","아세아","아이엠금융지주",
    "알테오젠","에스에프에이","에스엘","에스엠","에스원","에스티팜",
    "에이피알","에코프로","에코프로머티","에코프로비엠","엔씨","엔켐",
    "엘앤에프","영원무역","영원무역홀딩스","영풍","오뚜기","오리온",
    "오리온홀딩스","와이지엔터테인먼트","우리금융지주","원익IPS",
    "원익QnC","웹젠","위메이드","유진테크","유한양행","율촌화학",
    "이녹스첨단소재","이마트","이수스페셜티케미컬","이수페타시스",
    "이오테크닉스","제일기획","젬백스","종근당","주성엔지니어링",
    "지역난방공사","카카오","카카오게임즈","카카오뱅크","카카오페이",
    "컴투스","케어젠","코미코","코스맥스","코스모화학","코오롱인더스트리",
    "코웨이","콜마비앤에이치","크래프톤","클래시스","키움증권","태광산업",
    "테크윙","티씨케이","파라다이스","파마리서치","파크시스템스","팬오션",
    "펄어비스","펩트론","포스코DX","포스코엠텍","포스코인터내셔널",
    "포스코퓨처엠","풍산","피에스케이","피엔티","하나금융지주",
    "하나머티리얼즈","하림지주","하이브","하이트진로","한국가스공사",
    "한국금융지주","한국앤컴퍼니","한국전력","한국카본","한국콜마",
    "한국타이어앤테크놀로지","한국항공우주","한글과컴퓨터","한미사이언스",
    "한미약품","한샘","한솔케미칼","한온시스템","한일시멘트","한전KPS",
    "한전기술","한진칼","한화","한화생명","한화솔루션","한화시스템",
    "한화에어로스페이스","한화엔진","한화오션","현대건설","현대글로비스",
    "현대로템","현대모비스","현대백화점","현대엘리베이터","현대오토에버",
    "현대위아","현대자동차","현대제철","현대해상","호텔신라","효성중공업",
    "효성첨단소재","효성티앤씨","후성","휴젤",
    "SK네트웍스",  # 오늘의 주인공
]

# =============================================================
# 클래스
# =============================================================
class Module5BigCombo:

    def __init__(self, kiwoom):
        self.kiwoom       = kiwoom
        self.status       = {}   # {코드: 상태dict}
        self.name_to_code = {}   # {종목명: 종목코드} - 런타임 매핑
        self.scan_codes   = []   # 전체 종목코드 리스트
        self.scan_idx     = 0
        self.vol_queue    = []
        self.vol_idx      = 0
        self.candle_queue = []
        self.candle_idx   = 0
        self.prog_queue   = []
        self.prog_idx     = 0
        self.candidates   = {}   # 조건 통과 후보 {코드: 점수dict}
        self._screen_cnt  = 0
        self._tr_handler  = None
        self.kiwoom.OnReceiveTrData.connect(self._on_tr_dispatch)

    def _next_screen(self) -> str:
        self._screen_cnt += 1
        return f"{int(BC_SCREEN_BASE) + self._screen_cnt:04d}"

    def _is_open(self) -> bool:
        if datetime.now().weekday() >= 5:
            return False
        return BC_START <= datetime.now().time() <= BC_END

    # ==========================================================
    # 스캔 시작
    # ==========================================================
    def start(self):
        """condition_kiwoom_v2에서 초기화 후 호출"""
        # 종목 코드 조회 (종목명→코드 매핑)
        print("[모듈5] BigCombo 시작 - 종목코드 매핑 중...")
        self._build_code_map()

        # 5분 스캔 타이머
        self._scan_timer = QTimer()
        self._scan_timer.timeout.connect(self._on_scan_timer)
        self._scan_timer.start(BC_SCAN_INTERVAL)
        print(f"[모듈5] 5분 스캔 타이머 시작 (09:00~11:00)")

        # 즉시 1회 실행
        QTimer.singleShot(3000, self._on_scan_timer)

    def _build_code_map(self):
        """키움 GetCodeListByMarket으로 전체 종목코드 가져와 이름 매핑"""
        try:
            kospi = self.kiwoom.dynamicCall(
                "GetCodeListByMarket(QString)", "0").split(';')
            kosdaq = self.kiwoom.dynamicCall(
                "GetCodeListByMarket(QString)", "10").split(';')
            all_codes = [c for c in kospi + kosdaq if c]
            for code in all_codes:
                name = self.kiwoom.dynamicCall(
                    "GetMasterCodeName(QString)", code).strip()
                if name in FUTURES_STOCKS:
                    self.name_to_code[name] = code
            self.scan_codes = list(self.name_to_code.values())
            print(f"[모듈5] 종목코드 매핑 완료: {len(self.scan_codes)}개")
        except Exception as e:
            print(f"[모듈5] 코드 매핑 오류: {e}")

    # ==========================================================
    # 타이머 → 스캔
    # ==========================================================
    def _on_scan_timer(self):
        if not self._is_open():
            return
        if not self.scan_codes:
            print("[모듈5] 종목코드 없음 - 재매핑 시도")
            self._build_code_map()
            return
        print(f"\n[모듈5] {datetime.now().strftime('%H:%M')} 스캔 시작 ({len(self.scan_codes)}종목)")
        self.candidates = {}
        self._tr_handler = self._on_tr_basic
        QTimer.singleShot(500, lambda: self._scan_basic(0))

    def _on_tr_dispatch(self, screen, rqname, trcode, recordname, prev_next, *args):
        if self._tr_handler:
            self._tr_handler(screen, rqname, trcode, recordname, prev_next, *args)

    # ==========================================================
    # PHASE1: 기본 정보 스캔 (현재가/시가/전일고가/거래량/등락률)
    # ==========================================================
    def _scan_basic(self, idx):
        self.scan_idx = idx
        if idx >= len(self.scan_codes):
            self._phase1_done()
            return
        code = self.scan_codes[idx]
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "빅콤보기본조회", "opt10001", 0, "0710")

    def _on_tr_basic(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "빅콤보기본조회":
            return
        code = self.scan_codes[self.scan_idx]
        k    = self.kiwoom
        try:
            name      = k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                       trcode, rqname, 0, "종목명").strip()
            price     = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "현재가").strip()))
            open_p    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "시가").strip()))
            prev_high = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "전일고가").strip()))
            prev_close= abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "전일종가").strip()))
            volume    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "거래량").strip()))
            high52    = abs(int(k.dynamicCall("GetCommData(QString,QString,int,QString)",
                                              trcode, rqname, 0, "최고가").strip()))

            if prev_close == 0 or open_p == 0:
                QTimer.singleShot(100, lambda: self._scan_basic(self.scan_idx + 1))
                return

            # ── 갭 계산
            gap_rate    = (open_p - prev_close) / prev_close
            is_sijeo    = (open_p == price or open_p <= price * 1.001)  # 시가≈저가
            gap_ok      = gap_rate >= BC_GAP_RATE or is_sijeo

            if not gap_ok:
                QTimer.singleShot(100, lambda: self._scan_basic(self.scan_idx + 1))
                return

            # ── 전일고점 근접/돌파 OR 52주신고가 근접
            near_prev_high = (price >= prev_high * (1 - BC_HIGH_MARGIN))
            near_52w_high  = (price >= high52 * 0.98)
            pos_ok = near_prev_high or near_52w_high

            if not pos_ok:
                QTimer.singleShot(100, lambda: self._scan_basic(self.scan_idx + 1))
                return

            # ── 가산점 계산
            score = 0
            if gap_rate >= BC_GAP_RATE:
                score += 2
            if is_sijeo:
                score += 3   # 시가=저가 더 높은 가산점
            if price >= high52 * 0.98:
                score += 2   # 52주 신고가 근접
            if price > prev_high:
                score += 1   # 전일고점 돌파

            # 후보 등록 → 거래량 조회 대상으로
            self.candidates[code] = {
                "name":       name,
                "price":      price,
                "open_p":     open_p,
                "prev_high":  prev_high,
                "prev_close": prev_close,
                "volume":     volume,
                "gap_rate":   gap_rate,
                "is_sijeo":   is_sijeo,
                "score":      score,
                "vol_ok":     False,
                "candle_ok":  False,
                "prog_ok":    False,
                "prog_qty":   0,
                "news_ok":    False,
                "news_summary": "",
            }
            print(f"  [빅콤보] 갭/위치 통과: {name} "
                  f"갭{gap_rate*100:+.1f}% "
                  f"{'시=저 ' if is_sijeo else ''}"
                  f"점수:{score}")

        except Exception as e:
            pass

        QTimer.singleShot(150, lambda: self._scan_basic(self.scan_idx + 1))

    # ==========================================================
    # PHASE1 완료 → 거래량 조회
    # ==========================================================
    def _phase1_done(self):
        print(f"[모듈5] PHASE1 완료. 후보: {len(self.candidates)}개 → 거래량 조회")
        if not self.candidates:
            return
        self.vol_queue = list(self.candidates.keys())
        self._tr_handler = self._on_tr_vol
        QTimer.singleShot(300, lambda: self._scan_vol(0))

    # ==========================================================
    # PHASE2: 60일 거래량 조회
    # ==========================================================
    def _scan_vol(self, idx):
        self.vol_idx = idx
        if idx >= len(self.vol_queue):
            self._phase2_done()
            return
        code = self.vol_queue[idx]
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "수정주가구분", "1")
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "빅콤보일봉조회", "opt10081", 0, "0711")

    def _on_tr_vol(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "빅콤보일봉조회":
            return
        code = self.vol_queue[self.vol_idx]
        if code not in self.candidates:
            QTimer.singleShot(200, lambda: self._scan_vol(self.vol_idx + 1))
            return
        try:
            volumes = []
            for i in range(BC_VOL_DAYS + 1):
                v = abs(int(self.kiwoom.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "거래량").strip()))
                volumes.append(v)

            if len(volumes) >= 2:
                today_vol = volumes[0]
                avg_vol   = sum(volumes[1:]) / len(volumes[1:])
                ratio     = today_vol / avg_vol if avg_vol > 0 else 0
                if ratio >= BC_VOL_RATIO:
                    self.candidates[code]["vol_ok"] = True
                    print(f"  [빅콤보] 거래량 통과: {self.candidates[code]['name']} "
                          f"{ratio:.1f}배")
                else:
                    print(f"  [빅콤보] 거래량 미달: {self.candidates[code]['name']} "
                          f"{ratio:.1f}배 (기준 {BC_VOL_RATIO}배)")
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_vol(self.vol_idx + 1))

    def _phase2_done(self):
        # 거래량 미통과 제거
        self.candidates = {
            c: d for c, d in self.candidates.items() if d["vol_ok"]
        }
        print(f"[모듈5] PHASE2 완료. 거래량 통과: {len(self.candidates)}개 → 3분봉 조회")
        if not self.candidates:
            return
        self.candle_queue = list(self.candidates.keys())
        self._tr_handler = self._on_tr_candle
        QTimer.singleShot(300, lambda: self._scan_candle(0))

    # ==========================================================
    # PHASE3: 3분봉 연속 양봉 4캔들
    # ==========================================================
    def _scan_candle(self, idx):
        self.candle_idx = idx
        if idx >= len(self.candle_queue):
            self._phase3_done()
            return
        code = self.candle_queue[idx]
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "틱범위", "3")
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "수정주가구분", "1")
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "빅콤보3분봉조회", "opt10080", 0, "0712")

    def _on_tr_candle(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "빅콤보3분봉조회":
            return
        code = self.candle_queue[self.candle_idx]
        if code not in self.candidates:
            QTimer.singleShot(200, lambda: self._scan_candle(self.candle_idx + 1))
            return
        try:
            candles = []
            for i in range(10):  # 최근 10개 캔들
                o = abs(int(self.kiwoom.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "시가").strip()))
                c = abs(int(self.kiwoom.dynamicCall(
                    "GetCommData(QString,QString,int,QString)",
                    trcode, rqname, i, "현재가").strip()))
                candles.append({"open": o, "close": c})

            # 최근 캔들부터 연속 양봉 카운트
            # candles[0]이 가장 최근
            consecutive = 0
            for candle in candles:
                if candle["close"] > candle["open"]:
                    consecutive += 1
                else:
                    break

            if consecutive >= BC_CANDLE_MIN:
                self.candidates[code]["candle_ok"] = True
                print(f"  [빅콤보] 3분봉 통과: {self.candidates[code]['name']} "
                      f"연속양봉 {consecutive}개")
            else:
                print(f"  [빅콤보] 3분봉 미달: {self.candidates[code]['name']} "
                      f"연속양봉 {consecutive}개")
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_candle(self.candle_idx + 1))

    def _phase3_done(self):
        self.candidates = {
            c: d for c, d in self.candidates.items() if d["candle_ok"]
        }
        print(f"[모듈5] PHASE3 완료. 3분봉 통과: {len(self.candidates)}개 → 프로그램 매매 조회")
        if not self.candidates:
            return
        self.prog_queue = list(self.candidates.keys())
        self._tr_handler = self._on_tr_prog
        QTimer.singleShot(300, lambda: self._scan_prog(0))

    # ==========================================================
    # PHASE4: 프로그램 순매수 3만주 이상
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
            "SetInputValue(QString, QString)", "금액수량구분", "1")  # 수량
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "매매구분", "0")
        self.kiwoom.dynamicCall(
            "SetInputValue(QString, QString)", "단위구분", "1")
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "빅콤보프로그램조회", "opt10059", 0, "0713")

    def _on_tr_prog(self, screen, rqname, trcode, recordname, prev_next, *args):
        if rqname != "빅콤보프로그램조회":
            return
        code = self.prog_queue[self.prog_idx]
        if code not in self.candidates:
            QTimer.singleShot(200, lambda: self._scan_prog(self.prog_idx + 1))
            return
        try:
            buy_str  = self.kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, 0, "프로그램매수수량").strip()
            sell_str = self.kiwoom.dynamicCall(
                "GetCommData(QString,QString,int,QString)",
                trcode, rqname, 0, "프로그램매도수량").strip()
            net_qty = int(buy_str.replace(',','')) - int(sell_str.replace(',',''))
            self.candidates[code]["prog_qty"] = net_qty
            if net_qty >= BC_PROG_MIN_QTY:
                self.candidates[code]["prog_ok"] = True
                print(f"  [빅콤보] 프로그램 통과: {self.candidates[code]['name']} "
                      f"순매수 {net_qty:,}주")
            else:
                print(f"  [빅콤보] 프로그램 미달: {self.candidates[code]['name']} "
                      f"순매수 {net_qty:,}주")
        except:
            pass
        QTimer.singleShot(200, lambda: self._scan_prog(self.prog_idx + 1))

    def _phase4_done(self):
        self.candidates = {
            c: d for c, d in self.candidates.items() if d["prog_ok"]
        }
        print(f"[모듈5] PHASE4 완료. 프로그램 통과: {len(self.candidates)}개 → 뉴스 판별")
        if not self.candidates:
            return
        # 뉴스 판별 (Gemini) → 순차 처리
        codes = list(self.candidates.keys())
        self._check_news(codes, 0)

    # ==========================================================
    # PHASE5: Gemini 뉴스 빅이벤트 판별
    # ==========================================================
    def _check_news(self, codes: list, idx: int):
        if idx >= len(codes):
            self._phase5_done()
            return
        code = codes[idx]
        if code not in self.candidates:
            QTimer.singleShot(500, lambda: self._check_news(codes, idx + 1))
            return
        name = self.candidates[code]["name"]
        print(f"  [빅콤보] Gemini 뉴스 판별: {name}")

        def do_gemini():
            try:
                prompt = (
                    f"오늘 날짜 기준으로 '{name}' 종목의 최신 주요 뉴스를 검색해서 분석해줘.\n"
                    f"다음 기준으로 판단해:\n"
                    f"- 공시 (실적 서프라이즈, 대규모 수주, M&A, 지분 취득, 유니콘 투자 등)\n"
                    f"- 정책 수혜 (정부 지원, 규제 완화 등)\n"
                    f"- 업황 모멘텀 (섹터 강세, 수출 호조 등)\n\n"
                    f"응답 형식 (JSON만, 다른 텍스트 없이):\n"
                    f'{{"is_big_event": true/false, '
                    f'"score": 1~10, '
                    f'"summary": "한줄 요약", '
                    f'"event_type": "공시/정책/업황/없음"}}'
                )
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300}
                }
                resp = requests.post(GEMINI_URL, json=payload, timeout=20)
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                text = text.replace("```json", "").replace("```", "").strip()

                import json
                result = json.loads(text)
                is_big  = result.get("is_big_event", False)
                score   = result.get("score", 0)
                summary = result.get("summary", "")
                etype   = result.get("event_type", "없음")

                self.candidates[code]["news_ok"]      = is_big
                self.candidates[code]["news_score"]   = score
                self.candidates[code]["news_summary"]  = summary
                self.candidates[code]["news_type"]     = etype
                print(f"    → {'✅ 빅이벤트' if is_big else '❌ 일반'} "
                      f"점수:{score} {summary[:30]}")

            except Exception as e:
                print(f"    → Gemini 오류: {e} - 뉴스 없음으로 처리")
                self.candidates[code]["news_ok"] = False

            QTimer.singleShot(500, lambda: self._check_news(codes, idx + 1))

        # Qt 이벤트 루프 블로킹 방지: singleShot으로 실행
        QTimer.singleShot(100, do_gemini)

    def _phase5_done(self):
        # 빅이벤트 아니어도 점수 높으면 통과 (score >= 6)
        final = {}
        for code, d in self.candidates.items():
            news_score = d.get("news_score", 0)
            if d["news_ok"] or news_score >= 6:
                d["total_score"] = d["score"] + news_score
                final[code] = d

        # 점수 순 정렬
        final = dict(sorted(final.items(),
                            key=lambda x: x[1]["total_score"], reverse=True))

        print(f"[모듈5] PHASE5 완료. 최종 후보: {len(final)}개")
        if not final:
            send_telegram("[모듈5] 빅콤보 - 오늘은 해당 종목 없음")
            return

        # 이미 진입한 종목 제외
        for code in list(final.keys()):
            if code in self.status and self.status[code].get("entered"):
                final.pop(code, None)

        if not final:
            return

        self._enter_all(final)

    # ==========================================================
    # 진입
    # ==========================================================
    def _enter_all(self, final: dict):
        remaining = BC_MAX_POSITIONS - len(
            [s for s in self.status.values() if s.get("entered")])
        if remaining <= 0:
            send_telegram("[모듈5] 최대 보유 종목 도달 - 진입 스킵")
            return

        entered_count = 0
        for code, d in final.items():
            if entered_count >= remaining:
                break
            self._enter_one(code, d)
            entered_count += 1

    def _enter_one(self, code: str, d: dict):
        name      = d["name"]
        price     = d["price"]
        qty       = max(1, BC_ENTRY_AMOUNT // price)
        gap_str   = f"{d['gap_rate']*100:+.1f}%"
        sijeo_str = " 🔥시=저" if d["is_sijeo"] else ""
        news_str  = d.get("news_summary", "뉴스 없음")

        # 키움 시장가 매수
        ok = tm.enter_position(
            code, name, price,
            condition="빅콤보",
            order_type="market",
            limit_price=0,
            entry_amount=BC_ENTRY_AMOUNT
        )

        if ok:
            self.status[code] = {
                "name":        name,
                "entry_price": price,
                "entry_time":  datetime.now(),
                "entered":     True,
                "add_done":    False,
                "max_price":   price,
                "qty":         qty,
                "score":       d["total_score"],
            }

            send_telegram(
                f"<b>🚀 [빅콤보] 1차 진입!</b>\n"
                f"• {name}{sijeo_str}\n"
                f"  갭: {gap_str}  점수: {d['total_score']}점\n"
                f"  프로그램: +{d['prog_qty']:,}주\n"
                f"  진입가: {price:,}원  수량: {qty}주\n"
                f"  뉴스: {news_str}\n"
                f"  ─────────────────\n"
                f"  30분 내 -2% 눌림 시 추매 알림 예정"
            )

            # 30분 후 추매 체크
            QTimer.singleShot(
                BC_ADD_MINUTES * 60 * 1000,
                lambda c=code: self._check_add(c)
            )
            # 실시간 체결가 구독
            self._subscribe_realtime(code)

    def _subscribe_realtime(self, code: str):
        screen = self._next_screen()
        self.kiwoom.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            screen, code, "10;20", "1"
        )

    # ==========================================================
    # 실시간 체결가 수신 (on_realtime에서 호출)
    # ==========================================================
    def on_realtime(self, code: str, real_type: str):
        if code not in self.status:
            return
        if real_type != "주식체결":
            return
        try:
            price_str = self.kiwoom.dynamicCall(
                "GetCommRealData(QString, int)", real_type, 10)
            price = abs(int(price_str.strip()))
        except:
            return

        s = self.status[code]
        if price > s["max_price"]:
            s["max_price"] = price

        # 손절 체크 (-2.5%)
        entry = s["entry_price"]
        if price <= entry * (1 + STOP_LOSS_RATE):
            self._exit_position(code, price, "손절")

    # ==========================================================
    # 2차 추매 체크 (30분 후)
    # ==========================================================
    def _check_add(self, code: str):
        if code not in self.status:
            return
        s = self.status[code]
        if s["add_done"] or not s["entered"]:
            return

        entry = s["entry_price"]
        # 현재가 캐시에서 가져오기
        current = tm.kiwoom_realtime_cache.get(code, entry)
        rate    = (current - entry) / entry

        if BC_ADD_RATE <= rate < 0:
            # -2% 이내 눌림 → 텔레그램으로 승인 요청
            name = s["name"]
            send_telegram(
                f"<b>⚡ [빅콤보] 추매 승인 요청</b>\n"
                f"• {name}\n"
                f"  1차진입가: {entry:,}원\n"
                f"  현재가: {current:,}원 ({rate*100:+.1f}%)\n"
                f"  추매금액: {BC_ADD_AMOUNT:,}원\n\n"
                f"  👉 추매하시려면 텔레그램에 <b>'추매 {name}'</b> 입력"
            )
        else:
            print(f"  [빅콤보] {s['name']} 추매 조건 미달 ({rate*100:+.1f}%)")

    def execute_add_buy(self, name: str):
        """텔레그램 명령으로 추매 실행"""
        code = self.name_to_code.get(name)
        if not code or code not in self.status:
            send_telegram(f"[빅콤보] '{name}' 추매 대상 없음")
            return
        s = self.status[code]
        if s["add_done"]:
            send_telegram(f"[빅콤보] {name} 이미 추매 완료")
            return

        current = tm.kiwoom_realtime_cache.get(code, s["entry_price"])
        qty     = max(1, BC_ADD_AMOUNT // current)

        ok = tm.enter_position(
            code, name, current,
            condition="빅콤보추매",
            order_type="market",
            limit_price=0,
            entry_amount=BC_ADD_AMOUNT
        )
        if ok:
            s["add_done"] = True
            send_telegram(
                f"<b>✅ [빅콤보] 추매 완료!</b>\n"
                f"• {name}\n"
                f"  추매가: {current:,}원  수량: {qty}주\n"
                f"  추매금액: {BC_ADD_AMOUNT:,}원"
            )

    # ==========================================================
    # 청산
    # ==========================================================
    def _exit_position(self, code: str, price: int, reason: str):
        if code not in self.status:
            return
        s    = self.status.pop(code)
        name = s["name"]
        entry= s["entry_price"]
        rate = (price - entry) / entry * 100
        elapsed = int((datetime.now() - s["entry_time"]).total_seconds() / 60)

        tm.exit_position(code, name, reason="빅콤보_" + reason)
        send_telegram(
            f"<b>[빅콤보] 청산 - {reason}</b>\n"
            f"• {name}\n"
            f"  진입가: {entry:,}원 → 청산가: {price:,}원\n"
            f"  수익률: {rate:+.2f}%\n"
            f"  보유시간: {elapsed}분"
        )

    def setup_force_exit(self):
        """14:50 일괄 청산 타이머"""
        now_dt = datetime.now()
        target = now_dt.replace(hour=14, minute=50, second=0, microsecond=0)
        diff_ms = max(0, int((target - now_dt).total_seconds() * 1000))
        QTimer.singleShot(diff_ms, self._force_exit_all)
        print(f"[모듈5] 14:50 일괄청산 타이머 설정")

    def _force_exit_all(self):
        if not self.status:
            return
        names = [s["name"] for s in self.status.values()]
        send_telegram(
            f"<b>[빅콤보] 14:50 일괄 청산</b>\n"
            + "\n".join(f"• {n}" for n in names)
        )
        for code in list(self.status.keys()):
            price = tm.kiwoom_realtime_cache.get(code, 0)
            self._exit_position(code, price, "14:50일괄청산")