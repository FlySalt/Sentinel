"""텔레그램 Bot API — 특이점 알림 메시지 포맷 및 발송."""

import requests


def format_alert_message(alert: dict) -> str:
    """특이점 데이터를 마크다운 텔레그램 메시지로 변환."""
    icon = "📈" if alert["change_pct"] > 0 else "📉"
    reasons_text = "\n".join(f"  • {r}" for r in alert["alert_reasons"])
    ai_summary = alert.get("ai_summary", "(AI 분석 없음)")

    return (
        f"{icon} *{alert['name']}* `{alert['ticker']}`\n"
        f"현재가: *{alert['price']:,}원* ({alert['change_pct']:+.2f}%)\n"
        f"거래량: {alert['volume']:,}주 ({alert['volume_ratio']:.1f}배)\n"
        f"\n🔔 *감지 조건*\n{reasons_text}\n"
        f"\n🤖 *AI 분석*\n{ai_summary}"
    )


def send_alert(bot_token: str, chat_id: str, message: str) -> bool:
    """텔레그램 sendMessage API 호출. 성공 True, 실패 False."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"  [telegram] 발송 실패: {e}")
        return False
