"""Supabase — alerts / briefings / disclosures 테이블 저장."""

from datetime import datetime
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
