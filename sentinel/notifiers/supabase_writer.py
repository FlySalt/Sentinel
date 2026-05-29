"""Supabase — 모든 테이블 읽기/쓰기.

테이블 목록:
  alerts         : 특이점 알림
  briefings      : 예측 브리핑
  disclosures    : DART 공시
  daily_summary  : 장 마감 일일 요약
  flows          : 외국인·기관 수급
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from supabase import create_client

KST = ZoneInfo("Asia/Seoul")


def save_briefing(url: str, key: str, briefing: dict) -> bool:
    """briefings 테이블에 예측 브리핑 1건 삽입.

    briefing 필드:
      date (str YYYY-MM-DD), content (str), confidence_score (int),
      risk_level (str), factor_scores (dict)
    """
    try:
        client = create_client(url, key)
        client.table("briefings").insert(
            {
                "date": briefing["date"],
                "content": briefing["content"],
                "confidence_score": briefing["confidence_score"],
                "risk_level": briefing["risk_level"],
                "factor_scores": briefing["factor_scores"],
            }
        ).execute()
        return True
    except Exception as e:
        print(f"  [supabase] briefings 저장 실패: {e}")
        return False


def save_disclosure(url: str, key: str, disclosure: dict) -> bool:
    """disclosures 테이블에 공시 1건 삽입.

    disclosure 필드:
      ticker, company_name, title, disclosure_type,
      urgency, ai_summary (optional), impact (optional)
    """
    try:
        client = create_client(url, key)
        client.table("disclosures").insert(
            {
                "ticker": disclosure["ticker"],
                "company_name": disclosure["company_name"],
                "title": disclosure["title"],
                "disclosure_type": disclosure.get("disclosure_type", ""),
                "urgency": disclosure.get("urgency", "일반"),
                "ai_summary": disclosure.get("ai_summary", ""),
                "impact": disclosure.get("impact", ""),
            }
        ).execute()
        return True
    except Exception as e:
        print(f"  [supabase] disclosures 저장 실패 ({disclosure.get('ticker')}): {e}")
        return False


def get_today_alerts(url: str, key: str) -> list[dict]:
    """오늘(KST) 발생한 alerts 목록 조회.

    triggered_at 텍스트 컬럼(YYYY-MM-DD 시작)으로 필터링.
    Returns: [{"name","ticker","change_pct","volume_ratio",...}, ...]
    """
    try:
        client   = create_client(url, key)
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        resp = (
            client.table("alerts")
            .select("*")
            .like("triggered_at", f"{date_str}%")
            .execute()
        )
        return resp.data or []
    except Exception as e:
        print(f"  [supabase] alerts 조회 실패: {e}")
        return []


def get_recent_flows(url: str, key: str, ticker: str, days: int = 20) -> list[dict]:
    """특정 종목의 최근 N일 수급 데이터 조회 (최신순).

    Returns: [{"date","foreign_net","institution_net",...}, ...]
    """
    try:
        client = create_client(url, key)
        since  = (datetime.now(KST) - timedelta(days=days + 5)).strftime("%Y-%m-%d")
        resp = (
            client.table("flows")
            .select("date,foreign_net,institution_net")
            .eq("ticker", ticker)
            .gte("date", since)
            .order("date", desc=True)
            .limit(days)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        print(f"  [supabase] flows 조회 실패 ({ticker}): {e}")
        return []


def save_alert(url: str, key: str, alert: dict) -> bool:
    """alerts 테이블에 1건 삽입. 성공 True, 실패 False."""
    try:
        client = create_client(url, key)
        triggered_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        client.table("alerts").insert(
            {
                "ticker": alert["ticker"],
                "name": alert["name"],
                "price": alert["price"],
                "change_pct": alert["change_pct"],
                "volume_ratio": alert["volume_ratio"],
                "ai_summary": alert.get("ai_summary", ""),
                "alert_type": alert.get("alert_type", ""),
                "triggered_at": triggered_at,
            }
        ).execute()
        return True
    except Exception as e:
        print(f"  [supabase] 저장 실패 ({alert['ticker']}): {e}")
        return False


def save_daily_summary(url: str, key: str, summary: dict) -> bool:
    """daily_summary 테이블에 마감 요약 1건 삽입.

    summary 필드:
      date (str YYYY-MM-DD), market_summary (str),
      stock_data (dict), alerts_count (int), ai_summary (str)
    """
    try:
        client = create_client(url, key)
        client.table("daily_summary").insert(
            {
                "date":           summary["date"],
                "market_summary": summary.get("market_summary", ""),
                "stock_data":     summary.get("stock_data", {}),
                "alerts_count":   summary.get("alerts_count", 0),
                "ai_summary":     summary.get("ai_summary", ""),
            }
        ).execute()
        return True
    except Exception as e:
        print(f"  [supabase] daily_summary 저장 실패: {e}")
        return False


def save_flow(url: str, key: str, flow: dict) -> bool:
    """flows 테이블에 수급 데이터 1건 삽입.

    flow 필드:
      date (str YYYY-MM-DD), ticker (str), name (str),
      foreign_net (int 원), institution_net (int 원),
      foreign_consecutive_days (int), institution_consecutive_days (int),
      direction_changed (bool), ai_comment (str)
    """
    try:
        client = create_client(url, key)
        client.table("flows").insert(
            {
                "date":                          flow["date"],
                "ticker":                        flow["ticker"],
                "name":                          flow["name"],
                "foreign_net":                   flow.get("foreign_net", 0),
                "institution_net":               flow.get("institution_net", 0),
                "foreign_consecutive_days":      flow.get("foreign_consecutive_days", 0),
                "institution_consecutive_days":  flow.get("institution_consecutive_days", 0),
                "direction_changed":             flow.get("direction_changed", False),
                "ai_comment":                    flow.get("ai_comment", ""),
            }
        ).execute()
        return True
    except Exception as e:
        print(f"  [supabase] flows 저장 실패 ({flow.get('ticker')}): {e}")
        return False
