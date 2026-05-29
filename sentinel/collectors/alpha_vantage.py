"""Alpha Vantage API — 미국 거시 지표 수집.

수집 팩터 (7개):
  nasdaq     : 나스닥 등락률 (%)
  sp500      : S&P500 등락률 (%)
  sox        : 필라델피아 반도체 지수 등락률 (%)  ← ETF SOXX로 대체
  vix        : VIX 공포지수 현재값
  dxy        : 달러 인덱스 등락률 (%)
  kospi_fut  : 코스피200 야간선물 등락률 (%)  ← ETF IKF/EWY로 근사
  us10y      : 미국 10년물 국채금리 (%)

Alpha Vantage 무료 키: 25회/일, 5회/분
  → 종목당 1회 호출 → 총 7회 사용
"""

import time

import requests
from datetime import datetime, timezone

BASE_URL = "https://www.alphavantage.co/query"

# 무료 키: 5회/분 → 요청 사이 13초 대기
_FREE_TIER_DELAY = 13

# 팩터별 Alpha Vantage 심볼 매핑
_SYMBOLS: dict[str, str] = {
    "nasdaq": "QQQ",    # 나스닥 ETF
    "sp500":  "SPY",    # S&P500 ETF
    "sox":    "SOXX",   # 필라델피아 반도체 ETF
    "vix":    "VIX",    # CBOE VIX (Global Quote 지원 안 됨 → 별도 처리)
    "dxy":    "UUP",    # 달러 인덱스 ETF
    "kospi_fut": "EWY", # 한국 ETF로 야간선물 근사
}

_US10Y_MATURITY = "10year"


def _get_global_quote(api_key: str, symbol: str) -> dict:
    """GLOBAL_QUOTE 엔드포인트로 전일 종가 대비 등락률 조회."""
    resp = requests.get(
        BASE_URL,
        params={
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    quote = data.get("Global Quote", {})
    if not quote:
        raise ValueError(f"빈 응답: {symbol} — {data}")
    return quote


def _change_pct_from_quote(quote: dict) -> float:
    """Global Quote에서 등락률(%) 추출."""
    raw = quote.get("10. change percent", "0%").replace("%", "").strip()
    try:
        return round(float(raw), 2)
    except ValueError:
        return 0.0


def _price_from_quote(quote: dict) -> float:
    """Global Quote에서 현재가 추출."""
    try:
        return float(quote.get("05. price", "0"))
    except ValueError:
        return 0.0


def _fetch_vix_yahoo() -> float:
    """Yahoo Finance 비공식 API로 VIX 현재값 조회 (API 키 불필요)."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
    resp = requests.get(
        url,
        params={"interval": "1d", "range": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice") or meta.get("previousClose", 0)
    return round(float(price), 2)


def _get_fx_rate(api_key: str, from_currency: str, to_currency: str) -> float:
    """CURRENCY_EXCHANGE_RATE 엔드포인트로 현재 환율 조회."""
    resp = requests.get(
        BASE_URL,
        params={
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_currency,
            "to_currency": to_currency,
            "apikey": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    rate_info = data.get("Realtime Currency Exchange Rate", {})
    if not rate_info:
        raise ValueError(f"환율 빈 응답: {from_currency}/{to_currency} — {data}")
    try:
        return round(float(rate_info["5. Exchange Rate"]), 2)
    except (KeyError, ValueError) as e:
        raise ValueError(f"환율 파싱 오류: {rate_info}") from e


def _get_treasury_rate(api_key: str, maturity: str = "10year") -> float:
    """TREASURY_YIELD 엔드포인트로 미국 국채 금리 조회."""
    resp = requests.get(
        BASE_URL,
        params={
            "function": "TREASURY_YIELD",
            "interval": "daily",
            "maturity": maturity,
            "apikey": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    series = data.get("data", [])
    if not series:
        raise ValueError(f"국채금리 빈 응답: {data}")
    latest = series[0]  # 가장 최근
    try:
        return round(float(latest["value"]), 3)
    except (ValueError, KeyError):
        return 0.0


def collect_macro_factors(api_key: str) -> dict:
    """7개 거시 팩터를 수집해 dict로 반환.

    반환 형식:
    {
      "nasdaq":    {"value": -1.23, "unit": "%"},
      "sp500":     {"value": -0.85, "unit": "%"},
      "sox":       {"value": -2.10, "unit": "%"},
      "vix":       {"value": 18.45, "unit": "pt"},
      "dxy":       {"value":  0.32, "unit": "%"},
      "kospi_fut": {"value": -0.50, "unit": "%"},
      "us10y":     {"value":  4.32, "unit": "%"},
      "errors":    ["sox: 오류 메시지", ...],
    }
    """
    result: dict = {k: None for k in ["nasdaq", "sp500", "sox", "vix", "dxy", "kospi_fut", "us10y", "usdkrw"]}
    errors: list[str] = []

    # 등락률 팩터 (Global Quote) — 요청 사이 13초 대기 (무료 5회/분)
    pct_factors = ["nasdaq", "sp500", "sox", "dxy", "kospi_fut"]
    for i, factor in enumerate(pct_factors):
        if i > 0:
            time.sleep(_FREE_TIER_DELAY)
        symbol = _SYMBOLS[factor]
        try:
            quote = _get_global_quote(api_key, symbol)
            pct = _change_pct_from_quote(quote)
            result[factor] = {"value": pct, "unit": "%", "symbol": symbol}
            print(f"  {factor:12s}: {pct:+.2f}%  ({symbol})")
        except Exception as e:
            errors.append(f"{factor}: {e}")
            result[factor] = {"value": 0.0, "unit": "%", "symbol": symbol, "error": str(e)}
            print(f"  {factor:12s}: 수집 실패 — {e}")

    # VIX — Yahoo Finance 비공식 API (Alpha Vantage 무료 키 미지원)
    try:
        vix_val = _fetch_vix_yahoo()
        result["vix"] = {"value": vix_val, "unit": "pt", "symbol": "^VIX"}
        print(f"  {'vix':12s}: {vix_val:.2f} pt  (Yahoo Finance)")
    except Exception as e:
        errors.append(f"vix: {e}")
        result["vix"] = {"value": 0.0, "unit": "pt", "symbol": "^VIX", "error": str(e)}
        print(f"  {'vix':12s}: 수집 실패 — {e}")

    # 미국 10년물 국채금리
    time.sleep(_FREE_TIER_DELAY)
    try:
        rate = _get_treasury_rate(api_key, _US10Y_MATURITY)
        result["us10y"] = {"value": rate, "unit": "%", "symbol": "US10Y"}
        print(f"  {'us10y':12s}: {rate:.3f}%")
    except Exception as e:
        errors.append(f"us10y: {e}")
        result["us10y"] = {"value": 0.0, "unit": "%", "symbol": "US10Y", "error": str(e)}
        print(f"  {'us10y':12s}: 수집 실패 — {e}")

    # USD/KRW 환율 (키움 REST API 미지원 → Alpha Vantage FX로 대체)
    time.sleep(_FREE_TIER_DELAY)
    try:
        usdkrw = _get_fx_rate(api_key, "USD", "KRW")
        result["usdkrw"] = {"value": usdkrw, "unit": "원", "symbol": "USDKRW"}
        print(f"  {'usdkrw':12s}: {usdkrw:,.2f}원")
    except Exception as e:
        errors.append(f"usdkrw: {e}")
        result["usdkrw"] = {"value": 0.0, "unit": "원", "symbol": "USDKRW", "error": str(e)}
        print(f"  {'usdkrw':12s}: 수집 실패 — {e}")

    result["errors"] = errors
    result["collected_at"] = datetime.now(timezone.utc).isoformat()
    return result
