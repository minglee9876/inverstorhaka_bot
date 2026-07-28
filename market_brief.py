import html
import json
import math
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
# GitHub Secrets / 환경변수
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TOSS_CLIENT_ID = os.getenv("TOSS_CLIENT_ID", "").strip()
TOSS_CLIENT_SECRET = os.getenv("TOSS_CLIENT_SECRET", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# 기존 기본 모델을 유지합니다.
# GitHub Actions env에 OPENAI_MODEL을 넣으면 코드 수정 없이 교체할 수 있습니다.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
OPENAI_REASONING_EFFORT = os.getenv(
    "OPENAI_REASONING_EFFORT",
    "medium",
).strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN Secret이 없습니다.")

if not TELEGRAM_CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID Secret이 없습니다.")


# =========================================================
# 기본 설정
# =========================================================

TOSS_BASE_URL = "https://openapi.tossinvest.com"
KST = ZoneInfo("Asia/Seoul")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/124.0 Safari/537.36"
    )
}

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

NEWS_QUERIES = [
    "미국 증시 AI 반도체 when:1d",
    "삼성전자 SK하이닉스 HBM 반도체 when:1d",
    "현대차 테슬라 로봇 자동차 when:1d",
    "휴머노이드 로봇 ETF when:1d",
]


# =========================================================
# 공통 유틸리티
# =========================================================

def finite_float(value):
    """유효한 숫자는 float로, NaN/무한대/빈 값은 None으로 반환합니다."""
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def round_or_none(value, digits=4):
    number = finite_float(value)
    if number is None:
        return None
    return round(number, digits)


def json_ready(value):
    """OpenAI 입력 JSON에 NaN이나 pandas 타입이 섞이지 않게 정리합니다."""
    if isinstance(value, dict):
        return {
            str(key): json_ready(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]

    if isinstance(value, (float, int)):
        return round_or_none(value)

    return value


def pct_return(close: pd.Series, periods: int):
    if len(close) <= periods:
        return None

    past = finite_float(close.iloc[-1 - periods])
    current = finite_float(close.iloc[-1])

    if past in (None, 0) or current is None:
        return None

    return (current / past - 1) * 100


def distance_pct(current, reference):
    current = finite_float(current)
    reference = finite_float(reference)

    if current is None or reference in (None, 0):
        return None

    return (current / reference - 1) * 100


def display_pct(value):
    value = finite_float(value)
    if value is None:
        return "확인 불가"
    return f"{value:+.1f}%"


def display_number(value, digits=1):
    value = finite_float(value)
    if value is None:
        return "확인 불가"
    return f"{value:.{digits}f}"


def arrow(value: float) -> str:
    value = finite_float(value)

    if value is None or value == 0:
        return "－"
    if value > 0:
        return "▲"
    return "▼"


def stock_price(currency: str, value: float) -> str:
    if currency == "KRW":
        return f"₩{value:,.0f}"
    return f"${value:,.2f}"


def truncate_plain_text(text: str, max_chars: int = 3300) -> str:
    """
    GPT가 길이 지시를 초과해도 Telegram의 4096자 제한에 걸리지 않게 합니다.
    HTML escape 전에 일반 텍스트 상태에서 자릅니다.
    """
    text = (text or "").strip()

    if len(text) <= max_chars:
        return text

    shortened = text[:max_chars]

    if "\n" in shortened:
        shortened = shortened.rsplit("\n", 1)[0]

    return shortened.rstrip() + "\n\n…분량 제한으로 일부 내용이 생략되었습니다."


# =========================================================
# Yahoo Finance
# =========================================================

def get_yahoo_history(
    symbol: str,
    period: str = "1y",
) -> pd.DataFrame:
    """Yahoo Finance에서 일봉 데이터를 가져오며 최대 3번 재시도합니다."""
    last_error = None

    for attempt in range(3):
        try:
            frame = yf.Ticker(symbol).history(
                period=period,
                interval="1d",
                auto_adjust=False,
                timeout=30,
            )

            if not frame.empty and "Close" in frame.columns:
                return frame

        except Exception as error:
            last_error = error
            print(f"Yahoo {symbol} 오류 {attempt + 1}/3: {error}")

        time.sleep(2 * (attempt + 1))

    if last_error:
        print(f"Yahoo {symbol} 최종 오류: {last_error}")

    return pd.DataFrame()


# =========================================================
# 토스증권 API
# =========================================================

def get_toss_access_token():
    """Client Id와 Client Secret으로 토스증권 액세스 토큰을 발급받습니다."""
    if not TOSS_CLIENT_ID or not TOSS_CLIENT_SECRET:
        print("토스증권 Secret이 없습니다. Yahoo Finance만 사용합니다.")
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

        token = response.json().get("access_token")

        if not token:
            raise RuntimeError("토스증권 응답에 access_token이 없습니다.")

        print("토스증권 액세스 토큰 발급 성공")
        return token

    except Exception as error:
        print(f"토스증권 토큰 발급 실패. Yahoo로 전환합니다: {error}")
        return None


def get_toss_history(
    symbol: str,
    access_token: str,
) -> pd.DataFrame:
    """토스증권 Open API에서 최근 120개 일봉 데이터를 가져옵니다."""
    response = requests.get(
        f"{TOSS_BASE_URL}/api/v1/candles",
        headers={"Authorization": f"Bearer {access_token}"},
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

    candles = response.json().get("result", {}).get("candles", [])

    if not candles:
        raise RuntimeError(f"토스증권 {symbol} 일봉 데이터가 비어 있습니다.")

    rows = []

    for item in candles:
        row = {
            "Date": pd.to_datetime(item["timestamp"]),
            "Close": pd.to_numeric(
                item["closePrice"],
                errors="coerce",
            ),
        }

        # 토스 응답에 거래량 필드가 있을 때만 사용합니다.
        volume_value = (
            item.get("volume")
            or item.get("tradingVolume")
            or item.get("accumulatedTradingVolume")
        )

        if volume_value is not None:
            row["Volume"] = pd.to_numeric(
                volume_value,
                errors="coerce",
            )

        rows.append(row)

    frame = pd.DataFrame(rows).dropna(subset=["Close"])

    if frame.empty:
        raise RuntimeError(f"토스증권 {symbol} 종가 데이터가 없습니다.")

    return frame.sort_values("Date").set_index("Date")


# =========================================================
# 기술적 지표 계산
# =========================================================

def calculate_rsi(
    close: pd.Series,
    period: int = 14,
):
    """Wilder 방식에 가까운 지수평활 RSI를 계산합니다."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()
    average_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    relative_strength = average_gain / average_loss
    rsi = 100 - (100 / (1 + relative_strength))

    rsi = rsi.mask(
        (average_loss == 0) & (average_gain > 0),
        100,
    )
    rsi = rsi.mask(
        (average_gain == 0) & (average_loss > 0),
        0,
    )
    rsi = rsi.mask(
        (average_gain == 0) & (average_loss == 0),
        50,
    )

    valid = rsi.dropna()
    if valid.empty:
        return None
    return finite_float(valid.iloc[-1])


def metrics_from_frame(
    frame: pd.DataFrame,
    source: str,
):
    """
    가격뿐 아니라 기간 수익률, 이동평균 괴리, RSI, MACD,
    변동성, 최근 종가 범위, 가능한 경우 거래량 배수까지 계산합니다.
    """
    if frame.empty or "Close" not in frame.columns:
        return None

    close = pd.to_numeric(
        frame["Close"],
        errors="coerce",
    ).dropna()

    if len(close) < 2:
        return None

    current = finite_float(close.iloc[-1])
    previous = finite_float(close.iloc[-2])

    if current is None or previous in (None, 0):
        return None

    ma20 = finite_float(close.tail(20).mean()) if len(close) >= 20 else None
    ma60 = finite_float(close.tail(60).mean()) if len(close) >= 60 else None
    ma120 = finite_float(close.tail(120).mean()) if len(close) >= 120 else None

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_series = ema12 - ema26
    macd_signal_series = macd_series.ewm(span=9, adjust=False).mean()
    macd_histogram_series = macd_series - macd_signal_series

    returns = close.pct_change().dropna()
    volatility_20d = None

    if len(returns) >= 20:
        volatility_20d = finite_float(
            returns.tail(20).std() * math.sqrt(252) * 100
        )

    close_high_20d = (
        finite_float(close.tail(20).max())
        if len(close) >= 20
        else None
    )
    close_low_20d = (
        finite_float(close.tail(20).min())
        if len(close) >= 20
        else None
    )
    close_high_60d = (
        finite_float(close.tail(60).max())
        if len(close) >= 60
        else None
    )

    position_20d = None
    if (
        close_high_20d is not None
        and close_low_20d is not None
        and close_high_20d != close_low_20d
    ):
        position_20d = (
            (current - close_low_20d)
            / (close_high_20d - close_low_20d)
            * 100
        )

    drawdown_60d = None
    if close_high_60d not in (None, 0):
        drawdown_60d = (current / close_high_60d - 1) * 100

    volume_ratio_20d = None
    if "Volume" in frame.columns:
        volume = pd.to_numeric(
            frame["Volume"],
            errors="coerce",
        ).dropna()

        if len(volume) >= 20:
            average_volume = finite_float(volume.tail(20).mean())
            current_volume = finite_float(volume.iloc[-1])

            if average_volume not in (None, 0) and current_volume is not None:
                volume_ratio_20d = current_volume / average_volume

    last_index = close.index[-1]
    if hasattr(last_index, "strftime"):
        data_date = last_index.strftime("%Y-%m-%d")
    else:
        data_date = str(last_index)

    return {
        "current": current,
        "previous": previous,
        "change_pct": (current / previous - 1) * 100,
        "change_5d_pct": pct_return(close, 5),
        "change_20d_pct": pct_return(close, 20),
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "distance_ma20_pct": distance_pct(current, ma20),
        "distance_ma60_pct": distance_pct(current, ma60),
        "distance_ma120_pct": distance_pct(current, ma120),
        "rsi14": calculate_rsi(close),
        "macd": finite_float(macd_series.iloc[-1]),
        "macd_signal": finite_float(macd_signal_series.iloc[-1]),
        "macd_histogram": finite_float(macd_histogram_series.iloc[-1]),
        "volatility_20d_annualized_pct": volatility_20d,
        "volume_ratio_20d": volume_ratio_20d,
        "close_high_20d": close_high_20d,
        "close_low_20d": close_low_20d,
        "position_in_20d_range_pct": position_20d,
        "drawdown_from_60d_high_pct": drawdown_60d,
        "data_date": data_date,
        "source": source,
    }


def get_market_metrics(symbol: str):
    frame = get_yahoo_history(symbol, period="1y")
    return metrics_from_frame(frame, "Yahoo Finance")


def get_watch_metrics(
    item: dict,
    toss_token,
):
    """토스증권을 먼저 사용하고 실패하면 Yahoo Finance로 전환합니다."""
    if toss_token:
        try:
            frame = get_toss_history(item["toss"], toss_token)
            result = metrics_from_frame(frame, "토스증권")

            if result is not None:
                return result

        except Exception as error:
            print(
                f"토스증권 {item['toss']} 실패. "
                f"Yahoo로 전환: {error}"
            )

    yahoo_frame = get_yahoo_history(item["yahoo"], period="1y")
    return metrics_from_frame(yahoo_frame, "Yahoo Finance")


# =========================================================
# 표시 형식
# =========================================================

def market_line(
    name: str,
    data,
) -> str:
    if data is None:
        return f"{name}: 데이터 확인 실패"

    change = data["change_pct"]

    if name == "미국 10년물":
        bp = (data["current"] - data["previous"]) * 100
        return (
            f"{name}: {data['current']:.3f}% "
            f"{arrow(bp)} {abs(bp):.1f}bp"
        )

    if name == "VIX":
        return (
            f"{name}: {data['current']:.2f} "
            f"{arrow(change)} {abs(change):.2f}%"
        )

    return (
        f"{name}: {data['current']:,.2f} "
        f"{arrow(change)} {abs(change):.2f}%"
    )


def technical_detail(data) -> str:
    if data is None:
        return "기술지표 확인 불가"

    macd_direction = (
        "개선"
        if finite_float(data.get("macd_histogram")) is not None
        and data["macd_histogram"] > 0
        else "약화"
    )

    return (
        f"5일 {display_pct(data.get('change_5d_pct'))} · "
        f"20일 {display_pct(data.get('change_20d_pct'))}\n"
        f"20일선 괴리 {display_pct(data.get('distance_ma20_pct'))} · "
        f"60일선 괴리 {display_pct(data.get('distance_ma60_pct'))}\n"
        f"RSI {display_number(data.get('rsi14'))} · "
        f"MACD 모멘텀 {macd_direction}"
    )


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
    gap60 = data.get("distance_ma60_pct")
    change5 = data.get("change_5d_pct")
    macd_hist = data.get("macd_histogram")

    if ma20 is None or ma60 is None or rsi is None:
        return (
            "⏸ 보유·관찰",
            "이동평균 또는 RSI 계산에 필요한 자료가 부족합니다.",
        )

    if rsi >= 75:
        return (
            "⚠️ 비중 확대 자제",
            (
                f"RSI {rsi:.0f}로 과열권이며, "
                f"5일 수익률은 {display_pct(change5)}입니다."
            ),
        )

    if current < ma60:
        macd_text = "개선 중" if macd_hist is not None and macd_hist > 0 else "약화 중"
        return (
            "⚠️ 관망·비중 점검",
            (
                f"현재가가 60일선 대비 {display_pct(gap60)}이고 "
                f"RSI는 {rsi:.0f}, MACD 모멘텀은 {macd_text}입니다."
            ),
        )

    if (
        vix_value is not None
        and vix_value >= 25
        and current <= ma20
        and rsi <= 45
    ):
        return (
            "✅ 분할 접근 검토",
            (
                f"VIX {vix_value:.1f}의 높은 변동성 환경에서 "
                f"RSI {rsi:.0f}의 단기 조정 구간입니다."
            ),
        )

    if current > ma20 > ma60:
        return (
            "⏸ 보유·추세 관찰",
            (
                f"현재가가 20·60일선 위에 있고 "
                f"RSI는 {rsi:.0f}입니다."
            ),
        )

    return (
        "⏸ 보유·관찰",
        (
            f"단기·중기 추세가 혼조이며 "
            f"RSI는 {rsi:.0f}, 5일 수익률은 "
            f"{display_pct(change5)}입니다."
        ),
    )


# =========================================================
# 뉴스
# =========================================================

def fetch_news():
    """Google News RSS에서 중복을 제거한 핵심 뉴스 제목 3개를 가져옵니다."""
    articles = []
    seen = set()

    for query in NEWS_QUERIES:
        encoded = urllib.parse.quote(query)
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
                    entry.get("title", "")
                ).strip()
                link = entry.get("link", "").strip()

                if not title or title in seen:
                    continue

                seen.add(title)
                source_info = entry.get("source", {})

                if hasattr(source_info, "get"):
                    source = source_info.get("title", "")
                else:
                    source = ""

                articles.append(
                    {
                        "title": title,
                        "link": link,
                        "source": source,
                        "published": entry.get("published", ""),
                        "query": query,
                    }
                )

                if len(articles) >= 3:
                    return articles

        except Exception as error:
            print("뉴스 가져오기 오류:", error)

    return articles[:3]


# =========================================================
# 규칙 기반 종합 안내
# =========================================================

def rule_based_summary(
    market_data,
    rule_views,
):
    vix = market_data.get("VIX")
    vix_value = vix["current"] if vix else None

    if vix_value is None:
        market_text = (
            "VIX 확인이 필요해 시장 변동성 판단을 "
            "보수적으로 유지할 필요가 있습니다."
        )
    elif vix_value >= 25:
        market_text = (
            "VIX가 높은 구간이므로 가격 변동 확대와 "
            "현금 비중 관리가 중요합니다."
        )
    elif vix_value >= 20:
        market_text = (
            "변동성이 다소 높아 추격보다 가격과 비중을 "
            "나누는 접근이 필요합니다."
        )
    else:
        market_text = (
            "VIX 기준 시장 변동성은 비교적 안정적이지만 "
            "종목별 모멘텀은 별도로 확인해야 합니다."
        )

    actions = "\n".join(
        f"• {name}: {view[0]} — {view[1]}"
        for name, view in rule_views.items()
    )

    return (
        f"{market_text}\n\n"
        f"{actions}\n\n"
        "※ 자동 계산된 참고 정보이며 실제 주문 지시가 아닙니다."
    )


# =========================================================
# OpenAI 교차분석
# =========================================================

AI_INSTRUCTIONS = """
당신은 한국어로 작성하는 시장 및 기술지표 해설 보조 AI다.
제공된 JSON 데이터만 사용해 규칙 기반 결과를 교차검토하고,
단순 반복이 아닌 추세·모멘텀·변동성·시장 환경의 관계를 설명한다.

반드시 지킬 원칙:
1. JSON에 없는 가격, 실적, 뉴스 본문, 목표가, 지지선·저항선을 만들지 않는다.
2. 뉴스는 제목·출처·시각만 제공되므로 본문을 읽은 것처럼 말하지 않는다.
3. 규칙 신호에 무조건 동의하지 말고, 지표가 상충하면 그 사실을 명시한다.
4. RSI가 낮다는 이유만으로 바닥이나 반등을 단정하지 않는다.
5. 매수·매도를 명령하거나 수익을 보장하지 않는다.
6. 숫자를 언급할 때는 입력에 있는 값을 사용하고, 데이터가 없으면 생략한다.
7. 짧은 기간 급락과 중기 추세 훼손을 구분한다.
8. 마크다운 표와 ** 굵은 글씨는 사용하지 않는다.
9. 전체 결과는 공백 포함 약 2,800자 이내로 작성한다.

출력 형식:
[시장 환경]
• 지수, 반도체 지수, VIX, 금리, 달러를 연결한 해석 3~4개

[종목별 GPT 교차분석]
각 종목마다 아래 3개 항목을 작성한다.
종목명
• 추세·모멘텀: 5일/20일 수익률, 이동평균 괴리, RSI, MACD를 연결
• 규칙 교차판단: 규칙 신호를 지지하는 근거와 반대 또는 완화 근거
• 다음 확인 조건: 입력에 있는 20일 종가 고가·저가나 이동평균을 이용한 조건

[오늘의 종합 안내]
• 관심종목의 공통 위험 또는 상대적으로 다른 흐름
• 뉴스 제목으로 확인 가능한 변수와 확인 불가능한 부분
• 다음 브리핑에서 확인할 항목 3개

마지막 줄:
※ AI 해설은 입력 데이터 기반 참고 정보이며 실제 주문 지시가 아닙니다.
""".strip()


def build_ai_payload(
    market_data,
    watch_data,
    news,
    rule_views,
):
    market_payload = {}

    for name, symbol in MARKET_SYMBOLS.items():
        market_payload[name] = {
            "ticker": symbol,
            "metrics": market_data.get(name),
        }

    watch_payload = {}

    for name, item in WATCHLIST.items():
        action, reason = rule_views[name]
        watch_payload[name] = {
            "ticker": item["yahoo"],
            "currency": item["currency"],
            "metrics": watch_data.get(name),
            "rule_result": {
                "signal": action,
                "reason": reason,
            },
        }

    return json_ready(
        {
            "generated_at_kst": datetime.now(KST).isoformat(
                timespec="minutes"
            ),
            "market": market_payload,
            "watchlist": watch_payload,
            "news": news,
            "data_notes": [
                "가격은 공급원별 최근 일봉이며 실시간 체결가가 아닐 수 있음",
                "20일 고가·저가는 장중 고저가가 아닌 종가 기준",
                "거래량 데이터가 없는 공급원은 volume_ratio_20d가 null",
                "뉴스는 RSS 제목만 수집하며 기사 본문은 제공하지 않음",
            ],
        }
    )


def make_ai_analysis(
    market_data,
    watch_data,
    news,
    rule_views,
):
    """OPENAI_API_KEY가 있을 때 GPT 기술지표 교차분석을 생성합니다."""
    if not OPENAI_API_KEY or OpenAI is None:
        return None

    payload = build_ai_payload(
        market_data,
        watch_data,
        news,
        rule_views,
    )

    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=90.0,
            max_retries=2,
        )

        response = client.responses.create(
            model=OPENAI_MODEL,
            reasoning={
                "effort": OPENAI_REASONING_EFFORT,
            },
            text={
                "verbosity": "medium",
            },
            instructions=AI_INSTRUCTIONS,
            input=(
                "다음 JSON을 분석하세요. 모든 숫자와 판단 근거는 "
                "이 JSON 안에서만 가져오세요.\n\n"
                + json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            ),
            max_output_tokens=3000,
        )

        result = response.output_text.strip()
        return result or None

    except Exception as error:
        print("OpenAI 분석 오류:", error)
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


def build_news_html(news):
    news_lines = []

    for index, item in enumerate(news, start=1):
        safe_title = html.escape(item["title"])
        safe_link = html.escape(
            item["link"],
            quote=True,
        )

        if safe_link:
            news_lines.append(
                f'{index}. <a href="{safe_link}">{safe_title}</a>'
            )
        else:
            news_lines.append(f"{index}. {safe_title}")

    if news_lines:
        return "\n\n".join(news_lines)

    return "뉴스를 가져오지 못했습니다."


# =========================================================
# 메인 실행
# =========================================================

def main():
    toss_token = get_toss_access_token()

    market_data = {
        name: get_market_metrics(symbol)
        for name, symbol in MARKET_SYMBOLS.items()
    }

    watch_data = {
        name: get_watch_metrics(item, toss_token)
        for name, item in WATCHLIST.items()
    }

    vix_data = market_data.get("VIX")
    vix_value = vix_data["current"] if vix_data else None

    rule_views = {
        name: technical_view(data, vix_value)
        for name, data in watch_data.items()
    }

    news = fetch_news()

    ai_analysis = make_ai_analysis(
        market_data,
        watch_data,
        news,
        rule_views,
    )

    now = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M")

    market_section = "\n".join(
        market_line(name, market_data.get(name))
        for name in MARKET_SYMBOLS
        if name != "VIX"
    )

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

        source = html.escape(data["source"])
        detail = html.escape(technical_detail(data))

        stock_blocks.append(
            f"<b>{html.escape(name)}</b>\n"
            f"{stock_price(item['currency'], data['current'])} "
            f"{arrow(data['change_pct'])} "
            f"{abs(data['change_pct']):.2f}%\n"
            f"{action}\n"
            f"{html.escape(reason)}\n"
            f"{detail}\n"
            f"<i>출처: {source} · 기준일: "
            f"{html.escape(data['data_date'])}</i>"
        )

    first_message = (
        "☀️ <b>AI 투자 브리핑</b>\n"
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
            "\n\n※ 최근 일봉 기반 참고 신호이며 "
            "실시간 시세 또는 실제 주문 지시가 아닙니다."
        )
    )

    send_html(first_message)

    news_message = (
        "📰 <b>오늘의 핵심 뉴스 3건</b>\n\n"
        f"{build_news_html(news)}\n\n"
        "※ 뉴스 제목과 가격 데이터는 시차가 있을 수 있습니다."
    )

    send_html(news_message)

    if ai_analysis:
        analysis_title = "🤖 <b>GPT 기술지표 교차분석</b>"
        analysis_text = truncate_plain_text(ai_analysis)
    else:
        analysis_title = "🧭 <b>규칙 기반 종합 안내</b>"
        analysis_text = rule_based_summary(
            market_data,
            rule_views,
        )

    analysis_message = (
        f"{analysis_title}\n"
        "━━━━━━━━━━━━━━\n"
        f"{html.escape(analysis_text)}"
    )

    send_html(analysis_message)


if __name__ == "__main__":
    main()
