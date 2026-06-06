# -*- coding: utf-8 -*-
"""
telegram_bot.py - Gemini 지능형 트레이딩 비서
- 텔레그램 메시지 수신 -> Gemini API 호출 -> 답변 전송
- 대화 히스토리 유지 (맥락 기억)
- 명령어: /start /clear /status /help
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
CHAT_ID         = int(os.getenv("CHAT_ID"))
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
)

# =============================================================
# 시스템 프롬프트
# =============================================================
SYSTEM_PROMPT = """당신은 정완님의 전용 주식 트레이딩 AI 비서입니다.

[역할]
- 한국 주식 시장 전문가로서 매매 전략, 시황, 종목 분석을 도와줍니다.
- 현재 운용 중인 자동매매 시스템(하이퍼 트레이딩)의 상황을 설명하고 조언합니다.
- 질문에 대해 간결하고 핵심적인 답변을 제공합니다.

[현재 운용 전략]
- 모듈1: 주도섹터 + 52주 신고가 (15분 브리핑)
- 모듈2: 단타검색식황사장 실시간 조건검색 자동매매
- 모듈3: 종가베팅 (15:18 스캔, 기관순매수 5일+ 종목)
- 모듈4: 찰나의 매매 (거래량 상위 100종목 실시간 호가 감지)

[매매 원칙]
- 진입: 시장가 25만원 (25만원 초과 종목은 1주)
- 손절: -2.5%
- 트레일링 스탑: +3% 활성화, 5%/8%/13% 구간별 후퇴율
- 최대 보유: 15종목 (찰나 3종목 별도)

[답변 스타일]
- 한국어로 답변
- 핵심만 간결하게
- 숫자/수치는 구체적으로
- 불확실한 것은 불확실하다고 명시
"""

# =============================================================
# 대화 히스토리 (사용자별)
# =============================================================
conversation_history = {}
MAX_HISTORY = 20

def get_history(chat_id: int) -> list:
    return conversation_history.get(chat_id, [])

def add_history(chat_id: int, role: str, content: str):
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []
    # Gemini 형식: user / model
    gemini_role = "model" if role == "assistant" else "user"
    conversation_history[chat_id].append({
        "role": gemini_role,
        "parts": [{"text": content}]
    })
    # 최대 히스토리 초과 시 오래된 것 삭제
    if len(conversation_history[chat_id]) > MAX_HISTORY * 2:
        conversation_history[chat_id] = \
            conversation_history[chat_id][-MAX_HISTORY * 2:]

def clear_history(chat_id: int):
    conversation_history[chat_id] = []

# =============================================================
# Gemini API 호출
# =============================================================
async def ask_gemini(chat_id: int, user_message: str) -> str:
    try:
        # 히스토리에 사용자 메시지 추가
        add_history(chat_id, "user", user_message)
        history = get_history(chat_id)

        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": history,
            "generationConfig": {
                "temperature":     0.7,
                "maxOutputTokens": 1500,
            }
        }

        resp = requests.post(
            GEMINI_URL,
            json=payload,
            timeout=30
        )
        data = resp.json()

        # 오류 체크
        if "error" in data:
            err = data["error"]
            print(f"  Gemini 오류: {err}")
            return f"Gemini 오류: {err.get('message', '알수없음')}"

        answer = (
            data["candidates"][0]["content"]["parts"][0]["text"]
        )

        # 히스토리에 답변 추가
        add_history(chat_id, "assistant", answer)
        return answer

    except Exception as e:
        print(f"  Gemini 호출 오류: {e}")
        return f"오류 발생: {e}"

# =============================================================
# 권한 체크
# =============================================================
def is_authorized(update: Update) -> bool:
    return update.effective_chat.id == CHAT_ID

# =============================================================
# 명령어 핸들러
# =============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    now = datetime.now().strftime("%m/%d %H:%M")
    msg = (
        f"안녕하세요 정완님! Gemini 트레이딩 비서입니다. ({now})\n\n"
        f"무엇이든 물어보세요!\n\n"
        f"명령어:\n"
        f"/clear - 대화 초기화\n"
        f"/status - 시스템 상태\n"
        f"/help - 도움말"
    )
    await update.message.reply_text(msg)

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    clear_history(update.effective_chat.id)
    await update.message.reply_text("대화 히스토리를 초기화했습니다.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    now     = datetime.now()
    now_str = now.strftime("%m/%d %H:%M")
    weekday = ["월","화","수","목","금","토","일"][now.weekday()]
    is_market = (
        now.weekday() < 5
        and 9 <= now.hour < 15
    )
    market_str = "장중" if is_market else "장외"

    msg = (
        f"시스템 상태 ({now_str} {weekday}요일)\n"
        f"--------------------\n"
        f"장 상태: {market_str}\n"
        f"모듈1: 주도섹터 15분 브리핑\n"
        f"모듈2: 황사장 실시간 조건검색\n"
        f"모듈3: 종가베팅 (15:18)\n"
        f"모듈4: 찰나의 매매 실시간\n"
        f"--------------------\n"
        f"대화 히스토리: {len(get_history(update.effective_chat.id))}턴\n"
        f"AI 엔진: Gemini 2.0 Flash"
    )
    await update.message.reply_text(msg)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    msg = (
        "Gemini 트레이딩 비서 사용법\n"
        "--------------------\n"
        "질문 예시:\n"
        "• 오늘 시장 어때?\n"
        "• 손절 기준이 뭐야?\n"
        "• 트레일링 스탑 설명해줘\n"
        "• 지금 보유 종목 전략은?\n"
        "• 에코프로 어떻게 봐?\n"
        "• 황사장 조건식이 뭐야?\n\n"
        "명령어:\n"
        "/start - 시작\n"
        "/clear - 대화 초기화\n"
        "/status - 시스템 상태\n"
        "/help - 이 메시지"
    )
    await update.message.reply_text(msg)

# =============================================================
# 일반 메시지 핸들러
# =============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_msg = update.message.text
    chat_id  = update.effective_chat.id

    # 타이핑 표시
    await context.bot.send_chat_action(
        chat_id=chat_id, action="typing"
    )

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 질문: {user_msg[:50]}")

    answer = await ask_gemini(chat_id, user_msg)

    # 4096자 초과 시 분할 전송
    if len(answer) > 4000:
        chunks = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(answer)

    print(f"  답변: {answer[:50]}...")

# =============================================================
# 봇 실행
# =============================================================
def start_bot():
    print("Gemini 텔레그램 봇 시작...")
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start",  cmd_start))
    application.add_handler(CommandHandler("clear",  cmd_clear))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("help",   cmd_help))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message
    ))

    print(f"봇 준비 완료! CHAT_ID: {CHAT_ID}")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    start_bot()