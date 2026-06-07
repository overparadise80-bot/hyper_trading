# -*- coding: utf-8 -*-
"""
ngrok_server.py - 로컬 웹서버 + ngrok 터널 관리
main.py에서 import해서 사용
"""

import os
import sys
import json
import time
import threading
import subprocess
import requests
from http.server import HTTPServer, SimpleHTTPRequestHandler
from modules.common import send_telegram

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SERVE_PORT = 8765
_ngrok_proc   = None
_server_thread = None
_public_url    = None


# =============================================================
# 로컬 웹서버 (BASE_DIR 기준으로 파일 서빙)
# =============================================================
class SilentHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        pass  # 로그 출력 억제


def _run_server():
    server = HTTPServer(("0.0.0.0", SERVE_PORT), SilentHandler)
    server.serve_forever()


def start_server():
    global _server_thread
    _server_thread = threading.Thread(target=_run_server, daemon=True)
    _server_thread.start()
    print(f"[ngrok] 로컬 서버 시작: http://localhost:{SERVE_PORT}/monitor.html")


# =============================================================
# ngrok 터널 시작
# =============================================================
def start_ngrok():
    global _ngrok_proc, _public_url

    # 기존 프로세스 종료
    stop_ngrok()

    _ngrok_proc = subprocess.Popen(
        ["ngrok", "http", str(SERVE_PORT),
         "--log", "stdout", "--log-format", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW
    )

    # ngrok API로 public URL 가져오기 (최대 10초 대기)
    for _ in range(20):
        time.sleep(0.5)
        try:
            resp = requests.get(
                "http://127.0.0.1:4040/api/tunnels", timeout=2)
            tunnels = resp.json().get("tunnels", [])
            for t in tunnels:
                if t.get("proto") == "https":
                    _public_url = t["public_url"]
                    break
            if _public_url:
                break
        except:
            pass

    if _public_url:
        monitor_url = f"{_public_url}/monitor.html"
        print(f"[ngrok] 터널 시작: {monitor_url}")
        send_telegram(
            f"<b>주도섹터 모니터 URL</b>\n"
            f"{monitor_url}\n\n"
            f"갤럭시탭에서 위 링크를 열어두세요!\n"
            f"(15분마다 자동 새로고침)"
        )
        return monitor_url
    else:
        print("[ngrok] URL 획득 실패 - ngrok 상태 확인 필요")
        return None


def stop_ngrok():
    global _ngrok_proc, _public_url
    if _ngrok_proc and _ngrok_proc.poll() is None:
        _ngrok_proc.terminate()
        _ngrok_proc = None
    _public_url = None


def get_public_url():
    return _public_url


# =============================================================
# 통합 시작 함수 (main.py에서 호출)
# =============================================================
def start_monitor_server():
    """웹서버 + ngrok 동시 시작, 텔레그램으로 URL 전송"""
    start_server()
    time.sleep(1)
    url = start_ngrok()
    return url