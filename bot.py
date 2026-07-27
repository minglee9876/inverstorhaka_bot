
import requests
import datetime

TOKEN = "여기에_봇_토큰_붙여넣기"
CHAT_ID = "여기에_내_ID_붙여넣기"


now = datetime.datetime.now()

message = f"""
☀️ AI 투자 비서 테스트

현재 시간:
{now}

봇 연결 성공했습니다.

앞으로 매일 아침
투자 브리핑을 보내드릴 예정입니다.
"""


url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": message
    }
)
