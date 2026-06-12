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
from modules.module1_sector  import Module1Sector
from modules.module2_hwasa   import Module2Hwasa
from modules.module2_gdjum   import Module2Gdjum
from modules.module3_closing import Module3Closing
from modules.module4_chalna  import Module4Chalna
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
    global mod1, mod2_hw, mod2_gj, mod3, mod4, _condition_ok
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

    # 모듈 초기화
    mod1    = Module1Sector(kiwoom)
    mod2_hw = Module2Hwasa(kiwoom)
    mod2_gj = Module2Gdjum(kiwoom)
    mod3    = Module3Closing(kiwoom, condition_list)
    mod4    = Module4Chalna(kiwoom)

    mod1.set_condition_list(condition_list)

    # 타이머 설정
    timer_m1.timeout.connect(on_interval_m1)
    timer_m1.start(M1_INTERVAL)
    print("모듈1 15분 타이머 시작")

    # 체결 이벤트
    kiwoom.OnReceiveChejanData.connect(on_chejan)

    # 14:50 일괄청산
    tm.setup_force_exit_timer()

    # 모듈3 타이머 (테스트: 비활성화)
    # QTimer.singleShot(1500, mod3.setup_timer)

    # 모듈4 시작 (테스트: 비활성화)
    # QTimer.singleShot(2000, mod4.start)

    # 모듈1 초기 실행 → 완료 후 전일고점돌파 실시간 감시 등록
    QTimer.singleShot(10000, lambda: mod1.start_scan(on_complete=register_realtime_conditions))

    print("전체 모듈 초기화 완료!")
    send_telegram("<b>하이퍼 트레이딩 [테스트모드]</b>\n모듈1 스캔 완료 후 모듈2 조건검색 활성 예정")

# =============================================================
# 모듈1 타이머
# =============================================================
def on_interval_m1():
    print(f"\n[{datetime.now().strftime('%H:%M')}] 모듈1 타이머 발동!")
    if is_m1_open():
        mod1.start_scan()
    else:
        print("모듈1 장외 - 스킵")

# =============================================================
# 모듈2 실시간 조건검색 등록
# =============================================================
def register_realtime_conditions():
    # 전일고점돌파만 등록 (황사장 비활성화)
    kiwoom.OnReceiveRealCondition.connect(on_realtime_condition)
    kiwoom.OnReceiveTrCondition.connect(on_initial_condition)

    cname = GDJUM_CONDITION
    if cname not in condition_list:
        print(f"  조건식 없음: {cname}")
        return
    cidx = condition_list[cname]

    # 이전 세션 잔류 등록 해제 (크래시 후 OCX 좀비 상태 방지)
    try:
        kiwoom.dynamicCall("SendConditionStop(QString, QString, int)",
                           "0210", cname, int(cidx))
    except Exception:
        pass

    kiwoom.dynamicCall("SendCondition(QString, QString, int, int)",
                       "0210", cname, int(cidx), 1)
    print(f"  실시간 등록: [0210] {cname}")

    mod2_gj.start_monitoring()
    send_telegram(
        "<b>[전일고점돌파] 실시간 감시 시작</b>\n"
        "편입 종목 즉시 브리핑 | 10분 무편입 시 알림"
    )

def on_initial_condition(screen, code_list, condition_name, idx, prev_next):
    """실시간 등록 시 현재 편입 종목 초기 수신"""
    pass   # 초기 편입 종목은 알림 없이 무시

def on_realtime_condition(code, condition_type, condition_name, index, next_cond):
    """실시간 편입/이탈 콜백"""
    now_str = datetime.now().strftime("%H:%M")

    if condition_type == "I":
        # 황사장
        if condition_name == AUTO_TRADE_CONDITION:
            mod2_hw.on_enter(code, now_str)
        # 전일고점돌파
        elif condition_name == GDJUM_CONDITION:
            mod2_gj.on_enter(code, now_str)

    elif condition_type == "D":
        if condition_name == AUTO_TRADE_CONDITION:
            mod2_hw.on_exit(code, now_str)
        elif condition_name == GDJUM_CONDITION:
            mod2_gj.on_exit(code, now_str)

# =============================================================
# 실시간 데이터 수신 (이벤트 허브)
# =============================================================
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