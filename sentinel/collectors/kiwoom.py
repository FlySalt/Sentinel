"""키움 REST API — 토큰 발급 + 국내주식 시세/거래량 수집.

실제 응답 기준 (ka10081 /api/dostk/chart 일봉):
  stk_dt_pole_chart_qry[0]  → 가장 최근 영업일
    cur_prc       : 종가/현재가 (문자열, 부호 없음)
    pred_pre      : 전일대비 금액 ("+5500" / "-106000") — 이미 부호 포함
    pred_pre_sig  : 부호 코드 (2=상승, 3=보합, 5=하락)
    trde_tern_rt  : 거래회전율 (거래량/상장주식수 %) — 등락률 아님
    trde_qty      : 거래량
    dt            : 날짜 (YYYYMMDD)

API 구분:
  ka10081 = 주식일봉차트조회요청 (일봉) ← 사용
  ka10082 = 주식주봉차트조회요청 (주봉) ← 사용 금지
  ka10083 = 주식월봉차트조회요청 (월봉)
"""

import requests
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

BASE_URL = "https://api.kiwoom.com"


def get_access_token(app_key: str, app_secret: str) -> str:
    """OAuth2 client_credentials 방식으로 액세스 토큰 발급."""
    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("return_code", 0) != 0:
        raise ValueError(f"키움 토큰 오류: {data.get('return_msg', data)}")
    return data.get("token") or data.get("access_token")


def _parse_float(value, default: float = 0.0) -> float:
    """키움 숫자 문자열 파싱 — 콤마·부호(+/-) 포함."""
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def _fetch_chart(token: str, ticker: str, rows: int = 10) -> list:
    """ka10081 일봉차트 조회. 최근 rows개 반환.

    ka10081 = 주식일봉차트조회요청 (일봉 전용 API)
    ka10082는 주봉 전용이므로 사용 금지.
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    resp = requests.post(
        f"{BASE_URL}/api/dostk/chart",
        headers={
            "content-type": "application/json;charset=utf-8",
            "authorization": f"Bearer {token}",
            "api-id": "ka10081",
        },
        json={
            "stk_cd": ticker,
            "base_dt": today,
            "upd_stkpc_tp": "1",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("return_code", 0) != 0:
        raise ValueError(f"일봉 조회 오류: {data.get('return_msg', data)}")
    return data.get("stk_dt_pole_chart_qry", [])[:rows]


def get_stock_data(token: str, ticker: str, name: str, lookback_days: int = 5) -> dict | None:
    """종목 1개의 현재가·등락률·거래량 배율 수집 (ka10082 단일 호출).

    rows[0] = 가장 최근 영업일 (오늘 또는 마지막 장 마감일)
    rows[1:] = 직전 N일 (평균 거래량 산출)

    등락률: pred_pre(전일대비 금액) 사용 — qry_term_tp="1"(일봉)이면 일간 변동분.
      trde_tern_rt는 거래회전율(turnover ratio)이므로 등락률이 아님.
    """
    try:
        rows = _fetch_chart(token, ticker, rows=lookback_days + 2)
        if not rows:
            raise ValueError("빈 응답")

        today_row = rows[0]
        price    = int(_parse_float(today_row.get("cur_prc", "0")))
        volume   = int(_parse_float(today_row.get("trde_qty", "0")))

        # 등락률 = pred_pre / 전일종가 × 100
        # pred_pre 문자열에 이미 +/- 부호 포함 (예: "+5500", "-106000")
        pred_pre   = _parse_float(today_row.get("pred_pre", "0"))
        prev_close = price - pred_pre
        change_pct = (pred_pre / prev_close * 100) if prev_close != 0 else 0.0

        past_vols    = [int(_parse_float(r.get("trde_qty", "0"))) for r in rows[1:]]
        avg_vol      = sum(past_vols) / len(past_vols) if past_vols else 1
        volume_ratio = round(volume / avg_vol, 2) if avg_vol > 0 else 0.0

        return {
            "ticker": ticker,
            "name": name,
            "price": price,
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "avg_volume": int(avg_vol),
            "volume_ratio": volume_ratio,
        }
    except Exception as e:
        print(f"  [kiwoom] {name}({ticker}) 수집 실패: {e}")
        return None
