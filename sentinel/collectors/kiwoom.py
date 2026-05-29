"""키움 REST API — 토큰 발급 + 국내주식 시세/거래량/수급 수집.

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
  ka10085 = 외국인·기관 매매동향
  ka20004 = 업종일봉차트조회요청 (지수)

지수 데이터 보조:
  코스피·코스닥 지수는 Yahoo Finance 비공식 API로도 수집 가능
  (^KS11 = KOSPI, ^KQ11 = KOSDAQ)
"""

import requests
from datetime import datetime, timedelta
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


def get_usdkrw(token: str) -> float | None:
    """키움 API로 USD/KRW 환율 조회 (ka10090 현재환율조회).

    반환: float (예: 1380.5) 또는 실패 시 None
    """
    try:
        resp = requests.post(
            f"{BASE_URL}/api/dostk/forex",
            headers={
                "content-type": "application/json;charset=utf-8",
                "authorization": f"Bearer {token}",
                "api-id": "ka10090",
            },
            json={"frc_cd": "USD"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code", 0) != 0:
            raise ValueError(f"환율 조회 오류: {data.get('return_msg', data)}")
        # 응답 필드명: 실제 API 응답 기준 (cur_prc 또는 tdy_bse_rt)
        rate_str = (
            data.get("tdy_bse_rt")
            or data.get("cur_prc")
            or data.get("bass_rt")
            or "0"
        )
        rate = _parse_float(rate_str)
        return rate if rate > 0 else None
    except Exception as e:
        print(f"  [kiwoom] USD/KRW 환율 수집 실패: {e}")
        return None


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


# ── 지수 시세 ──────────────────────────────────────────────────────────────────

def get_index_price(index_code: str) -> dict | None:
    """코스피·코스닥 지수 종가·등락률 조회.

    Yahoo Finance 비공식 API 사용 (키움 지수 API 미지원).

    index_code: "KOSPI" | "KOSDAQ"
    Returns: {"index_code", "price", "change_pct"} | None
    """
    symbol_map = {
        "KOSPI":  "%5EKS11",   # ^KS11
        "KOSDAQ": "%5EKQ11",   # ^KQ11
    }
    symbol = symbol_map.get(index_code.upper())
    if not symbol:
        print(f"  [kiwoom] 알 수 없는 지수 코드: {index_code}")
        return None
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        resp = requests.get(
            url,
            params={"interval": "1d", "range": "2d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        meta   = result["meta"]
        price  = float(meta.get("regularMarketPrice") or meta.get("previousClose", 0))
        prev   = float(meta.get("chartPreviousClose") or meta.get("previousClose", price))
        change_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
        return {
            "index_code": index_code.upper(),
            "price":      round(price, 2),
            "change_pct": change_pct,
        }
    except Exception as e:
        print(f"  [kiwoom] {index_code} 지수 수집 실패: {e}")
        return None


# ── 외국인·기관 수급 ────────────────────────────────────────────────────────────

def get_investor_trend(token: str, ticker: str, name: str, days: int = 20) -> list[dict]:
    """외국인·기관 일별 순매수 금액 조회.

    데이터 소스:
      ka10008 (/api/dostk/frgnistt): 외국인 50일 목록 → foreign_net 히스토리
      ka10009 (/api/dostk/frgnistt): 당일 외국인+기관 → institution_net 보완

    응답 필드 (확인됨):
      ka10008: stk_frgnr[].{dt, close_pric, chg_qty}
      ka10009: {date, close_pric, orgn_daly_nettrde, frgnr_daly_nettrde}
               orgn_daly_nettrde / frgnr_daly_nettrde 단위: 주식수 (주)
               → 금액 근사치 = 순매매주식수 × close_pric

    NOTE: 당일 데이터는 T+2 결제 특성상 장 마감 후에도 당일 값이 비어 있을 수 있음.
          이 경우 당일 행은 chg_qty=0으로 처리되며, 직전 확정 영업일 데이터부터 반환.

    days: 조회 일수 (최대 20일 권장)
    Returns: [{"date", "foreign_net", "institution_net"}, ...] 최신순
      - foreign_net / institution_net: 원화 금액 근사치 (순매매주식수 × 종가)
      - 실패 시 [] 반환
    """
    # ── 1. ka10009: 당일 기관 데이터 수집 ──────────────────────────────────────
    today_inst_net  = 0
    today_date_str  = ""
    try:
        resp9 = requests.post(
            f"{BASE_URL}/api/dostk/frgnistt",
            headers={
                "content-type": "application/json;charset=utf-8",
                "authorization": f"Bearer {token}",
                "api-id": "ka10009",
            },
            json={"stk_cd": ticker},
            timeout=10,
        )
        resp9.raise_for_status()
        d9 = resp9.json()
        if d9.get("return_code", 0) == 0:
            today_date_str = str(d9.get("date", ""))
            price9 = abs(_parse_float(d9.get("close_pric", "0")))
            orgn_qty  = _parse_float(d9.get("orgn_daly_nettrde", "0") or "0")
            today_inst_net = int(orgn_qty * price9)
    except Exception:
        pass   # 기관 데이터 실패 시 0으로 처리

    # ── 2. ka10008: 외국인 히스토리 수집 ───────────────────────────────────────
    try:
        resp8 = requests.post(
            f"{BASE_URL}/api/dostk/frgnistt",
            headers={
                "content-type": "application/json;charset=utf-8",
                "authorization": f"Bearer {token}",
                "api-id": "ka10008",
            },
            json={"stk_cd": ticker},
            timeout=10,
        )
        resp8.raise_for_status()
        data8 = resp8.json()
        if data8.get("return_code", 0) != 0:
            raise ValueError(f"수급 조회 오류: {data8.get('return_msg', data8)}")

        rows = data8.get("stk_frgnr", [])
        if not rows:
            raise ValueError("빈 응답")

        # chg_qty=0인 미확정 행 제외
        valid_rows = [r for r in rows if r.get("chg_qty", "0") != "0"] or rows

        result = []
        for row in valid_rows[:days]:
            date_str = str(row.get("dt", ""))
            price    = abs(_parse_float(row.get("close_pric", "0")))
            chg_qty  = _parse_float(row.get("chg_qty", "0"))
            frgn_net = int(chg_qty * price)
            # 같은 날짜면 ka10009에서 수집한 기관 데이터 사용
            inst_net = today_inst_net if date_str == today_date_str else 0
            result.append({
                "date":            date_str,
                "foreign_net":     frgn_net,
                "institution_net": inst_net,
            })

        return result
    except Exception as e:
        print(f"  [kiwoom] {name}({ticker}) 수급 조회 실패: {e}")
        return []
