import html
import os
import time
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import requests
import yfinance as yf

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


# =========================================================
# GitHub Secrets
# =========================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()

TOSS_CLIENT_ID = os.getenv(
    "TOSS_CLIENT_ID",
    "",
).strip()

TOSS_CLIENT_SECRET = os.getenv(
    "TOSS_CLIENT_SECRET",
    "",
).strip()

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    "",
).strip()

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5-mini",
).strip()


if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN Secret이 없습니다."
    )

if not TELEGRAM_CHAT_ID:
    raise RuntimeError(
        "TELEGRAM_CHAT_ID Secret이 없습니다."
    )


# =========================================================
# 기본 설정
# =========================================================

TOSS_BASE_URL = "https://openapi.tossinvest.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/124.0 Safari/537.36"
    )
}


# 미국 시장 지수
MARKET_SYMBOLS = {
    "S&P500": "^GSPC",
    "나스닥": "^IXIC",
    "다우": "^DJI",
    "SOX 반도체": "^SOX",
    "러셀2000": "^RUT",
    "미국 10년물": "^TNX",
    "달러인덱스": "DX-Y.NYB",
    "VIX": "^VIX",
}


# 관심 종목
WATCHLIST = {
    "삼성전자": {
        "toss": "005930",
        "yahoo": "005930.KS",
        "currency": "KRW",
    },
    "SK하이닉스": {
        "toss": "000660",
        "yahoo": "000660.KS",
        "currency": "KRW",
    },
    "현대차2우B": {
        "toss": "005387",
        "yahoo": "005387.KS",
        "currency": "KRW",
    },
    "TIGER 코리아휴머노이드로봇산업": {
        "toss": "0148J0",
        "yahoo": "0148J0.KS",
        "currency": "KRW",
    },
    "테슬라": {
        "toss": "TSLA",
        "yahoo": "TSLA",
        "currency": "USD",
    },
}


# Google 뉴스 검색어
NEWS_QUERIES = [
    "미국 증시 AI 반도체 when:1d",
    "삼성전자 SK하이닉스 HBM 반도체 when:1d",
    "현대차 테슬라 로봇 자동차 when:1d",
    "휴머노이드 로봇 ETF when:1d",
]


# =========================================================
# Yahoo Finance
# =========================================================

def get_yahoo_history(
    symbol: str,
    period: str = "6mo",
) -> pd.DataFrame:
    """
    Yahoo Finance에서 일봉 데이터를 가져옵니다.
    실패할 경우 최대 3번 다시 시도합니다.
    """

    last_error = None

    for attempt in range(3):
        try:
            frame = yf.Ticker(symbol).history(
                period=period,
                interval="1d",
                auto_adjust=False,
                timeout=30,
            )

            if (
                not frame.empty
                and "Close" in frame.columns
            ):
                return frame

        except Exception as error:
            last_error = error

            print(
                f"Yahoo {symbol} 오류 "
                f"{attempt + 1}/3: {error}"
            )

        time.sleep(2 * (attempt + 1))

    if last_error:
        print(
            f"Yahoo {symbol} 최종 오류: "
            f"{last_error}"
        )

    return pd.DataFrame()


# =========================================================
# 토스증권 API
# =========================================================

def get_toss_access_token():
    """
    Client Id와 Client Secret으로
    토스증권 액세스 토큰을 발급받습니다.
    """

    if (
        not TOSS_CLIENT_ID
        or not TOSS_CLIENT_SECRET
    ):
        print(
            "토스증권 Secret이 없습니다. "
            "Yahoo Finance만 사용합니다."
        )
        return None

    try:
        response = requests.post(
            f"{TOSS_BASE_URL}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": TOSS_CLIENT_ID,
                "client_secret": TOSS_CLIENT_SECRET,
            },
            timeout=30,
        )

        print(
            "토스 토큰 응답:",
            response.status_code,
            response.text[:500],
        )

        response.raise_for_status()

        token = response.json().get(
            "access_token"
        )

        if not token:
            raise RuntimeError(
                "토스증권 응답에 "
                "access_token이 없습니다."
            )

        print(
            "토스증권 액세스 토큰 발급 성공"
        )

        return token

    except Exception as error:
        print(
            "토스증권 토큰 발급 실패. "
            f"Yahoo로 전환합니다: {error}"
        )

        return None


def get_toss_history(
    symbol: str,
    access_token: str,
) -> pd.DataFrame:
    """
    토스증권 Open API에서
    최근 120개 일봉 데이터를 가져옵니다.
    """

    response = requests.get(
        f"{TOSS_BASE_URL}/api/v1/candles",
        headers={
            "Authorization": (
                f"Bearer {access_token}"
            )
        },
        params={
            "symbol": symbol,
            "interval": "1d",
            "count": 120,
            "adjusted": True,
        },
        timeout=30,
    )

    print(
        f"토스 {symbol} 응답:",
        response.status_code,
        response.text[:300],
    )

    response.raise_for_status()

    payload = response.json()

    candles = (
        payload
        .get("result", {})
        .get("candles", [])
    )

    if not candles:
        raise RuntimeError(
            f"토스증권 {symbol} "
            "일봉 데이터가 비어 있습니다."
        )

    rows = []

    for item in candles:
        rows.append(
            {
                "Date": pd.to_datetime(
                    item["timestamp"]
                ),
                "Close": pd.to_numeric(
                    item["closePrice"],
                    errors="coerce",
                ),
            }
        )

    frame = (
        pd.DataFrame(rows)
        .dropna(subset=["Close"])
    )

    if frame.empty:
        raise RuntimeError(
            f"토스증권 {symbol} "
            "종가 데이터가 없습니다."
        )

    frame = (
        frame
        .sort_values("Date")
        .set_index("Date")
    )

    return frame


# =========================================================
# 기술적 지표 계산
# =========================================================

def calculate_rsi(
    close: pd.Series,
    period: int = 14,
):
    """
    RSI 14일 값을 계산합니다.
    """

    delta = close.diff()

    gain = (
        delta
        .clip(lower=0)
        .rolling(period)
        .mean()
    )

    loss = (
        -delta
        .clip(upper=0)
        .rolling(period)
        .mean()
    )

    rs = gain / loss.replace(
        0,
        float("nan"),
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    valid = rsi.dropna()

    if valid.empty:
        return None

    return float(
        valid.iloc[-1]
    )


def metrics_from_frame(
    frame: pd.DataFrame,
    source: str,
):
    """
    현재가, 전일 대비 등락률,
    20일·60일 평균, RSI를 계산합니다.
    """

    if (
        frame.empty
        or "Close" not in frame.columns
    ):
        return None

    close = pd.to_numeric(
        frame["Close"],
        errors="coerce",
    ).dropna()

    if len(close) < 2:
        return None

    current = float(
        close.iloc[-1]
    )

    previous = float(
        close.iloc[-2]
    )

    change_pct = (
        (current - previous)
        / previous
        * 100
    )

    if len(close) >= 20:
        ma20 = float(
            close.tail(20).mean()
        )
    else:
        ma20 = None

    if len(close) >= 60:
        ma60 = float(
            close.tail(60).mean()
        )
    else:
        ma60 = None

    return {
        "current": current,
        "previous": previous,
        "change_pct": change_pct,
        "ma20": ma20,
        "ma60": ma60,
        "rsi14": calculate_rsi(close),
        "source": source,
    }


def get_market_metrics(symbol: str):
    """
    미국 지수는 Yahoo Finance에서 가져옵니다.
    """

    frame = get_yahoo_history(
        symbol,
        period="6mo",
    )

    return metrics_from_frame(
        frame,
        "Yahoo Finance",
    )


def get_watch_metrics(
    item: dict,
    toss_token,
):
    """
    관심 종목은 토스증권을 먼저 사용합니다.

    토스증권 호출이 실패하면
    Yahoo Finance로 자동 전환합니다.
    """

    if toss_token:
        try:
            frame = get_toss_history(
                item["toss"],
                toss_token,
            )

            result = metrics_from_frame(
                frame,
                "토스증권",
            )

            if result is not None:
                return result

        except Exception as error:
            print(
                f"토스증권 "
                f"{item['toss']} 실패. "
                f"Yahoo로 전환: {error}"
            )

    yahoo_frame = get_yahoo_history(
        item["yahoo"],
        period="6mo",
    )

    return metrics_from_frame(
        yahoo_frame,
        "Yahoo Finance",
    )


# =========================================================
# 표시 형식
# =========================================================

def arrow(value: float) -> str:
    if value > 0:
        return "▲"

    if value < 0:
        return "▼"

    return "－"


def market_line(
    name: str,
    data,
) -> str:
    if data is None:
        return (
            f"{name}: 데이터 확인 실패"
        )

    change = data["change_pct"]

    if name == "미국 10년물":
        bp = (
            data["current"]
            - data["previous"]
        ) * 100

        return (
            f"{name}: "
            f"{data['current']:.3f}% "
            f"{arrow(bp)} "
            f"{abs(bp):.1f}bp"
        )

    if name == "VIX":
        return (
            f"{name}: "
            f"{data['current']:.2f} "
            f"{arrow(change)} "
            f"{abs(change):.2f}%"
        )

    return (
        f"{name}: "
        f"{data['current']:,.2f} "
        f"{arrow(change)} "
        f"{abs(change):.2f}%"
    )


def stock_price(
    currency: str,
    value: float,
) -> str:
    if currency == "KRW":
        return f"₩{value:,.0f}"

    return f"${value:,.2f}"


# =========================================================
# 관심 종목 규칙 기반 분석
# =========================================================

def technical_view(
    data,
    vix_value,
):
    if data is None:
        return (
            "⚪ 데이터 확인 필요",
            "가격 데이터를 가져오지 못했습니다.",
        )

    current = data["current"]
    ma20 = data["ma20"]
    ma60 = data["ma60"]
    rsi = data["rsi14"]

    if (
        ma20 is None
        or ma60 is None
        or rsi is None
    ):
        return (
            "⏸ 보유·관찰",
            (
                "이동평균 또는 RSI 계산에 "
                "필요한 자료가 부족합니다."
            ),
        )

    if rsi >= 75:
        return (
            "⚠️ 비중 확대 자제",
            (
                f"RSI {rsi:.0f}로 "
                "단기 과열 가능성을 "
                "점검할 구간입니다."
            ),
        )

    if current < ma60:
        return (
            "⚠️ 관망·비중 점검",
            (
                "현재가가 60일 평균보다 낮고 "
                f"RSI는 {rsi:.0f}입니다."
            ),
        )

    if (
        vix_value is not None
        and vix_value >= 25
        and current <= ma20
        and rsi <= 45
    ):
        return (
            "✅ 분할매수 검토",
            (
                "시장 변동성이 높고 "
                f"RSI {rsi:.0f}로 "
                "단기 조정 구간입니다."
            ),
        )

    if current > ma20 > ma60:
        return (
            "⏸ 보유",
            (
                "20일 평균이 60일 평균보다 높고 "
                f"RSI는 {rsi:.0f}입니다."
            ),
        )

    return (
        "⏸ 보유·관찰",
        (
            "단기와 중기 추세가 혼조이며 "
            f"RSI는 {rsi:.0f}입니다."
        ),
    )


# =========================================================
# 뉴스
# =========================================================

def fetch_news():
    """
    Google News RSS에서
    핵심 뉴스 제목 3개를 가져옵니다.
    """

    articles = []
    seen = set()

    for query in NEWS_QUERIES:
        encoded = urllib.parse.quote(
            query
        )

        url = (
            "https://news.google.com/rss/search"
            f"?q={encoded}"
            "&hl=ko"
            "&gl=KR"
            "&ceid=KR:ko"
        )

        try:
            feed = feedparser.parse(
                url,
                request_headers=HEADERS,
            )

            for entry in feed.entries[:5]:
                title = html.unescape(
                    entry.get(
                        "title",
                        "",
                    )
                ).strip()

                link = entry.get(
                    "link",
                    "",
                ).strip()

                if not title:
                    continue

                if title in seen:
                    continue

                seen.add(title)

                articles.append(
                    {
                        "title": title,
                        "link": link,
                    }
                )

                if len(articles) >= 3:
                    return articles

        except Exception as error:
            print(
                "뉴스 가져오기 오류:",
                error,
            )

    return articles[:3]


# =========================================================
# 규칙 기반 종합 안내
# =========================================================

def rule_based_summary(
    market_data,
    rule_views,
):
    vix = market_data.get("VIX")

    if vix:
        vix_value = vix["current"]
    else:
        vix_value = None

    if vix_value is None:
        market_text = (
            "VIX 확인이 필요해 시장 변동성 "
            "판단을 보수적으로 유지하세요."
        )

    elif vix_value >= 25:
        market_text = (
            "VIX가 높은 구간이므로 "
            "한 번에 매수하기보다 "
            "분할 접근과 현금 관리가 중요합니다."
        )

    elif vix_value >= 20:
        market_text = (
            "변동성이 다소 높아 "
            "추격 매수보다 가격과 비중을 "
            "나누는 접근이 적절합니다."
        )

    else:
        market_text = (
            "VIX 기준 변동성은 비교적 안정적이지만 "
            "종목별 과열 여부는 별도로 확인하세요."
        )

    actions = ", ".join(
        f"{name} {view[0]}"
        for name, view
        in rule_views.items()
    )

    return (
        f"{market_text}\n"
        f"관심 종목 참고 신호: {actions}\n"
        "※ 자동 계산된 참고 정보이며 "
        "실제 주문 지시가 아닙니다."
    )


# =========================================================
# OpenAI 분석
# =========================================================

def make_ai_analysis(
    market_data,
    watch_data,
    news,
    rule_views,
):
    """
    OPENAI_API_KEY가 있을 때만
    AI 종합 분석을 생성합니다.

    키가 없다면 규칙 기반 분석을 사용합니다.
    """

    if not OPENAI_API_KEY:
        return None

    if OpenAI is None:
        return None

    market_text = "\n".join(
        market_line(
            name,
            market_data.get(name),
        )
        for name in MARKET_SYMBOLS
    )

    watch_lines = []

    for name, item in WATCHLIST.items():
        data = watch_data.get(name)

        if data is None:
            watch_lines.append(
                f"{name}: 데이터 없음"
            )
            continue

        action, reason = rule_views[name]

        if data["rsi14"] is None:
            rsi_text = "확인 불가"
        else:
            rsi_text = (
                f"{data['rsi14']:.1f}"
            )

        watch_lines.append(
            f"{name}: "
            f"{stock_price(item['currency'], data['current'])}, "
            f"등락 {data['change_pct']:+.2f}%, "
            f"RSI {rsi_text}, "
            f"데이터 출처 {data['source']}, "
            f"규칙 신호 {action}, "
            f"근거 {reason}"
        )

    news_text = "\n".join(
        f"- {item['title']}"
        for item in news
    )

    if not news_text:
        news_text = "뉴스 없음"

    prompt = f"""
아래 데이터만 사용하여 한국어 아침 투자 브리핑을 작성하세요.

규칙:
- 확정적 예측을 하지 마세요.
- 수익을 보장하지 마세요.
- 제공되지 않은 사실을 만들지 마세요.
- 전체 분량은 9문장 이내로 작성하세요.
- 매수나 매도를 직접 명령하지 마세요.
- 검토, 관찰, 분할 접근, 비중 점검 등의 표현을 사용하세요.

구성:
1. 시장 전체 해석 2문장
2. 삼성전자 1문장
3. SK하이닉스 1문장
4. 현대차2우B 1문장
5. TIGER 코리아휴머노이드로봇산업 1문장
6. 테슬라 1문장
7. 뉴스에서 오늘 확인할 변수 1문장
8. 마지막에 현금 운용 원칙 1문장

[시장 데이터]
{market_text}

[관심 종목]
{chr(10).join(watch_lines)}

[뉴스 제목]
{news_text}
""".strip()

    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=45.0,
            max_retries=2,
        )

        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=(
                "당신은 제공된 숫자와 뉴스 제목만 "
                "근거로 신중하게 설명하는 "
                "투자 브리핑 작성자입니다."
            ),
            input=prompt,
        )

        return response.output_text.strip()

    except Exception as error:
        print(
            "OpenAI 분석 오류:",
            error,
        )

        return None


# =========================================================
# 텔레그램
# =========================================================

def send_html(text: str):
    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=60,
    )

    print(
        "텔레그램 응답:",
        response.status_code,
        response.text,
    )

    response.raise_for_status()


# =========================================================
# 메인 실행
# =========================================================

def main():
    # 토스증권 토큰
    toss_token = get_toss_access_token()

    # 미국 시장 데이터
    market_data = {
        name: get_market_metrics(symbol)
        for name, symbol
        in MARKET_SYMBOLS.items()
    }

    # 관심 종목 데이터
    watch_data = {
        name: get_watch_metrics(
            item,
            toss_token,
        )
        for name, item
        in WATCHLIST.items()
    }

    # VIX
    vix_data = market_data.get("VIX")

    if vix_data:
        vix_value = vix_data["current"]
    else:
        vix_value = None

    # 종목별 규칙 판단
    rule_views = {
        name: technical_view(
            data,
            vix_value,
        )
        for name, data
        in watch_data.items()
    }

    # 뉴스
    news = fetch_news()

    # AI 분석
    ai_analysis = make_ai_analysis(
        market_data,
        watch_data,
        news,
        rule_views,
    )

    now = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime(
        "%Y년 %m월 %d일 %H:%M"
    )

    # -------------------------
    # 미국 시장 표시
    # -------------------------

    market_section = "\n".join(
        market_line(
            name,
            market_data.get(name),
        )
        for name in MARKET_SYMBOLS
        if name != "VIX"
    )

    # -------------------------
    # 관심 종목 표시
    # -------------------------

    stock_blocks = []

    for name, item in WATCHLIST.items():
        data = watch_data.get(name)
        action, reason = rule_views[name]

        if data is None:
            stock_blocks.append(
                f"<b>{html.escape(name)}</b>\n"
                "⚪ 데이터 확인 실패"
            )
            continue

        source = html.escape(
            data["source"]
        )

        stock_blocks.append(
            f"<b>{html.escape(name)}</b>\n"
            f"{stock_price(item['currency'], data['current'])} "
            f"{arrow(data['change_pct'])} "
            f"{abs(data['change_pct']):.2f}%\n"
            f"{action}\n"
            f"{html.escape(reason)}\n"
            f"<i>출처: {source}</i>"
        )

    # 첫 번째 메시지
    first_message = (
        "☀️ <b>오전 8시 AI 투자 브리핑</b>\n"
        f"{now}\n\n"
        "━━━━━━━━━━━━━━\n"
        "🇺🇸 <b>미국 시장</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{html.escape(market_section)}\n\n"
        "━━━━━━━━━━━━━━\n"
        "📌 <b>관심 종목 기술적 참고</b>\n"
        "━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(stock_blocks)
        + (
            "\n\n※ 최근 종가·이동평균·RSI 기반 "
            "참고 신호이며 실제 주문 지시가 아닙니다."
        )
    )

    send_html(first_message)

    # -------------------------
    # 뉴스 표시
    # -------------------------

    news_lines = []

    for index, item in enumerate(
        news,
        start=1,
    ):
        safe_title = html.escape(
            item["title"]
        )

        safe_link = html.escape(
            item["link"],
            quote=True,
        )

        if safe_link:
            news_lines.append(
                f'{index}. '
                f'<a href="{safe_link}">'
                f'{safe_title}</a>'
            )
        else:
            news_lines.append(
                f"{index}. {safe_title}"
            )

    if news_lines:
        news_section = "\n\n".join(
            news_lines
        )
    else:
        news_section = (
            "뉴스를 가져오지 못했습니다."
        )

    # -------------------------
    # AI 또는 규칙 기반 분석
    # -------------------------

    if ai_analysis:
        analysis_title = (
            "🤖 <b>AI 종합 분석</b>"
        )

        analysis_section = html.escape(
            ai_analysis
        )

    else:
        analysis_title = (
            "🧭 <b>규칙 기반 종합 안내</b>"
        )

        analysis_section = html.escape(
            rule_based_summary(
                market_data,
                rule_views,
            )
        )

    # 두 번째 메시지
    second_message = (
        "📰 <b>오늘의 핵심 뉴스 3건</b>\n\n"
        f"{news_section}\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{analysis_title}\n"
        "━━━━━━━━━━━━━━\n"
        f"{analysis_section}\n\n"
        "※ 뉴스 제목과 가격 데이터는 "
        "시차가 있을 수 있습니다."
    )

    send_html(second_message)


if __name__ == "__main__":
    main()
