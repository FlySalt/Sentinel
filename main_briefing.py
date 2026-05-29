"""Sentinel 4주차 — 야간 미국장 → 한국장 예측 브리핑 (매일 06:00 KST).

흐름:
  1. Alpha Vantage로 7개 거시 팩터 수집
  2. 키움 REST API로 USD/KRW 환율 수집
  3. Google News RSS로 글로벌 매크로 뉴스 수집
  4. 룰 기반 신뢰도 계산 (0~100%)
  5. Gemini 2.5 Pro로 브리핑 생성
  6. 텔레그램 발송 + Supabase briefings 저장
"""

import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Windows 콘솔 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from sentinel.ai.gemini_client import generate_briefing_summary
from sentinel.collectors.alpha_vantage import collect_macro_factors
from sentinel.collectors.kiwoom import get_access_token, get_usdkrw
from sentinel.collectors.news import fetch_macro_news
from sentinel.notifiers.supabase_writer import save_briefing
from sentinel.notifiers.telegram import send_alert

KST = ZoneInfo("Asia/Seoul")

REQUIRED_ENV = [
    "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET",
    "ALPHA_VANTAGE_KEY",
    "GOOGLE_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "SUPABASE_URL", "SUPABASE_KEY",
]


def load_config() -> dict:
    for d in [Path(__file__).parent, Path(__file__).parent.parent]:
        p = d / "config.yaml"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("config.yaml을 찾을 수 없습니다.")


def check_env() -> bool:
    missing = [v for v in REQUIRED_ENV if not os.getenv(v)]
    if missing:
        print(f"[오류] 환경 변수 누락: {missing}")
        return False
    return True


# ── 신뢰도 계산 ─────────────────────────────────────────────────────────────

def _judge_factor(key: str, value: float) -> str:
    """팩터별 긍정/부정/중립 판단."""
    if key in ("nasdaq", "sp500", "sox", "kospi_fut"):
        if value >= 0.5:
            return "긍정"
        if value <= -0.5:
            return "부정"
        return "중립"
    if key == "dxy":
        # 달러 강세(+)는 신흥국 부정
        if value >= 0.3:
            return "부정"
        if value <= -0.3:
            return "긍정"
        return "중립"
    if key == "vix":
        if value <= 18:
            return "긍정"
        if value >= 25:
            return "부정"
        return "중립"
    if key == "us10y":
        if value <= 3.5:
            return "긍정"
        if value >= 4.5:
            return "부정"
        return "중립"
    return "중립"


def calc_confidence(factors: dict, news: dict) -> tuple[int, dict]:
    """7개 팩터 긍정/부정으로 신뢰도(0~100) 계산.

    Returns: (confidence_score, factor_judgments)
    """
    keys = ["nasdaq", "sp500", "sox", "vix", "dxy", "kospi_fut", "us10y"]
    judgments: dict[str, str] = {}
    positive = 0
    total = 0

    for key in keys:
        factor = factors.get(key)
        if factor is None or "error" in (factor or {}):
            judgments[key] = "데이터없음"
            continue
        val = factor["value"]
        j = _judge_factor(key, val)
        judgments[key] = j
        total += 1
        if j == "긍정":
            positive += 1

    base_score = round((positive / total * 100) if total > 0 else 50)

    # 뉴스 리스크 보정
    risk_penalty = {
        "없음": 0,
        "낮음": -5,
        "중간": -10,
        "높음": -20,
    }.get(news.get("risk_level", "없음"), 0)

    score = max(0, min(100, base_score + risk_penalty))
    return score, judgments


# ── 브리핑 메시지 포맷 ────────────────────────────────────────────────────────

def format_briefing_message(
    date_str: str,
    confidence: int,
    risk_level: str,
    usdkrw: float | None,
    factors: dict,
    ai_content: str,
) -> str:
    """텔레그램 브리핑 메시지 생성."""
    conf_bar = "🟢" if confidence >= 60 else "🟡" if confidence >= 40 else "🔴"
    risk_icon = {"없음": "✅", "낮음": "🟡", "중간": "🟠", "높음": "🔴"}.get(risk_level, "❓")

    def fmt_pct(f: dict | None) -> str:
        if not f:
            return "N/A"
        v = f.get("value", 0)
        return f"{v:+.2f}%" if f.get("unit") == "%" else f"{v:.2f}"

    nasdaq_str   = fmt_pct(factors.get("nasdaq"))
    sp500_str    = fmt_pct(factors.get("sp500"))
    sox_str      = fmt_pct(factors.get("sox"))
    vix_val      = factors.get("vix", {}).get("value", 0) if factors.get("vix") else 0
    dxy_str      = fmt_pct(factors.get("dxy"))
    kfut_str     = fmt_pct(factors.get("kospi_fut"))
    us10y_val    = factors.get("us10y", {}).get("value", 0) if factors.get("us10y") else 0
    usdkrw_str   = f"{usdkrw:,.1f}원" if usdkrw else "N/A"

    return (
        f"🌙 *오늘의 예측 브리핑* — {date_str}\n"
        f"{conf_bar} 신뢰도: *{confidence}%*  |  리스크: {risk_icon} *{risk_level}*\n"
        f"\n📊 *거시 지표*\n"
        f"  나스닥: {nasdaq_str}  |  S&P500: {sp500_str}\n"
        f"  SOX(반도체): {sox_str}  |  VIX: {vix_val:.1f}\n"
        f"  달러(DXY): {dxy_str}  |  USD/KRW: {usdkrw_str}\n"
        f"  코스피200선물: {kfut_str}  |  미국채10Y: {us10y_val:.2f}%\n"
        f"\n🤖 *AI 브리핑*\n{ai_content}"
    )


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now_kst = datetime.now(KST)
    date_str = now_kst.strftime("%Y-%m-%d")

    print("=" * 55)
    print("  Sentinel — 예측 브리핑 시작")
    print(f"  실행 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    print("=" * 55)

    if not check_env():
        sys.exit(1)

    config = load_config()
    watchlist = config.get("watchlist", [])

    # ── STEP 1: Alpha Vantage 거시 팩터 ──────────────────────────────────────
    print("\n[1/5] Alpha Vantage 거시 팩터 수집...")
    factors = collect_macro_factors(os.environ["ALPHA_VANTAGE_KEY"])
    if factors.get("errors"):
        for err in factors["errors"]:
            print(f"  ⚠ {err}")

    # ── STEP 2: 키움 USD/KRW 환율 ────────────────────────────────────────────
    print("\n[2/5] 키움 USD/KRW 환율 수집...")
    usdkrw: float | None = None
    try:
        token = get_access_token(
            os.environ["KIWOOM_APP_KEY"],
            os.environ["KIWOOM_APP_SECRET"],
        )
        usdkrw = get_usdkrw(token)
        if usdkrw:
            print(f"  USD/KRW: {usdkrw:,.1f}원")
        else:
            print("  USD/KRW: 수집 실패 (None)")
    except Exception as e:
        print(f"  키움 토큰/환율 수집 실패: {e}")

    # ── STEP 3: Google News RSS ───────────────────────────────────────────────
    print("\n[3/5] 글로벌 매크로 뉴스 수집...")
    news = fetch_macro_news(max_items=10)
    if news.get("error"):
        print(f"  뉴스 수집 오류: {news['error']}")
    else:
        print(f"  헤드라인 {len(news['headlines'])}건 | "
              f"리스크키워드 {news['risk_count']}건 | "
              f"리스크레벨: {news['risk_level']}")
        for h in news["headlines"][:5]:
            print(f"    - {h[:70]}")

    # ── STEP 4: 신뢰도 계산 ──────────────────────────────────────────────────
    print("\n[4/5] 신뢰도 계산...")
    confidence, judgments = calc_confidence(factors, news)
    risk_level = news.get("risk_level", "없음")
    print(f"  신뢰도: {confidence}%  |  리스크: {risk_level}")
    for k, j in judgments.items():
        val = factors.get(k, {}).get("value", "N/A") if factors.get(k) else "N/A"
        print(f"    {k:12s}: {j}  ({val})")

    # ── STEP 5: Gemini 브리핑 생성 ────────────────────────────────────────────
    print("\n[5/5] Gemini 2.5 Pro 브리핑 생성...")
    try:
        ai_content = generate_briefing_summary(
            api_key=os.environ["GOOGLE_API_KEY"],
            factors=factors,
            usdkrw=usdkrw,
            news=news,
            confidence=confidence,
            risk_level=risk_level,
            watchlist=watchlist,
        )
        preview = ai_content[:100].replace("\n", " ")
        print(f"  ✓ {preview}...")
    except Exception as e:
        ai_content = f"AI 브리핑 생성 실패: {e}"
        print(f"  ✗ {e}")

    # ── 텔레그램 발송 ─────────────────────────────────────────────────────────
    msg = format_briefing_message(date_str, confidence, risk_level, usdkrw, factors, ai_content)
    ok = send_alert(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
        msg,
    )
    print(f"\n  텔레그램: {'✓ 발송 완료' if ok else '✗ 발송 실패'}")

    # ── Supabase 저장 ─────────────────────────────────────────────────────────
    factor_scores = {
        k: {
            "value": factors[k]["value"] if factors.get(k) else None,
            "judgment": judgments.get(k, "데이터없음"),
        }
        for k in ["nasdaq", "sp500", "sox", "vix", "dxy", "kospi_fut", "us10y"]
    }
    if usdkrw:
        factor_scores["usdkrw"] = {"value": usdkrw, "judgment": "참고"}

    ok = save_briefing(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
        {
            "date": date_str,
            "content": ai_content,
            "confidence_score": confidence,
            "risk_level": risk_level,
            "factor_scores": factor_scores,
        },
    )
    print(f"  Supabase: {'✓ 저장 완료' if ok else '✗ 저장 실패'}")

    print(f"\n{'=' * 55}")
    print("  예측 브리핑 완료")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
