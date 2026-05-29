"""Sentinel 4주차 — DART 긴급 공시 모니터링 (평일 09:00~18:00 매시간).

흐름:
  1. OpenDartReader로 관심 종목 최근 1시간 공시 수집
  2. 룰 기반 긴급/일반 분류
  3. 긴급 공시만 Gemini 2.5 Flash로 3줄 요약 + 영향 분석
  4. 긴급 공시 → 텔레그램 발송 / 전체 → Supabase 저장
"""

import io
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Windows 콘솔 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from sentinel.ai.gemini_client import generate_disclosure_summary
from sentinel.notifiers.supabase_writer import save_disclosure
from sentinel.notifiers.telegram import send_alert

KST = ZoneInfo("Asia/Seoul")

REQUIRED_ENV = [
    "DART_API_KEY",
    "GOOGLE_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "SUPABASE_URL", "SUPABASE_KEY",
]

# ── 긴급 공시 분류 키워드 ─────────────────────────────────────────────────────
URGENT_KEYWORDS = [
    "유상증자", "무상증자",
    "자기주식취득", "자사주취득", "자기주식소각", "자사주소각",
    "최대주주변경", "최대주주 변경",
    "합병", "분할",
    "주요사항보고",   # 대규모 투자 등 포함
    "풍문또는보도", "조회공시",
]

# 긴급 판단 추가: 금액 기준 (제목에 금액 파싱 — 간략 처리)
_LARGE_INVESTMENT_THRESHOLD = 100_000_000_000  # 1000억


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


# ── DART 공시 수집 ───────────────────────────────────────────────────────────

def fetch_recent_disclosures(dart_key: str, watchlist: list, hours: int = 1) -> list[dict]:
    """관심 종목 최근 N시간 이내 신규 공시 수집.

    Returns: [{"ticker", "company_name", "title", "disclosure_type", "rcp_no", "rcept_dt"}, ...]
    """
    try:
        import opendartreader as OpenDartReader
    except ImportError:
        raise ImportError("OpenDartReader 패키지가 없습니다. pip install OpenDartReader")

    dart = OpenDartReader.OpenDartReader(dart_key)
    now_kst = datetime.now(KST)
    since = now_kst - timedelta(hours=hours)
    since_str = since.strftime("%Y%m%d")

    results: list[dict] = []
    for item in watchlist:
        ticker = item["code"]
        name   = item["name"]
        try:
            df = dart.list(ticker, start=since_str, kind="A", final="N")
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                # rcept_dt: YYYYMMDD 형식, 오늘 또는 어제만 필터
                rcept_dt_str = str(row.get("rcept_dt", ""))
                try:
                    rcept_dt = datetime.strptime(rcept_dt_str, "%Y%m%d").replace(tzinfo=KST)
                    # 시간 단위 필터는 DART API가 날짜 단위 → 날짜만 체크
                    if rcept_dt.date() < since.date():
                        continue
                except ValueError:
                    pass
                results.append({
                    "ticker":          ticker,
                    "company_name":    name,
                    "title":           str(row.get("report_nm", "")),
                    "disclosure_type": str(row.get("report_nm", "")),
                    "rcp_no":          str(row.get("rcept_no", "")),
                    "rcept_dt":        rcept_dt_str,
                })
        except Exception as e:
            print(f"  [dart] {name}({ticker}) 공시 조회 실패: {e}")

    return results


# ── 긴급 분류 ────────────────────────────────────────────────────────────────

def classify_urgency(title: str) -> str:
    """공시 제목으로 긴급/일반 판단."""
    for kw in URGENT_KEYWORDS:
        if kw in title:
            return "긴급"
    return "일반"


# ── 텔레그램 메시지 포맷 ─────────────────────────────────────────────────────

def format_disclosure_message(disclosure: dict) -> str:
    impact_icon = {"긍정": "📈", "중립": "➡️", "부정": "📉"}.get(
        disclosure.get("impact", "중립"), "❓"
    )
    ai_text = disclosure.get("ai_summary", "(AI 요약 없음)")
    return (
        f"🚨 *긴급 공시* — {disclosure['company_name']} `{disclosure['ticker']}`\n"
        f"📋 {disclosure['title']}\n"
        f"\n{impact_icon} *포트폴리오 영향*: {disclosure.get('impact', '중립')}\n"
        f"\n🤖 *AI 요약*\n{ai_text}"
    )


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now_kst = datetime.now(KST)

    print("=" * 55)
    print("  Sentinel — DART 공시 모니터링 시작")
    print(f"  실행 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    print("=" * 55)

    if not check_env():
        sys.exit(1)

    config = load_config()
    watchlist: list = config.get("watchlist", [])

    # ── STEP 1: 공시 수집 ────────────────────────────────────────────────────
    print(f"\n[1/3] DART 공시 수집 ({len(watchlist)}개 종목, 최근 1시간)...")
    try:
        disclosures = fetch_recent_disclosures(
            os.environ["DART_API_KEY"], watchlist, hours=1
        )
        print(f"  총 {len(disclosures)}건 발견")
    except Exception as e:
        print(f"  ✗ 공시 수집 실패: {e}")
        sys.exit(1)

    if not disclosures:
        print("\n최근 1시간 내 신규 공시 없음. 정상 종료.")
        return

    # ── STEP 2: 긴급 분류 ────────────────────────────────────────────────────
    print("\n[2/3] 긴급/일반 분류...")
    for d in disclosures:
        d["urgency"] = classify_urgency(d["title"])

    urgent = [d for d in disclosures if d["urgency"] == "긴급"]
    normal = [d for d in disclosures if d["urgency"] == "일반"]
    print(f"  긴급: {len(urgent)}건 / 일반: {len(normal)}건")

    for d in disclosures:
        tag = "🚨" if d["urgency"] == "긴급" else "  "
        print(f"  {tag} [{d['urgency']}] {d['company_name']} — {d['title']}")

    # ── STEP 3: 긴급 공시 AI 분석 → 알림 → 저장 ─────────────────────────────
    print(f"\n[3/3] 긴급 공시 처리 ({len(urgent)}건)...")
    for d in urgent:
        print(f"\n  {d['company_name']} ({d['ticker']}) — {d['title']}")

        # AI 요약
        try:
            summary, impact = generate_disclosure_summary(
                os.environ["GOOGLE_API_KEY"], d
            )
            d["ai_summary"] = summary
            d["impact"]     = impact
            print(f"  ✓ AI 요약 완료 (영향: {impact})")
        except Exception as e:
            d["ai_summary"] = f"AI 요약 실패: {e}"
            d["impact"]     = "중립"
            print(f"  ✗ AI 요약 실패: {e}")

        # 텔레그램
        msg = format_disclosure_message(d)
        ok = send_alert(
            os.environ["TELEGRAM_BOT_TOKEN"],
            os.environ["TELEGRAM_CHAT_ID"],
            msg,
        )
        print(f"  텔레그램: {'✓ 발송 완료' if ok else '✗ 발송 실패'}")

    # ── 전체 공시 Supabase 저장 ───────────────────────────────────────────────
    print(f"\n  Supabase 저장 (전체 {len(disclosures)}건)...")
    saved = 0
    for d in disclosures:
        ok = save_disclosure(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
            d,
        )
        if ok:
            saved += 1
    print(f"  ✓ {saved}/{len(disclosures)}건 저장 완료")

    print(f"\n{'=' * 55}")
    print(f"  완료: 긴급 {len(urgent)}건 알림, 전체 {len(disclosures)}건 저장")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
