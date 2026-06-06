# -*- coding: utf-8 -*-
"""
main.py - 하이퍼 트레이딩 시스템 구동부
실행: python main.py

자동 구동:
  - telegram_bot.py    : 즉시 (상시 구동 - Gemini 지능형 비서)
  - channel_monitor.py : 즉시 (상시 구동 - 텔레그램 채널 모니터링)
  - morning_briefing.py: 07:50 (해외시황 + 리서치 브리핑)
  - condition_kiwoom.py: 08:00 (모듈1~4 자동매매 - venv32 전용)
  - primary_notice.py  : 20:00 (DART 공시 브리핑)
"""

import subprocess
import threading
import time
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# =============================================================
# 경로 설정
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 일반 Python (현재 가상환경)
PYTHON_NORMAL = sys.executable

# 32비트 Python (키움 API 전용)
PYTHON_32 = r"C:\HyperTrading\venv32\Scripts\python.exe"

# venv32 없으면 현재 Python으로 대체 (경고 출력)
if not os.path.exists(PYTHON_32):
    print(f"[경고] venv32 없음: {PYTHON_32}")
    print(f"  condition_kiwoom.py는 수동으로 venv32에서 실행하세요!")
    PYTHON_32 = PYTHON_NORMAL

# =============================================================
# 스크립트 설정 (파일경로, 사용할 Python)
# =============================================================
SCRIPTS = {
    # (스크립트경로, 사용할Python)
    "condition_kiwoom":  (
        os.path.join(BASE_DIR, "condition_kiwoom_v2.py"),
        PYTHON_32       # 반드시 32비트 Python
    ),
    "morning_briefing":  (
        os.path.join(BASE_DIR, "morning_briefing.py"),
        PYTHON_NORMAL
    ),
    "primary_notice":    (
        os.path.join(BASE_DIR, "primary_notice.py"),
        PYTHON_NORMAL
    ),
    "channel_monitor":   (
        os.path.join(BASE_DIR, "channel_monitor.py"),
        PYTHON_NORMAL
    ),
}

# 실행 중인 프로세스 관리
processes = {}

# =============================================================
# 유틸
# =============================================================
def now() -> str:
    return datetime.now().strftime("%H:%M:%S")

def today() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

def is_weekday() -> bool:
    return datetime.now().weekday() < 5   # 월~금

def should_run(job_name: str, hour: int, minute: int) -> bool:
    """해당 시각이 됐고, 오늘 아직 실행 안 됐으면 True"""
    now_dt  = datetime.now()
    today_d = now_dt.date()
    cur_hm  = (now_dt.hour, now_dt.minute)

    if cur_hm != (hour, minute):
        return False
    if _last_triggered.get(job_name) == today_d:
        return False
    return True

def mark_done(job_name: str):
    _last_triggered[job_name] = datetime.now().date()

_last_triggered = {}

# =============================================================
# 프로세스 실행 / 종료
# =============================================================
def run_script(name: str):
    """스크립트 실행 (이미 실행 중이면 스킵)"""
    if name in processes and processes[name].poll() is None:
        print(f"[{now()}] {name} 이미 실행 중 - 스킵")
        return

    if name not in SCRIPTS:
        print(f"[{now()}] {name} 설정 없음")
        return

    script_path, python_exe = SCRIPTS[name]

    if not os.path.exists(script_path):
        print(f"[{now()}] 파일 없음: {script_path}")
        return

    if not os.path.exists(python_exe):
        print(f"[{now()}] Python 없음: {python_exe}")
        return

    try:
        proc = subprocess.Popen(
            [python_exe, script_path],
            creationflags=subprocess.CREATE_NEW_CONSOLE   # Windows 새 창
        )
        processes[name] = proc
        print(f"[{now()}] {name} 시작 (PID: {proc.pid}, Python: {os.path.basename(python_exe)})")
    except Exception as e:
        print(f"[{now()}] {name} 실행 오류: {e}")

def stop_script(name: str):
    """스크립트 종료"""
    if name in processes and processes[name].poll() is None:
        processes[name].terminate()
        print(f"[{now()}] {name} 종료")
        processes.pop(name, None)

# =============================================================
# 스케줄 루프 (1분마다 체크)
# =============================================================
def schedule_loop():
    print(f"[{now()}] 스케줄러 시작")

    while True:
        try:
            if is_weekday():

                # 07:50 - 모닝 브리핑
                if should_run("morning_briefing", 7, 50):
                    print(f"[{now()}] 모닝 브리핑 시작!")
                    run_script("morning_briefing")
                    mark_done("morning_briefing")

                # 08:00 - 자동매매 시작 (venv32)
                if should_run("condition_kiwoom", 8, 0):
                    print(f"[{now()}] 자동매매 시작! (venv32)")
                    run_script("condition_kiwoom")
                    mark_done("condition_kiwoom")

                # 20:00 - 공시 브리핑
                if should_run("primary_notice", 20, 0):
                    print(f"[{now()}] 공시 브리핑 시작!")
                    run_script("primary_notice")
                    mark_done("primary_notice")

            # 프로세스 상태 모니터링 (비정상 종료 감지)
            for name, proc in list(processes.items()):
                if proc.poll() is not None:
                    exit_code = proc.poll()
                    print(f"[{now()}] 경고: {name} 종료됨 (코드: {exit_code})")
                    processes.pop(name, None)

                    # channel_monitor / telegram_bot은 자동 재시작
                    if name in ("channel_monitor",) and is_weekday():
                        print(f"[{now()}] {name} 자동 재시작...")
                        time.sleep(5)
                        run_script(name)

        except Exception as e:
            print(f"[{now()}] 스케줄 오류: {e}")

        time.sleep(60)   # 1분 대기

# =============================================================
# 즉시 실행 체크 (프로그램 시작 시 현재 시간대 확인)
# =============================================================
def check_immediate():
    """시작 시각에 따라 즉시 실행할 것들 체크"""
    now_dt  = datetime.now()
    now_hm  = (now_dt.hour, now_dt.minute)

    if not is_weekday():
        print(f"[{now()}] 주말 - 자동매매 스킵")
        return

    # 07:50~08:01 사이 시작 시 모닝 브리핑 즉시 실행
    if (7, 50) <= now_hm <= (8, 1):
        print(f"[{now()}] 모닝 브리핑 즉시 실행!")
        run_script("morning_briefing")
        mark_done("morning_briefing")

    # 08:00~15:30 사이 시작 시 자동매매 즉시 실행
    if (8, 0) <= now_hm <= (15, 30):
        print(f"[{now()}] 자동매매 즉시 실행! (venv32)")
        run_script("condition_kiwoom")
        mark_done("condition_kiwoom")

    # 20:00~20:30 사이 시작 시 공시 브리핑 즉시 실행
    if (20, 0) <= now_hm <= (20, 30):
        print(f"[{now()}] 공시 브리핑 즉시 실행!")
        run_script("primary_notice")
        mark_done("primary_notice")

# =============================================================
# 메인
# =============================================================
def main():
    print("=" * 55)
    print("  하이퍼 트레이딩 시스템")
    print(f"  {today()}")
    print(f"  Python: {PYTHON_NORMAL}")
    print(f"  venv32: {PYTHON_32}")
    print("=" * 55)

    # 1. Gemini 텔레그램 봇 (메인 스레드 내 별도 스레드)
    print(f"[{now()}] Gemini 텔레그램 봇 시작...")
    try:
        from telegram_bot import start_bot
        bot_thread = threading.Thread(target=start_bot, daemon=True)
        bot_thread.start()
        print(f"[{now()}] 텔레그램 봇 스레드 시작 완료")
    except Exception as e:
        print(f"[{now()}] 텔레그램 봇 오류: {e}")
    time.sleep(2)

    # 2. 채널 모니터 (별도 프로세스 - 상시)
    print(f"[{now()}] 채널 모니터 시작...")
    run_script("channel_monitor")
    time.sleep(2)

    # 3. 즉시 실행 체크
    check_immediate()

    # 4. 스케줄 루프 (메인 스레드에서 실행)
    print(f"\n[{now()}] 스케줄 대기 중...")
    print(f"  07:50 → morning_briefing.py")
    print(f"  08:00 → condition_kiwoom_v2.py (venv32)")
    print(f"  20:00 → primary_notice.py")
    print(f"  Ctrl+C로 종료\n")
    schedule_loop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n[{now()}] 시스템 종료 중...")
        for name in list(processes.keys()):
            stop_script(name)
        print(f"[{now()}] 종료 완료")