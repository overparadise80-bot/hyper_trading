# -*- coding: utf-8 -*-
"""
condition_kiwoom.py - 하이퍼 트레이딩 메인
키움 API 로그인 + 이벤트 허브 역할
각 모듈로 이벤트 분배
"""

import sys
import os
import json
from datetime import datetime
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer

# =============================================================
# 로그 파일 설정 (터미널 + 파일 동시 출력)
# =============================================================
class _Tee:
    def __init__(self, *streams):
        self._streams = streams
    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
    def flush(self):
        for s in self._streams:
            try: s.flush()
            except Exception: pass
    def fileno(self):
        return self._streams[0].fileno()

def _setup_logging():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"kiwoom_{datetime.now().strftime('%Y%m%d')}.log")
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    print(f"[로그] 파일 저장 시작: {log_path}")

_setup_logging()

# 모듈 import
from modules import trade_manager as tm
from modules.tr_queue        import KiwoomQueue
from modules.module1_sector  import Module1Sector
from modules.module2_hwasa   import Module2Hwasa
from modules.module2_gdjum   import Module2Gdjum
from modules.module3_closing  import Module3Closing
from modules.module4_chalna   import Module4Chalna
from modules.module5_sonsugun import Module5Sonsugun
from modules.common          import (
    send_telegram, M1_INTERVAL, M1_START, M1_END,
    M2_START, M2_END, AUTO_TRADE_CONDITION, GDJUM_CONDITION,
    is_m1_open, is_m2_open
)

# =============================================================
# Qt 앱 + 키움 API
# =============================================================
app    = QApplication(sys.argv)
kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

# trade_manager에 kiwoom 주입
tm.set_kiwoom(kiwoom)

# =============================================================
# 모듈 인스턴스 (조건식 로드 후 초기화)
# =============================================================
mod1    = None
mod2_hw = None
mod2_gj = None
mod3    = None
mod4    = None
mod5    = None

# =============================================================
# 전역 타이머
# =============================================================
timer_m1       = QTimer()   # 15분 주기
condition_list = {}

# 로그인 완료 여부 플래그
_login_ok      = False
_condition_ok  = False

# =============================================================
# ★ 로그인 타임아웃 감시 (60초)
# - CommConnect() 후 60초 내 on_login이 안 오면 종료
# =============================================================
def _safe_quit(msg: str = ""):
    """CommTerminate → app.quit() — OCX 상태 정리 후 종료"""
    try:
        kiwoom.dynamicCall("CommTerminate()")
    except Exception:
        pass
    if msg:
        print(msg)
        send_telegram(msg)
    app.quit()

def _login_timeout():
    if not _login_ok:
        _safe_quit(
            "⚠️ 키움 로그인 응답 없음 (60초 타임아웃)\n"
            "영웅문이 이미 실행 중이거나 자동 로그인 팝업 확인 필요\n"
            "→ 재시작 대기 중..."
        )

login_timeout_timer = QTimer()
login_timeout_timer.setSingleShot(True)
login_timeout_timer.timeout.connect(_login_timeout)
login_timeout_timer.start(60 * 1000)   # 60초

# =============================================================
# ★ 조건식 로드 타임아웃 감시 (30초)
# - GetConditionLoad() 후 30초 내 on_condition_load가 안 오면 종료
# =============================================================
condition_timeout_timer = QTimer()
condition_timeout_timer.setSingleShot(True)

def _condition_timeout():
    if not _condition_ok:
        _safe_quit(
            "⚠️ 조건식 로드 응답 없음 (30초 타임아웃)\n"
            "키움 서버 응답 지연 가능성\n"
            "→ 재시작 대기 중..."
        )

condition_timeout_timer.timeout.connect(_condition_timeout)

# =============================================================
# 로그인
# =============================================================
def on_login(err_code: int):
    global _login_ok
    login_timeout_timer.stop()   # ★ 타임아웃 감시 해제

    if err_code == 0:
        _login_ok = True
        print("로그인 성공!")
        # 조건식 로드 타임아웃 감시 시작 (30초)
        condition_timeout_timer.start(30 * 1000)
        QTimer.singleShot(1000, lambda:
            kiwoom.dynamicCall("GetConditionLoad()"))
    else:
        print(f"로그인 실패: {err_code}")
        QTimer.singleShot(3000, lambda: _safe_quit(
            f"❌ 키움 로그인 실패 (코드: {err_code})\n"
            f"영웅문 상태 확인 후 재시작 필요"
        ))

# =============================================================
# 조건식 로드 완료
# =============================================================
def on_condition_load():
    global mod1, mod2_hw, mod2_gj, mod3, mod4, mod5, _condition_ok
    condition_timeout_timer.stop()   # ★ 타임아웃 감시 해제
    _condition_ok = True

    result = kiwoom.dynamicCall("GetConditionNameList()")
    for c in result.split(';'):
        if not c:
            continue
        parts = c.split('^')
        if len(parts) >= 2:
            condition_list[parts[1]] = parts[0]
    print(f"조건식 로드: {list(condition_list.keys())}")

    # 조건식이 하나도 없으면 비정상
    if not condition_list:
        QTimer.singleShot(3000, lambda: _safe_quit(
            "⚠️ 조건식 목록이 비어 있음\n"
            "키움 HTS에서 조건식 저장 상태 확인 필요\n"
            "→ 재시작 대기 중..."
        ))
        return

    # 글로벌 TR 큐 생성 (모듈1/2 OCX 호출 직렬화)
    queue   = KiwoomQueue()

    # 모듈 초기화
    mod1    = Module1Sector(kiwoom, queue)
    mod2_hw = Module2Hwasa(kiwoom, queue)
    mod2_gj = Module2Gdjum(kiwoom, queue)
    mod3    = Module3Closing(kiwoom, condition_list)
    mod4    = Module4Chalna(kiwoom)
    mod5    = Module5Sonsugun(kiwoom, queue, mod1)

    mod1.set_condition_list(condition_list)
    mod1.set_sonsugun(mod5)

    # 타이머 설정
    timer_m1.timeout.connect(on_interval_m1)
    timer_m1.start(M1_INTERVAL)
    print("모듈1 15분 타이머 시작")

    # 체결 이벤트
    kiwoom.OnReceiveChejanData.connect(on_chejan)

    # 14:50 일괄청산
    tm.setup_force_exit_timer()

    QTimer.singleShot(1500, mod3.setup_timer)

    # 모듈4 시작 (테스트: 비활성화)
    # QTimer.singleShot(2000, mod4.start)

    # 모듈5 시작 (장 시작 후 5초 뒤 첫 top100 조회)
    QTimer.singleShot(8000, mod5.start)

    # 모듈1 초기 실행 → 완료 후 조건검색 등록 + 주도주 실시간 구독
    def _on_first_scan_done():
        register_realtime_conditions()
        # refresh_sector_realtime()  # DIAGNOSTIC: 주도주 실시간 구독 비활성화
    QTimer.singleShot(10000, lambda: mod1.start_scan(on_complete=_on_first_scan_done))

    print("전체 모듈 초기화 완료!")
    send_telegram("<b>하이퍼 트레이딩 [테스트모드]</b>\n모듈1 스캔 완료 후 모듈2 조건검색 활성 예정")

# =============================================================
# prices.json 갱신 (30초마다 실시간 가격 → UI 폴링용)
# =============================================================
def _write_prices_json():
    if not mod1 or not mod1.theme_ranking:
        return
    try:
        prices = {}
        for t in mod1.theme_ranking[:7]:
            for s in t["stocks"]:
                code       = s["code"]
                live_price = tm.kiwoom_realtime_cache.get(code)
                prev_close = s.get("prev_close", 0)
                if live_price and prev_close > 0:
                    live_rate = (live_price - prev_close) / prev_close * 100
                    extra = kiwoom_live_cache.get(code, {})
                    def to_rate(p):
                        return round((p - prev_close) / prev_close * 100, 2) if p > 0 else 0
                    prices[code] = {
                        "price":     live_price,
                        "rate":      round(live_rate, 2),
                        "prog":      s.get("prog", 0),
                        "amt":       extra.get("amt", s.get("amount", 0)),
                        "open_rate": to_rate(extra["open_p"]) if extra.get("open_p") else s.get("open_rate", 0),
                        "high_rate": to_rate(extra["high_p"]) if extra.get("high_p") else s.get("high_rate", 0),
                        "low_rate":  to_rate(extra["low_p"])  if extra.get("low_p")  else s.get("low_rate",  0),
                    }
        import json as _json
        with open("prices.json", "w", encoding="utf-8") as f:
            _json.dump({
                "updated": datetime.now().strftime("%H:%M:%S"),
                "prices":  prices,
            }, f)
    except Exception as e:
        print(f"[prices.json] 갱신 오류: {e}")

timer_prices = QTimer()
timer_prices.timeout.connect(_write_prices_json)
timer_prices.start(30 * 1000)

# =============================================================
# 주도주 실시간 체결 구독 관리
# =============================================================
REALTIME_SCREEN = "0900"
_subscribed_codes = set()

# 조건검색 폴링 상태 (is_realtime=0 방식)
_hw_active_codes: set = set()
_gj_active_codes: set = set()
_hw_initialized  = False   # 첫 폴링은 조용히 초기화만
_gj_initialized  = False
_condition_poll_timer = None

def refresh_sector_realtime():
    """모듈1 스캔 완료 후 주도주 종목 실시간 체결 구독 교체"""
    global _subscribed_codes
    if not mod1 or not mod1.theme_ranking:
        return

    new_codes = {
        s["code"]
        for t in mod1.theme_ranking[:7]
        for s in t["stocks"][:5]  # UI 표시 종목(top5)과 동일하게 제한
    }

    # 빠진 종목 구독 해제
    removed = _subscribed_codes - new_codes
    for code in removed:
        kiwoom.dynamicCall(
            "SetRealRemove(QString,QString)", REALTIME_SCREEN, code)

    # 새로 들어온 종목 구독 등록 - 10개씩 배치, 500ms 간격 (OCX 스택 오버런 방지)
    added = list(new_codes - _subscribed_codes)
    _subscribed_codes = new_codes

    _BATCH = 10
    batches = [added[i:i+_BATCH] for i in range(0, len(added), _BATCH)]

    def _reg_batch(idx):
        if idx >= len(batches):
            print(f"[실시간] 주도주 구독 갱신: +{len(added)}개 -{len(removed)}개 (총 {len(new_codes)}개)")
            return
        kiwoom.dynamicCall(
            "SetRealReg(QString,QString,QString,QString)",
            REALTIME_SCREEN, ";".join(batches[idx]), "10;11", "1")
        QTimer.singleShot(500, lambda: _reg_batch(idx + 1))

    if added:
        _reg_batch(0)
    else:
        print(f"[실시간] 주도주 구독 갱신: +0개 -{len(removed)}개 (총 {len(new_codes)}개)")

# =============================================================
# 모듈1 타이머
# =============================================================
def on_interval_m1():
    print(f"\n[{datetime.now().strftime('%H:%M')}] 모듈1 타이머 발동!")
    if is_m1_open():
        mod1.start_scan()  # SetRealReg 호출 없이 스캔만 (OCX 안정성 우선)
    else:
        print("모듈1 장외 - 스킵")

# =============================================================
# 모듈2 실시간 조건검색 등록
# =============================================================
def _register_condition(screen: str, cname: str, cidx: str):
    """SendConditionStop → SendCondition 등록 (좀비 방지)"""
    try:
        kiwoom.dynamicCall("SendConditionStop(QString, QString, int)",
                           screen, cname, int(cidx))
    except Exception:
        pass
    kiwoom.dynamicCall("SendCondition(QString, QString, int, int)",
                       screen, cname, int(cidx), 1)
    print(f"  실시간 등록: [{screen}] {cname}")

def _poll_conditions():
    """조건검색 폴링 1회 (is_realtime=0 — OCX 스택 오버런 방지)"""
    hw = AUTO_TRADE_CONDITION
    gj = GDJUM_CONDITION
    if hw in condition_list:
        kiwoom.dynamicCall("SendConditionStop(QString,QString,int)",
                           "0211", hw, int(condition_list[hw]))
        kiwoom.dynamicCall("SendCondition(QString,QString,int,int)",
                           "0211", hw, int(condition_list[hw]), 0)
    def _poll_gj():
        if gj in condition_list:
            kiwoom.dynamicCall("SendConditionStop(QString,QString,int)",
                               "0210", gj, int(condition_list[gj]))
            kiwoom.dynamicCall("SendCondition(QString,QString,int,int)",
                               "0210", gj, int(condition_list[gj]), 0)
    QTimer.singleShot(300, _poll_gj)

def register_realtime_conditions():
    global _condition_poll_timer
    # is_realtime=1 제거 — OnReceiveRealCondition 폭주가 OCX 스택 오버런 원인
    kiwoom.OnReceiveTrCondition.connect(on_initial_condition)

    mod2_hw.start_monitoring()
    mod2_gj.start_monitoring()
    send_telegram(
        "<b>[조건검색] 폴링 감시 시작 (30초 간격)</b>\n"
        "• 황사장 | 전일고점돌파\n"
        "편입 즉시 브리핑 | 10분 무편입 시 알림"
    )

    _poll_conditions()  # 즉시 1회
    _condition_poll_timer = QTimer()
    _condition_poll_timer.timeout.connect(_poll_conditions)
    _condition_poll_timer.start(5 * 1000)
    print("  [조건검색] 폴링 모드 5초 간격 시작")

    # 15:25 일일 조건검색 이력 요약 전송
    _schedule_daily_summary()

def _schedule_daily_summary():
    """15:25에 당일 황사장/전일고점돌파 전체 편입·이탈 이력을 텔레그램으로 전송"""
    from datetime import time as dtime
    now_dt = datetime.now()
    target = now_dt.replace(hour=15, minute=25, second=0, microsecond=0)
    if now_dt >= target:
        return  # 이미 지난 시각이면 스킵
    ms = int((target - now_dt).total_seconds() * 1000)
    QTimer.singleShot(ms, _send_daily_summary)
    print(f"  [일일요약] 15:25 전송 예약 ({ms // 60000}분 후)")

def _send_daily_summary():
    today = datetime.now().strftime("%m/%d")
    header = f"<b>📋 조건검색 일일 이력 ({today})</b>\n{'─' * 20}"
    hw_text = mod2_hw.get_daily_summary() if mod2_hw else "황사장 데이터 없음"
    gj_text = mod2_gj.get_daily_summary() if mod2_gj else "전일고점 데이터 없음"
    send_telegram(f"{header}\n\n{hw_text}\n\n{gj_text}")
    print("  [일일요약] 조건검색 이력 전송 완료")

def on_initial_condition(screen, code_list, condition_name, idx, prev_next):
    """폴링 결과 수신 — 이전 목록과 비교해 신규 편입/이탈만 처리"""
    global _hw_active_codes, _gj_active_codes, _hw_initialized, _gj_initialized
    codes = {c for c in code_list.split(";") if c.strip()}
    now_s = datetime.now().strftime("%H:%M")

    if condition_name == AUTO_TRADE_CONDITION:
        if not _hw_initialized:
            _hw_active_codes = codes
            _hw_initialized  = True
            print(f"  [황사장] 초기화: 기존 {len(codes)}개 무시 (신규 편입부터 알림)")
            return
        entered = codes - _hw_active_codes
        exited  = _hw_active_codes - codes
        _hw_active_codes = codes
        for code in entered:
            mod2_hw.on_enter(code, now_s)
        for code in exited:
            mod2_hw.on_exit(code, now_s)
        if entered or exited:
            print(f"  [황사장] +{len(entered)} -{len(exited)} (총 {len(codes)})")
    elif condition_name == GDJUM_CONDITION:
        if not _gj_initialized:
            _gj_active_codes = codes
            _gj_initialized  = True
            print(f"  [전일고점] 초기화: 기존 {len(codes)}개 무시 (신규 편입부터 알림)")
            return
        entered = codes - _gj_active_codes
        exited  = _gj_active_codes - codes
        _gj_active_codes = codes
        for code in entered:
            mod2_gj.on_enter(code, now_s)
        for code in exited:
            mod2_gj.on_exit(code, now_s)
        if entered or exited:
            print(f"  [전일고점] +{len(entered)} -{len(exited)} (총 {len(codes)})")

def on_realtime_condition(code, condition_type, condition_name, index, next_cond):
    """미사용 — 폴링 모드로 전환됨 (on_initial_condition 처리)"""
    pass

# =============================================================
# 실시간 데이터 수신 (이벤트 허브)
# =============================================================
# {code: {amt, open_p, high_p, low_p}} — prices.json 갱신에 사용
kiwoom_live_cache = {}

def on_realtime_data(code, real_type, real_data):
    # 모듈4: 찰나의 매매
    if real_type in ("주식호가잔량", "주식체결"):
        mod4.on_realtime(code, real_type)

    # 전일고점돌파 호가 체크
    if real_type == "주식호가잔량":
        mod2_gj.on_realtime_hoga(code, real_type)

    # 포지션 보유 종목 실시간 체결가 (트레일링/손절)
    if real_type == "주식체결":
        # 현재가 캐시 업데이트
        try:
            price_str = kiwoom.dynamicCall(
                "GetCommRealData(QString, int)", real_type, 10)
            price = abs(int(price_str.strip()))
            tm.kiwoom_realtime_cache[code] = price
            # 전일고점돌파 최고가 업데이트
            mod2_gj.on_realtime_price(code, price)
        except:
            pass
        # 트레일링/손절
        tm.on_realtime_price(code, real_type, kiwoom)
        # UI용 추가 실시간 캐시 (거래대금·시가·고가·저가)
        try:
            def gd(fid): return kiwoom.dynamicCall("GetCommRealData(QString, int)", real_type, fid).strip()
            amt_man  = abs(int(gd(14)))   # 누적거래대금 (만원)
            open_p   = abs(int(gd(16)))   # 시가
            high_p   = abs(int(gd(17)))   # 고가
            low_p    = abs(int(gd(18)))   # 저가
            kiwoom_live_cache[code] = {
                "amt":    amt_man // 10000,  # 만원 → 억원
                "open_p": open_p,
                "high_p": high_p,
                "low_p":  low_p,
            }
        except:
            pass

# =============================================================
# 체결 이벤트
# =============================================================
def on_chejan(gubun, item_cnt, fid_list):
    tm.on_chejan(gubun, kiwoom)

# =============================================================
# 이벤트 연결 + 실행
# =============================================================
kiwoom.OnEventConnect.connect(on_login)
kiwoom.OnReceiveConditionVer.connect(on_condition_load)
kiwoom.OnReceiveRealData.connect(on_realtime_data)

try:
    kiwoom.dynamicCall("CommConnect()")
    sys.exit(app.exec_())
except Exception as e:
    try:
        kiwoom.dynamicCall("CommTerminate()")
    except Exception:
        pass
    send_telegram(f"❌ condition_kiwoom 치명적 오류: {e}")
    sys.exit(1)