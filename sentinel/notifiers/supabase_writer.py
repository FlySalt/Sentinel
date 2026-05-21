"""Supabase — alerts 테이블에 특이점 레코드 저장."""

from datetime import datetime
from zoneinfo import ZoneInfo

from supabase import create_client

KST = ZoneInfo("Asia/Seoul")


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
