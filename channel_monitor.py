# -*- coding: utf-8 -*-
"""
channel_monitor.py - 텔레그램 채널 실시간 모니터링
Telethon으로 내 계정에 가입된 채널 메시지를 수신해서
내 텔레그램 채팅방으로 전달
"""

import os
import asyncio
import requests
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat

load_dotenv()

TELEGRAM_API_ID   = int(os.getenv("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
CHAT_ID           = os.getenv("CHAT_ID")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
)

# =============================================================
# 모니터링할 채널 목록
# =============================================================
# 전체 전달 채널 (필터링 없이 모든 메시지 전달)
PASSTHROUGH_CHANNELS = [
    "AWAKE - 실시간 주식 공시 정리채널",
    "주요공시 알리미",
]

# 필터링 채널 (키워드 매칭된 메시지만 전달)
FILTER_CHANNELS = [
    "빠르고 정확한 주식정보방",
    "AWAKE - 52주 신고가 모니터링",
    "상장기업 수사대 🚒",
]

# 전체 모니터링 채널 - Telethon 구독용 (정확한 채널명)
# 상장기업 수사대는 이모지가 있어서 iter_dialogs로 자동 탐색
MONITOR_CHANNELS = PASSTHROUGH_CHANNELS + FILTER_CHANNELS

# 부분 문자열 매칭용
PASSTHROUGH_KEYWORDS = [
    "실시간 주식 공시",
    "주요공시 알리미",
]

# 필터 채널도 부분 문자열로 매칭 (이모지 무관)
FILTER_KEYWORDS_CHANNEL = [
    "빠르고 정확한 주식정보방",
    "AWAKE - 52주 신고가",
    "상장기업 수사대",   # 이모지 무관하게 매칭
]

# =============================================================
# 필터링 키워드 (FILTER_CHANNELS에만 적용)
# =============================================================
FILTER_KEYWORDS = [
    # 공시 관련
    "수주", "공급계약", "유상증자", "실적", "영업이익",
    "매출", "서프라이즈", "신고가", "상한가",
    # 매매 관련
    "매수", "매도", "추천", "목표가", "상향",
    # 섹터 관련
    "반도체", "2차전지", "바이오", "AI", "방산",
]

# 이 키워드가 포함되면 전달 안 함 (노이즈 제거 - 전체 채널 공통)
EXCLUDE_KEYWORDS = [
    "광고", "홍보", "유료", "가입", "초대",
    "텔레그램", "카톡", "오픈채팅",
]

# =============================================================
# Gemini 요약 (선택적)
# =============================================================
USE_GEMINI_SUMMARY = False   # Gemini 쿼터 문제 시 False로 설정

async def gemini_summarize(text: str, channel_name: str) -> str:
    """Gemini로 메시지 요약"""
    try:
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text":
                    f"다음은 '{channel_name}' 텔레그램 채널의 주식 관련 메시지입니다.\n"
                    f"핵심만 2~3줄로 요약해주세요. 종목명, 수치, 핵심 이슈 위주로.\n\n"
                    f"메시지:\n{text[:1000]}"
                }]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 300,
            }
        }
        resp = requests.post(GEMINI_URL, json=payload, timeout=15)
        data = resp.json()
        if "candidates" in data:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return text[:300]
    except Exception as e:
        print(f"  Gemini 요약 오류: {e}")
        return text[:300]

# =============================================================
# 텔레그램 전송 (봇 API)
# =============================================================
def send_telegram(msg: str):
    try:
        url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
        for chunk in chunks:
            requests.post(url, data={
                "chat_id":    CHAT_ID,
                "text":       chunk,
                "parse_mode": "HTML"
            }, timeout=10)
    except Exception as e:
        print(f"  텔레그램 전송 오류: {e}")

# =============================================================
# 메시지 필터링
# =============================================================
def should_forward(text: str) -> bool:
    """전달 여부 판단"""
    if not text:
        return False

    # 제외 키워드 체크
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return False

    # 필터 키워드 없으면 전체 전달
    if not FILTER_KEYWORDS:
        return True

    # 필터 키워드 포함 여부
    return any(kw in text for kw in FILTER_KEYWORDS)

def get_matched_keywords(text: str) -> list:
    """매칭된 키워드 목록"""
    return [kw for kw in FILTER_KEYWORDS if kw in text]

# =============================================================
# 메인 모니터링 루프
# =============================================================
async def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 채널 모니터 시작...")

    # Telethon 클라이언트 (세션 파일: channel_monitor.session)
    client = TelegramClient(
        "channel_monitor",
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH
    )

    await client.start()
    print("  Telethon 로그인 성공!")

    # 모니터링 대상 채널 ID 수집 (부분 문자열 매칭)
    all_keywords = PASSTHROUGH_KEYWORDS + FILTER_KEYWORDS_CHANNEL
    monitor_ids = set()
    async for dialog in client.iter_dialogs():
        if any(kw in dialog.name for kw in all_keywords):
            monitor_ids.add(dialog.id)
            print(f"  모니터링 등록: {dialog.name} (ID: {dialog.id})")

    if not monitor_ids:
        print("  경고: 모니터링 대상 채널이 없습니다.")
        print("  가입된 채널명을 MONITOR_CHANNELS에 정확히 입력해주세요.")

    # 시작 알림
    send_telegram(
        f"<b>채널 모니터 시작</b> ({datetime.now().strftime('%m/%d %H:%M')})\n"
        f"모니터링 채널: {len(monitor_ids)}개\n"
        f"{'  '.join([c for c in MONITOR_CHANNELS[:3]])}..."
    )

    # ==========================================================
    # 실시간 메시지 수신 이벤트
    # ==========================================================
    @client.on(events.NewMessage(chats=list(monitor_ids)))
    async def on_new_message(event):
        try:
            text         = event.message.text or ""
            chat         = await event.get_chat()
            channel_name = getattr(chat, 'title', '알수없음')
            now_str      = datetime.now().strftime("%H:%M:%S")

            if not text:
                return

            print(f"  [{now_str}] {channel_name}: {text[:50]}")

            # 제외 키워드 공통 체크
            for kw in EXCLUDE_KEYWORDS:
                if kw in text:
                    print(f"    -> 제외 키워드({kw}) - 스킵")
                    return

            # ── 전체 전달 채널 ──────────────────────────────
            is_passthrough = any(
                kw in channel_name for kw in PASSTHROUGH_KEYWORDS
            )
            if is_passthrough:
                # Gemini 요약 적용
                if USE_GEMINI_SUMMARY and len(text) > 100:
                    summary = await gemini_summarize(text, channel_name)
                    msg  = f"<b>[{channel_name}]</b> ({now_str})\n"
                    msg += "--------------------\n"
                    msg += f"{summary}\n\n"
                    msg += f"<i>원문: {text[:150]}...</i>"
                else:
                    msg  = f"<b>[{channel_name}]</b> ({now_str})\n"
                    msg += "--------------------\n"
                    msg += text[:500]
                send_telegram(msg)
                print(f"    -> 전체전달 완료")
                return

            # ── 필터링 채널 ──────────────────────────────────
            is_filter = any(
                kw in channel_name for kw in FILTER_KEYWORDS_CHANNEL
            )
            if not is_filter:
                print(f"    -> 미등록 채널 - 스킵")
                return

            matched = get_matched_keywords(text)
            if not matched:
                print(f"    -> 키워드 없음 - 스킵")
                return

            if USE_GEMINI_SUMMARY and len(text) > 100:
                summary = await gemini_summarize(text, channel_name)
                msg  = f"<b>[{channel_name}]</b> ({now_str})\n"
                msg += f"키워드: {', '.join(matched)}\n"
                msg += "--------------------\n"
                msg += f"{summary}\n\n"
                msg += f"<i>원문: {text[:100]}...</i>"
            else:
                msg  = f"<b>[{channel_name}]</b> ({now_str})\n"
                msg += f"키워드: {', '.join(matched)}\n"
                msg += "--------------------\n"
                msg += text[:500]

            send_telegram(msg)
            print(f"    -> 필터전달 완료 (키워드: {matched})")

        except Exception as e:
            print(f"  메시지 처리 오류: {e}")

    print(f"\n  실시간 모니터링 시작! (Ctrl+C로 종료)")
    await client.run_until_disconnected()

# =============================================================
# 실행
# =============================================================
if __name__ == "__main__":
    asyncio.run(main())