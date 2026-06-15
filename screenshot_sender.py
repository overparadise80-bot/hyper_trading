# -*- coding: utf-8 -*-
"""
screenshot_sender.py - 모니터 HTML을 스크린샷 찍어 텔레그램 전송
module1_sector.py의 _build_and_send()에서 호출
"""

import os
import requests
import tempfile
from modules.common import TELEGRAM_TOKEN, CHAT_ID

HTML_PATH = os.path.abspath("monitor.html")


def send_screenshot_to_telegram(caption: str = "", ngrok_url: str = None):
    """
    monitor.html을 playwright로 스크린샷 → 텔레그램 전송
    ngrok_url이 있으면 캡션 아래에 링크 추가
    """
    if ngrok_url:
        monitor_url = f"{ngrok_url}/monitor.html"
        caption = f"{caption}\n\n<a href='{monitor_url}'>📊 모바일 실시간 보기</a>\n{monitor_url}"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [스크린샷] playwright 미설치 - 텍스트 메시지만 전송")
        return False

    screenshot_path = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # HTML 파일 로드
            page.goto(f"file:///{HTML_PATH.replace(os.sep, '/')}")

            # 3열 레이아웃 (>900px 조건 충족)
            page.set_viewport_size({"width": 1200, "height": 900})
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)

            # 전체 페이지 높이에 맞게 viewport 재조정
            page_height = page.evaluate("document.body.scrollHeight")
            page.set_viewport_size({"width": 1200, "height": page_height})

            # 스크린샷 저장 (임시 파일)
            tmp = tempfile.NamedTemporaryFile(
                suffix=".png", delete=False, prefix="monitor_")
            screenshot_path = tmp.name
            tmp.close()

            page.screenshot(path=screenshot_path, full_page=True)
            browser.close()

        print(f"  [스크린샷] 캡처 완료: {screenshot_path}")

        # 텔레그램 전송
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(screenshot_path, "rb") as f:
            resp = requests.post(url, data={
                "chat_id":    CHAT_ID,
                "caption":    caption,
                "parse_mode": "HTML",
            }, files={"photo": f}, timeout=30)

        if resp.status_code == 200:
            print("  [스크린샷] 텔레그램 전송 완료!")
            return True
        else:
            print(f"  [스크린샷] 전송 실패: {resp.text}")
            return False

    except Exception as e:
        print(f"  [스크린샷] 오류: {e}")
        return False

    finally:
        if screenshot_path and os.path.exists(screenshot_path):
            os.unlink(screenshot_path)