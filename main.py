# -*- coding: utf-8 -*-
"""
main.py - 하이퍼 트레이딩 시스템 구동부

실행: python main.py

★ 실행 순서 주의:
   1. main.py 먼저 실행
   2. 키움 영웅문 HTS는 condition_kiwoom이 로그인 완료 후 실행
      (영웅문 먼저 켜면 OCX 로그인 충돌 가능)

자동 구동:
- telegram_bot.py : 즉시 (상시 구동 - Gemini 지능형 비서, 자동 재시작)
- channel_monitor.py : 즉시 (상시 구동 - 텔레그램 채널 모니터링)
- morning_briefing.py: 07:50 (해외시황 + 리서치 브리핑)
- condition_kiwoom.py: 08:00 (모듈1~4 자동매매 - venv32 전용)
- primary_notice.py : 20:00 (DART 공시 브리핑)
"""

import subprocess
import threading
import time
import os
import sys
import atexit
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# =============================================================
# ★ 단일 인스턴스 잠금 (중복 실행 방지)
# =============================================================
_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "main.lock")

def _is_pid_alive(pid: int) -> bool:
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ['tasklist', '/FI', f'PID eq {pid}', '/NH', '/FO', 'CSV'],
            text=True, stderr=_sp.DEVNULL, timeout=5,
            creationflags=0x08000000  # CREATE_NO_WINDOW
        )
        return str(pid) in out
    except Exception:
        return False

def _check_single_instance():
    os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)
    if os.path.exists(_LOCK_FILE):
        try:
            with open(_LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            if pid != os.getpid() and _is_pid_alive(pid):
                print(f"[경고] main.py 이미 실행 중 (PID: {pid}) - 중복 실행 차단")
                sys.exit(0)
        except Exception:
            pass
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def _release_lock():
    try:
        if os.path.exists(_LOCK_FILE):
            with open(_LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(_LOCK_FILE)
    except Exception:
        pass

# venv32 체크를 _check_single_instance보다 먼저 실행
# → 시스템Python이면 락파일 건드리지 않고 즉시 재실행
_VENV32_EARLY = r"C:\HyperTrading\venv32\Scripts\python.exe"
if os.path.exists(_VENV32_EARLY):
    if os.path.abspath(sys.executable).lower() != os.path.abspath(_VENV32_EARLY).lower():
        print(f"[재실행] 시스템Python 감지 → venv32로 재시작...")
        subprocess.Popen(
            [_VENV32_EARLY] + sys.argv,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        sys.exit(0)

_check_single_instance()
atexit.register(_release_lock)

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
    log_path = os.path.join(log_dir, f"main_{datetime.now().strftime('%Y%m%d')}.log")
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    print(f"[로그] 파일 저장 시작: {log_path}")

_setup_logging()

# =============================================================
# 경로 설정
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_VENV32 = r"C:\HyperTrading\venv32\Scripts\python.exe"

if os.path.exists(_VENV32):
    PYTHON_NORMAL = _VENV32
    PYTHON_32     = _VENV32
else:
    print(f"[경고] venv32 없음: {_VENV32}")
    print(f"       condition_kiwoom.py는 수동으로 venv32에서 실행하세요!")
    PYTHON_NORMAL = sys.executable
    PYTHON_32     = sys.executable

# =============================================================
# 스크립트 설정
# =============================================================
SCRIPTS = {
    "condition_kiwoom": (
        os.path.join(BASE_DIR, "condition_kiwoom_v2.py"),
        PYTHON_32
    ),
    "morning_briefing": (
        os.path.join(BASE_DIR, "morning_briefing.py"),
        PYTHON_NORMAL
    ),
    "primary_notice": (
        os.path.join(BASE_DIR, "primary_notice.py"),
        PYTHON_NORMAL
    ),
    "channel_monitor": (
        os.path.join(BASE_DIR, "channel_monitor.py"),
        PYTHON_NORMAL
    ),
}

# 실행 중인 프로세스 관리
processes = {}

# ★ condition_kiwoom 재시작 횟수 추적 (무한루프 방지)
_restart_count   = {}
_MAX_RESTART     = 5    # 최대 재시작 횟수
_restart_blocked = set()  # 재시작 차단 목록

# =============================================================
# 유틸
# =============================================================
def now() -> str:
    return datetime.now().strftime("%H:%M:%S")

def today() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

def is_weekday() -> bool:
    return datetime.now().weekday() < 5

def should_run(job_name: str, hour: int, minute: int) -> bool:
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
def _kill_stale(name: str):
    """OS 레벨에서 같은 스크립트를 실행 중인 기존 프로세스를 모두 종료한다.
    main.py 재시작 시 processes 딕셔너리가 초기화되어 추적이 끊기는 문제 방지."""
    if name not in SCRIPTS:
        return
    script_path = SCRIPTS[name][0]
    script_name = os.path.basename(script_path)
    try:
        result = subprocess.check_output(
            ['wmic', 'process', 'where',
             f'CommandLine like "%{script_name}%"',
             'get', 'ProcessId', '/format:csv'],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=0x08000000
        )
        killed = []
        for line in result.strip().splitlines():
            parts = line.strip().split(',')
            if len(parts) >= 2 and parts[-1].strip().isdigit():
                pid = int(parts[-1].strip())
                if pid == os.getpid():
                    continue
                try:
                    subprocess.run(
                        ['taskkill', '/F', '/PID', str(pid)],
                        stderr=subprocess.DEVNULL, timeout=5,
                        creationflags=0x08000000
                    )
                    killed.append(pid)
                except Exception:
                    pass
        if killed:
            print(f"[{now()}] 기존 {name} 프로세스 종료: {killed}")
            time.sleep(1)
    except Exception as e:
        print(f"[{now()}] {name} 기존 프로세스 정리 오류: {e}")

def run_script(name: str):
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

    # 시작 전 기존 인스턴스 강제 종료
    # (main.py 재시작 시 processes 딕셔너리가 초기화되어 중복 실행 방지 불가)
    if name in ("condition_kiwoom", "channel_monitor"):
        _kill_stale(name)

    try:
        if name == "condition_kiwoom":
            # kiwoom은 자체 로그 있음, 별도 콘솔 창 유지
            proc = subprocess.Popen(
                [python_exe, script_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            # 나머지 스크립트는 logs/ 에 출력 캡처
            log_dir  = os.path.join(BASE_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"{name}_{datetime.now().strftime('%Y%m%d')}.log")
            log_file = open(log_path, "a", encoding="utf-8", buffering=1)
            proc = subprocess.Popen(
                [python_exe, "-X", "utf8", script_path],
                stdout=log_file,
                stderr=log_file,
            )
        processes[name] = proc
        print(f"[{now()}] {name} 시작 (PID: {proc.pid})")
    except Exception as e:
        print(f"[{now()}] {name} 실행 오류: {e}")

def stop_script(name: str):
    if name in processes and processes[name].poll() is None:
        processes[name].terminate()
        print(f"[{now()}] {name} 종료")
    processes.pop(name, None)

# =============================================================
# ★ 텔레그램 봇 - 자동 재시작 루프
# =============================================================
def start_bot_with_restart():
    """텔레그램 봇 스레드: 크래시 시 5초 후 자동 재시작"""
    while True:
        try:
            from telegram_bot import start_bot
            print(f"[{now()}] 텔레그램 봇 (재)시작...")
            start_bot()
        except Exception as e:
            print(f"[{now()}] 텔레그램 봇 크래시: {e}")
            print(f"[{now()}] 5초 후 자동 재시작...")
            time.sleep(5)

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
                    # ★ 하루 시작 시 재시작 카운터 초기화
                    _restart_count["condition_kiwoom"] = 0
                    _restart_blocked.discard("condition_kiwoom")
                    run_script("condition_kiwoom")
                    mark_done("condition_kiwoom")

                # 20:00 - 공시 브리핑
                if should_run("primary_notice", 20, 0):
                    print(f"[{now()}] 공시 브리핑 시작!")
                    run_script("primary_notice")
                    mark_done("primary_notice")

            # ★ 프로세스 상태 모니터링 + 자동 재시작
            for name, proc in list(processes.items()):
                if proc.poll() is not None:
                    exit_code = proc.poll()
                    print(f"[{now()}] 경고: {name} 종료됨 (코드: {exit_code})")
                    processes.pop(name, None)

                    # channel_monitor: 항상 재시작
                    if name == "channel_monitor" and is_weekday():
                        print(f"[{now()}] channel_monitor 자동 재시작...")
                        time.sleep(5)
                        run_script(name)

                    # ★ condition_kiwoom: 장중(08:00~15:30)에만 재시작
                    if name == "condition_kiwoom" and is_weekday():
                        now_dt = datetime.now()
                        if (8, 0) <= (now_dt.hour, now_dt.minute) <= (15, 30):

                            # ★ 재시작 차단 여부 확인
                            if name in _restart_blocked:
                                print(f"[{now()}] condition_kiwoom 재시작 차단 중 - 수동 확인 필요")
                                continue

                            # ★ 재시작 횟수 확인
                            cnt = _restart_count.get(name, 0) + 1
                            _restart_count[name] = cnt

                            if cnt > _MAX_RESTART:
                                _restart_blocked.add(name)
                                msg = (
                                    f"🚨 condition_kiwoom 재시작 {cnt}회 초과\n"
                                    f"자동 재시작 중단 - 수동 확인 필요\n"
                                    f"(오늘 하루 재시작 차단)"
                                )
                                print(f"[{now()}] {msg}")
                                # 텔레그램 직접 호출 (common import)
                                try:
                                    from modules.common import send_telegram
                                    send_telegram(msg)
                                except:
                                    pass
                                continue

                            print(f"[{now()}] condition_kiwoom 장중 자동 재시작! ({cnt}/{_MAX_RESTART})")

                            # ★ 60초 대기 (키움 서버 세션 정리 시간 확보)
                            # _kill_stale은 run_script 내부에서 호출됨
                            print(f"[{now()}] 60초 대기 후 재시작...")
                            time.sleep(60)
                            run_script(name)

        except Exception as e:
            print(f"[{now()}] 스케줄 오류: {e}")

        time.sleep(60)

# =============================================================
# 즉시 실행 체크
# =============================================================
def check_immediate():
    now_dt = datetime.now()
    now_hm = (now_dt.hour, now_dt.minute)

    if not is_weekday():
        print(f"[{now()}] 주말 - 자동매매 스킵")
        return

    if (7, 50) <= now_hm <= (8, 1):
        print(f"[{now()}] 모닝 브리핑 즉시 실행!")
        run_script("morning_briefing")
        mark_done("morning_briefing")

    if (8, 0) <= now_hm <= (15, 30):
        print(f"[{now()}] 자동매매 즉시 실행! (venv32)")
        _restart_count["condition_kiwoom"] = 0
        _restart_blocked.discard("condition_kiwoom")
        run_script("condition_kiwoom")
        mark_done("condition_kiwoom")

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
    print()
    print("  ★ 실행 순서 주의 ★")
    print("  영웅문 HTS는 condition_kiwoom 로그인 완료 후 실행하세요.")
    print("  (영웅문 먼저 켜면 OCX 로그인 충돌 가능)")
    print()

    # 1. ★ 텔레그램 봇 (자동 재시작 루프 포함)
    print(f"[{now()}] Gemini 텔레그램 봇 시작...")
    bot_thread = threading.Thread(
        target=start_bot_with_restart,
        daemon=True,
        name="TelegramBot"
    )
    bot_thread.start()
    print(f"[{now()}] 텔레그램 봇 스레드 시작 완료")
    time.sleep(2)

    # 2. 채널 모니터 (별도 프로세스 - 상시)
    print(f"[{now()}] 채널 모니터 시작...")
    run_script("channel_monitor")
    time.sleep(2)

    # 3. 즉시 실행 체크
    check_immediate()

    # 4. 스케줄 루프
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