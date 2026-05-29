"""Gemini API 래퍼 — 특이점 알림 / 예측 브리핑 / 공시 요약.

용도별 모델:
  특이점 알림  : gemini-2.5-flash      (500 RPD 무료)
  예측 브리핑  : gemini-2.5-pro        (복잡한 추론)
  공시 요약    : gemini-2.5-flash      (문서 처리)

모델별 무료 tier 일일 한도 (RPD):
  gemini-2.5-flash      :   500건
  gemini-2.5-flash-lite :    20건  ← 사용 금지 (너무 낮음)
  gemini-2.0-flash-lite :     0건  ← 이 API 키에서 차단됨
"""

import re
import time

from google import genai
from google.genai import errors as genai_errors

MODEL_FLASH  = "gemini-2.5-flash"   # 특이점 알림, 공시 요약
MODEL_PRO    = "gemini-2.5-pro"     # 예측 브리핑
MODEL        = MODEL_FLASH           # 하위 호환 (generate_alert_summary용)

_MAX_RETRIES = 3
_RETRY_DELAY = 35  # 429 응답의 retryDelay 기본값보다 여유 있게 대기


def generate_alert_summary(api_key: str, alert: dict) -> str:
    """특이점 데이터를 받아 투자자용 상황 설명 3~4문장을 반환.

    503 서버 과부하 및 429 할당량 초과 오류는 최대 3회 재시도.
    429 응답에 retryDelay가 있으면 해당 시간만큼 대기.
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
        except (genai_errors.ServerError, genai_errors.ClientError) as e:
            last_err = e
            if attempt >= _MAX_RETRIES:
                break
            # 429 응답의 retryDelay 값을 파싱해 정확히 대기
            delay = _RETRY_DELAY
            msg = str(e)
            m = re.search(r"retryDelay.*?(\d+)s", msg)
            if m:
                delay = int(m.group(1)) + 5
            print(f"    Gemini 오류, {delay}초 후 재시도 ({attempt}/{_MAX_RETRIES})...")
            time.sleep(delay)
        except Exception as e:
            raise e

    raise RuntimeError(f"Gemini 재시도 {_MAX_RETRIES}회 실패: {last_err}")


def _call_with_retry(api_key: str, model: str, prompt: str) -> str:
    """공통 Gemini 호출 + 재시도 로직."""
    client = genai.Client(api_key=api_key)
    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return response.text.strip()
        except (genai_errors.ServerError, genai_errors.ClientError) as e:
            last_err = e
            if attempt >= _MAX_RETRIES:
                break
            delay = _RETRY_DELAY
            m = re.search(r"retryDelay.*?(\d+)s", str(e))
            if m:
                delay = int(m.group(1)) + 5
            print(f"    Gemini 오류, {delay}초 후 재시도 ({attempt}/{_MAX_RETRIES})...")
            time.sleep(delay)
        except Exception as e:
            raise e
    raise RuntimeError(f"Gemini 재시도 {_MAX_RETRIES}회 실패: {last_err}")


def generate_briefing_summary(
    api_key: str,
    factors: dict,
    usdkrw: float | None,
    news: dict,
    confidence: int,
    risk_level: str,
    watchlist: list,
) -> str:
    """거시 팩터 + 뉴스 기반 한국장 예측 브리핑 생성 (gemini-2.5-pro).

    Returns: 브리핑 본문 문자열
    """
    def fmt(key: str) -> str:
        f = factors.get(key)
        if not f:
            return "데이터없음"
        v = f.get("value", 0)
        u = f.get("unit", "")
        return f"{v:+.2f}{u}" if u == "%" else f"{v:.2f}{u}"

    headlines_text = "\n".join(f"  - {h}" for h in news.get("headlines", [])[:8])
    stock_names = ", ".join(item["name"] for item in watchlist)

    prompt = f"""당신은 국내 주식 시장 전문 애널리스트입니다.
다음 데이터를 바탕으로 오늘 한국 주식 시장 예측 브리핑을 작성해주세요.

## 전날 미국장 지표
- 나스닥: {fmt('nasdaq')}
- S&P500: {fmt('sp500')}
- SOX(필라델피아 반도체): {fmt('sox')}
- VIX(공포지수): {fmt('vix')} pt
- 달러 인덱스(DXY): {fmt('dxy')}
- 코스피200 야간선물(EWY): {fmt('kospi_fut')}
- 미국 10년물 국채금리: {fmt('us10y')} %
- USD/KRW 환율: {f'{usdkrw:,.1f}원' if usdkrw else '데이터없음'}

## 글로벌 매크로 뉴스 ({news.get('risk_count', 0)}건 리스크 키워드 감지)
{headlines_text}

## 룰 기반 신뢰도: {confidence}% | 리스크 레벨: {risk_level}

## 관심 종목: {stock_names}

다음 형식으로 한국어 브리핑을 작성해주세요 (총 250자 내외):
1. 전날 미국장 흐름 한 줄 요약
2. 오늘 한국장 전망 (코스피/코스닥 방향성)
3. 주목할 섹터 또는 위험 요인 1~2개
4. 관심 종목 중 특별히 주목할 종목과 이유 (1~2개)

투자 권유 없이 객관적으로 작성하세요."""

    return _call_with_retry(api_key, MODEL_PRO, prompt)


def generate_disclosure_summary(api_key: str, disclosure: dict) -> tuple[str, str]:
    """DART 공시 원문 3줄 요약 + 포트폴리오 영향 분석 (gemini-2.5-flash).

    Returns: (ai_summary, impact)
      impact: "긍정" | "중립" | "부정"
    """
    prompt = f"""당신은 국내 주식 공시 전문가입니다.
다음 DART 공시를 분석해주세요.

## 공시 정보
- 종목: {disclosure['company_name']} ({disclosure['ticker']})
- 공시 유형: {disclosure.get('disclosure_type', '알 수 없음')}
- 공시 제목: {disclosure['title']}

## 요청 사항
1. 공시 내용 3줄 요약 (핵심 수치와 날짜 포함)
2. 포트폴리오 영향: 긍정 / 중립 / 부정 중 하나만 선택 후 이유 1문장

형식:
[요약]
- (1줄)
- (2줄)
- (3줄)

[영향]: 긍정/중립/부정 — (이유)"""

    response = _call_with_retry(api_key, MODEL_FLASH, prompt)

    # 영향 파싱
    impact = "중립"
    for keyword in ["긍정", "부정", "중립"]:
        if f"[영향]: {keyword}" in response or f"[영향]:{keyword}" in response:
            impact = keyword
            break
        # 대소문자/공백 관계없이 탐색
        if keyword in response.split("[영향]")[-1][:20] if "[영향]" in response else "":
            impact = keyword
            break

    return response, impact
