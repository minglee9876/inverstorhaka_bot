import os
import json
import re
import math
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Circle
import pandas as pd
import requests
import yfinance as yf


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


if not TOKEN or not CHAT_ID:
    raise RuntimeError(
        "GitHub Secrets의 텔레그램 토큰과 채팅 ID를 확인하세요."
    )


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}


# -----------------------------
# VIX 최근 1년 데이터
# -----------------------------
def get_vix():
    data = yf.Ticker("^VIX").history(
        period="1y",
        interval="1d",
    )

    if data.empty or "Close" not in data:
        raise RuntimeError(
            "VIX 데이터를 가져오지 못했습니다."
        )

    close = pd.to_numeric(
        data["Close"],
        errors="coerce",
    ).dropna()

    if close.empty:
        raise RuntimeError(
            "VIX 종가 데이터가 없습니다."
        )

    return close


# -----------------------------
# CNN Fear & Greed
# -----------------------------
def get_fear_greed():
    url = (
        "https://production.dataviz.cnn.io/"
        "index/fearandgreed/graphdata"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    item = response.json()["fear_and_greed"]

    return float(item["score"])


# -----------------------------
# CBOE 페이지에서 풋콜비율 읽기
# -----------------------------
def parse_cboe(html, requested_date):
    start = html.find('\\"optionsData\\":')
    end = html.find('\\"selectedDate\\"', start)

    if start < 0 or end < 0:
        return None

    raw = html[start:end].rstrip(",")
    raw = raw.replace('\\"', '"')

    options = json.loads(
        "{" + raw + "}"
    )["optionsData"]

    match = re.search(
        r'selectedDate\\":\\"'
        r"(\d{4}-\d{2}-\d{2})",
        html,
    )

    if match:
        actual_date = match.group(1)
    else:
        actual_date = requested_date

    # 주말이나 휴장일이면 다른 날짜가 반환될 수 있음
    if actual_date != requested_date:
        return None

    ratios = {
        row["name"]: row["value"]
        for row in options["ratios"]
    }

    value = ratios.get(
        "TOTAL PUT/CALL RATIO"
    )

    if value is None:
        return None

    return {
        "date": actual_date,
        "ratio": float(value),
    }


def fetch_cboe_one(date_text):
    url = (
        "https://www.cboe.com/markets/us/options/"
        "market-statistics/daily"
        f"?dt={date_text}"
    )

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
        )

        response.raise_for_status()

        return parse_cboe(
            response.text,
            date_text,
        )

    except Exception as error:
        print(
            "CBOE 오류:",
            date_text,
            error,
        )

        return None


# -----------------------------
# 최근 45일 풋콜비율 수집
# -----------------------------
def get_put_call_history():
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=45)

    dates = [
        day.strftime("%Y-%m-%d")
        for day in pd.bdate_range(
            start=start,
            end=end,
        )
    ]

    rows = []

    # 동시에 5일씩 가져와 시간을 줄임
    with ThreadPoolExecutor(
        max_workers=5
    ) as executor:

        futures = [
            executor.submit(
                fetch_cboe_one,
                item,
            )
            for item in dates
        ]

        for future in as_completed(futures):
            result = future.result()

            if result:
                rows.append(result)

    frame = pd.DataFrame(rows)

    if frame.empty:
        return frame

    frame["date"] = pd.to_datetime(
        frame["date"]
    )

    frame = (
        frame
        .sort_values("date")
        .drop_duplicates("date")
    )

    # 10일 이동평균
    frame["ma10"] = (
        frame["ratio"]
        .rolling(10)
        .mean()
    )

    return frame


def fear_name(score):
    if score < 25:
        return "극단적 공포"

    if score < 45:
        return "공포"

    if score < 55:
        return "중립"

    if score < 75:
        return "탐욕"

    return "극단적 탐욕"


# -----------------------------
# 그래프 이미지 만들기
# -----------------------------
def draw_dashboard(
    vix,
    fear,
    put_call,
):
    fig = plt.figure(
        figsize=(10, 14),
        dpi=160,
    )

    grid = fig.add_gridspec(
        3,
        1,
        hspace=0.42,
    )

    # -------------------------
    # 1. VIX 차트
    # -------------------------
    ax1 = fig.add_subplot(grid[0])

    ax1.plot(
        vix.index,
        vix.values,
        linewidth=2.2,
        color="#2563EB",
    )

    ax1.axhline(
        25,
        linestyle="--",
        color="#F59E0B",
        label="Signal 25",
    )

    ax1.axhline(
        30,
        linestyle="--",
        color="#DC2626",
        label="High 30",
    )

    ax1.scatter(
        vix.index[-1],
        vix.iloc[-1],
        color="#111827",
        s=65,
    )

    ax1.set_title(
        (
            "VIX - 1 Year   "
            f"Current: {vix.iloc[-1]:.2f}"
        ),
        fontweight="bold",
    )

    ax1.grid(alpha=0.25)
    ax1.legend()

    # -------------------------
    # 2. Fear & Greed 게이지
    # -------------------------
    ax2 = fig.add_subplot(grid[1])

    bands = [
        (0, 25, "#7C3AED"),
        (25, 45, "#F97316"),
        (45, 55, "#9CA3AF"),
        (55, 75, "#84CC16"),
        (75, 100, "#16A34A"),
    ]

    for low, high, color in bands:
        ax2.add_patch(
            Wedge(
                (0, 0),
                1,
                180 - high * 1.8,
                180 - low * 1.8,
                width=0.28,
                facecolor=color,
                edgecolor="white",
            )
        )

    angle = math.radians(
        180 - fear * 1.8
    )

    ax2.plot(
        [
            0,
            0.72 * math.cos(angle),
        ],
        [
            0,
            0.72 * math.sin(angle),
        ],
        color="#111827",
        linewidth=4,
    )

    ax2.add_patch(
        Circle(
            (0, 0),
            0.055,
            color="#111827",
        )
    )

    ax2.text(
        0,
        -0.06,
        f"{fear:.1f}",
        ha="center",
        va="top",
        fontsize=28,
        fontweight="bold",
    )

    ax2.text(
        0,
        1.08,
        "Fear & Greed Gauge",
        ha="center",
        fontsize=15,
        fontweight="bold",
    )

    ax2.text(
        -1.02,
        -0.02,
        "0",
        ha="center",
    )

    ax2.text(
        1.02,
        -0.02,
        "100",
        ha="center",
    )

    ax2.set_xlim(-1.2, 1.2)
    ax2.set_ylim(-0.25, 1.2)
    ax2.set_aspect("equal")
    ax2.axis("off")

    # -------------------------
    # 3. 풋콜비율 차트
    # -------------------------
    ax3 = fig.add_subplot(grid[2])

    if put_call.empty:
        latest_pc = None

        ax3.text(
            0.5,
            0.5,
            "CBOE data unavailable",
            ha="center",
            va="center",
            transform=ax3.transAxes,
        )

    else:
        latest_pc = float(
            put_call["ratio"].iloc[-1]
        )

        ax3.plot(
            put_call["date"],
            put_call["ratio"],
            color="#2563EB",
            alpha=0.65,
            label="Daily",
        )

        ax3.plot(
            put_call["date"],
            put_call["ma10"],
            color="#F59E0B",
            linewidth=2.2,
            label="10-day MA",
        )

        ax3.axhline(
            1.0,
            linestyle="--",
            color="#DC2626",
            label="Signal 1.0",
        )

        ax3.scatter(
            put_call["date"].iloc[-1],
            latest_pc,
            color="#111827",
            s=65,
        )

        ax3.legend()

    if latest_pc is None:
        current_text = "N/A"
    else:
        current_text = f"{latest_pc:.2f}"

    ax3.set_title(
        (
            "CBOE Total Put/Call Ratio "
            "- Recent 45 Days   "
            f"Current: {current_text}"
        ),
        fontweight="bold",
    )

    ax3.grid(alpha=0.25)

    fig.suptitle(
        "AI INVESTMENT SENTIMENT DASHBOARD",
        fontsize=18,
        fontweight="bold",
    )

    fig.savefig(
        "sentiment_dashboard.png",
        bbox_inches="tight",
    )

    plt.close(fig)

    return latest_pc


# -----------------------------
# 텔레그램으로 이미지 전송
# -----------------------------
def send_photo(caption):
    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/sendPhoto"
    )

    with open(
        "sentiment_dashboard.png",
        "rb",
    ) as image:

        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
            },
            files={
                "photo": image,
            },
            timeout=90,
        )

    print(
        response.status_code,
        response.text,
    )

    response.raise_for_status()


def main():
    # 데이터 가져오기
    vix = get_vix()
    fear = get_fear_greed()
    put_call = get_put_call_history()

    # 그래프 만들기
    put_call_value = draw_dashboard(
        vix,
        fear,
        put_call,
    )

    vix_value = float(
        vix.iloc[-1]
    )

    # -------------------------
    # 투자 신호 점수
    # -------------------------
    score = 0

    if vix_value >= 25:
        score += 1

    if fear < 20:
        score += 1

    if (
        put_call_value is not None
        and put_call_value >= 1.0
    ):
        score += 1

    light = {
        0: "🟢",
        1: "🟡",
        2: "🟠",
        3: "🔴",
    }[score]

    action = {
        0: "관망",
        1: "투자 예정 금액의 20%",
        2: "투자 예정 금액의 50%",
        3: "70~100%를 나눠서 매수",
    }[score]

    if put_call_value is None:
        pc_text = "확인 실패"
        pc_check = "⚪"
    else:
        pc_text = f"{put_call_value:.2f}"

        if put_call_value >= 1.0:
            pc_check = "✅"
        else:
            pc_check = "❌"

    if vix_value >= 25:
        vix_check = "✅"
    else:
        vix_check = "❌"

    if fear < 20:
        fear_check = "✅"
    else:
        fear_check = "❌"

    now = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime(
        "%Y년 %m월 %d일 %H:%M"
    )

    caption = (
        f"{light} AI 투자 신호등 {score}/3\n"
        f"{now}\n\n"
        f"VIX: {vix_value:.2f} "
        f"{vix_check}\n"
        f"Fear & Greed: {fear:.1f} "
        f"({fear_name(fear)}) "
        f"{fear_check}\n"
        f"Put/Call: {pc_text} "
        f"{pc_check}\n\n"
        f"오늘 행동: {action}\n"
        "※ 참고용 지표이며 실제 주문 신호가 아닙니다."
    )

    send_photo(caption)


if __name__ == "__main__":
    main()
