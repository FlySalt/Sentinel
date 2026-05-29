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

import requests
from datetime import datetime, timezone

BASE_URL = "https://www.alphavantage.co/query"

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
    result: dict = {k: None for k in ["nasdaq", "sp500", "sox", "vix", "dxy", "kospi_fut", "us10y"]}
    errors: list[str] = []

    # 등락률 팩터 (Global Quote)
    pct_factors = ["nasdaq", "sp500", "sox", "dxy", "kospi_fut"]
    for factor in pct_factors:
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

    # VIX — Global Quote로 현재값 조회
    try:
        quote = _get_global_quote(api_key, "VIX")
        vix_val = _price_from_quote(quote)
        result["vix"] = {"value": vix_val, "unit": "pt", "symbol": "VIX"}
        print(f"  {'vix':12s}: {vix_val:.2f} pt")
    except Exception as e:
        errors.append(f"vix: {e}")
        result["vix"] = {"value": 0.0, "unit": "pt", "symbol": "VIX", "error": str(e)}
        print(f"  {'vix':12s}: 수집 실패 — {e}")

    # 미국 10년물 국채금리
    try:
        rate = _get_treasury_rate(api_key, _US10Y_MATURITY)
        result["us10y"] = {"value": rate, "unit": "%", "symbol": "US10Y"}
        print(f"  {'us10y':12s}: {rate:.3f}%")
    except Exception as e:
        errors.append(f"us10y: {e}")
        result["us10y"] = {"value": 0.0, "unit": "%", "symbol": "US10Y", "error": str(e)}
        print(f"  {'us10y':12s}: 수집 실패 — {e}")

    result["errors"] = errors
    result["collected_at"] = datetime.now(timezone.utc).isoformat()
    return result
