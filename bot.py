import os
import datetime
from zoneinfo import ZoneInfo

import requests


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN이 비어 있습니다. "
        "GitHub Secrets를 확인하세요."
    )

if not CHAT_ID:
    raise RuntimeError(
        "TELEGRAM_CHAT_ID가 비어 있습니다. "
        "GitHub Secrets를 확인하세요."
    )

now = datetime.datetime.now(
    ZoneInfo("Asia/Seoul")
).strftime("%Y년 %m월 %d일 %H:%M")

message = f"""☀️ AI 투자 비서 테스트

현재 시간: {now}

텔레그램 봇 연결에 성공했습니다.
"""

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

response = requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": message,
    },
    timeout=30,
)

print("Telegram 응답 상태:", response.status_code)
print("Telegram 응답 내용:", response.text)

if response.status_code != 200:
    raise RuntimeError(
        f"텔레그램 전송 실패: {response.status_code}"
    )

print("텔레그램 메시지 전송 성공")
