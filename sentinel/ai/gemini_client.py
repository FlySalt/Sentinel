"""Gemini 2.5 Flash-Lite — 특이점 상황 설명 생성."""

import time

from google import genai
from google.genai import errors as genai_errors

MODEL = "gemini-2.5-flash-lite"
_MAX_RETRIES = 3
_RETRY_DELAY = 5  # seconds


def generate_alert_summary(api_key: str, alert: dict) -> str:
    """특이점 데이터를 받아 투자자용 상황 설명 3~4문장을 반환.

    503/429 등 일시적 과부하 오류는 최대 3회 재시도.
    """
    client = genai.Client(api_key=api_key)

    reasons_text = "\n".join(f"- {r}" for r in alert["alert_reasons"])
    prompt = f"""당신은 국내 주식 시장 분석 어시스턴트입니다.
다음 종목에서 이상 징후가 감지되었습니다.

종목: {alert['name']} ({alert['ticker']})
현재가: {alert['price']:,}원
등락률: {alert['change_pct']:+.2f}%
거래량: {alert['volume']:,}주 (최근 평균 대비 {alert['volume_ratio']:.1f}배)

감지 조건:
{reasons_text}

위 상황을 투자자에게 알리는 간결한 한국어 설명을 3~4문장으로 작성해주세요.
- 현재 상황 요약
- 주의해야 할 점
- 다음에 모니터링할 포인트
순서로 작성하되, 투자 권유나 확정적 예측은 하지 마세요."""

    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return response.text.strip()
        except genai_errors.ServerError as e:
            last_err = e
            if attempt < _MAX_RETRIES:
                print(f"    Gemini 서버 과부하, {_RETRY_DELAY}초 후 재시도 ({attempt}/{_MAX_RETRIES})...")
                time.sleep(_RETRY_DELAY)
        except Exception as e:
            raise e

    raise RuntimeError(f"Gemini 재시도 {_MAX_RETRIES}회 실패: {last_err}")
