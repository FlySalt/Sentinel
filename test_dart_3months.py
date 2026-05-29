"""DART 3개월치 공시 전체 파이프라인 테스트.

긴급 → AI 요약 + 텔레그램 + Supabase 저장
일반 → Supabase 저장만

실행: python test_dart_3months.py
"""

import io
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

# main_dart.py의 함수들 재사용
from main_dart import (
    classify_urgency,
    format_disclosure_message,
    _fetch_document_text,
    URGENT_KEYWORDS,
)
from sentinel.ai.gemini_client import generate_disclosure_summary
from sentinel.notifiers.supabase_writer import save_disclosure
from sentinel.notifiers.telegram import send_alert

KST = ZoneInfo("Asia/Seoul")
MONTHS = 3


def load_config() -> dict:
    p = Path(__file__).parent / "config.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_3month_disclosures(dart_key: str, watchlist: list) -> list[dict]:
    """최근 3개월치 공시 전체 수집."""
    import opendartreader as OpenDartReader

    dart = OpenDartReader.OpenDartReader(dart_key)
    now_kst = datetime.now(KST)
    since = now_kst - timedelta(days=90)
    since_str = since.strftime("%Y%m%d")

    print(f"  수집 기간: {since_str} ~ {now_kst.strftime('%Y%m%d')}")

    results: list[dict] = []
    for item in watchlist:
        ticker = item["code"]
        name = item["name"]
        try:
            df = dart.list(ticker, start=since_str, kind="A", final="N")
            if df is None or df.empty:
                print(f"  {name}({ticker}): 공시 없음")
                continue
            count = len(df)
            print(f"  {name}({ticker}): {count}건 발견")
            for _, row in df.iterrows():
                rcp_no = str(row.get("rcept_no", ""))
                results.append({
                    "ticker":          ticker,
                    "company_name":    name,
                    "title":           str(row.get("report_nm", "")),
                    "disclosure_type": str(row.get("report_nm", "")),
                    "rcp_no":          rcp_no,
                    "rcept_dt":        str(row.get("rcept_dt", "")),
                    "document":        "",  # 긴급 공시만 원문 수집 (속도 최적화)
                })
        except Exception as e:
            print(f"  {name}({ticker}) 조회 실패: {e}")

    return results


def main():
    now_kst = datetime.now(KST)
    print("=" * 60)
    print("  DART 3개월치 공시 전체 파이프라인 테스트")
    print(f"  실행: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    print("=" * 60)

    dart_key = os.getenv("DART_API_KEY")
    google_key = os.getenv("GOOGLE_API_KEY")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    sb_url = os.getenv("SUPABASE_URL")
    sb_key = os.getenv("SUPABASE_KEY")

    missing = [k for k, v in {
        "DART_API_KEY": dart_key, "GOOGLE_API_KEY": google_key,
        "TELEGRAM_BOT_TOKEN": tg_token, "TELEGRAM_CHAT_ID": tg_chat,
        "SUPABASE_URL": sb_url, "SUPABASE_KEY": sb_key,
    }.items() if not v]
    if missing:
        print(f"[오류] 환경변수 누락: {missing}")
        sys.exit(1)

    config = load_config()
    watchlist = config.get("watchlist", [])

    # ── STEP 1: 공시 수집 (3개월) ───────────────────────────────────────────
    print(f"\n[1/3] DART 공시 수집 ({len(watchlist)}개 종목, 최근 3개월)...")
    import opendartreader as OpenDartReader
    dart = OpenDartReader.OpenDartReader(dart_key)

    now_kst2 = datetime.now(KST)
    since = now_kst2 - timedelta(days=90)
    since_str = since.strftime("%Y%m%d")
    print(f"  수집 기간: {since_str} ~ {now_kst2.strftime('%Y%m%d')}")

    disclosures: list[dict] = []
    for item in watchlist:
        ticker = item["code"]
        name = item["name"]
        try:
            df = dart.list(ticker, start=since_str, kind="A", final="N")
            if df is None or df.empty:
                print(f"  {name}({ticker}): 공시 없음")
                continue
            print(f"  {name}({ticker}): {len(df)}건")
            for _, row in df.iterrows():
                rcp_no = str(row.get("rcept_no", ""))
                disclosures.append({
                    "ticker":          ticker,
                    "company_name":    name,
                    "title":           str(row.get("report_nm", "")),
                    "disclosure_type": str(row.get("report_nm", "")),
                    "rcp_no":          rcp_no,
                    "rcept_dt":        str(row.get("rcept_dt", "")),
                    "document":        "",
                })
        except Exception as e:
            print(f"  {name}({ticker}) 조회 실패: {e}")

    print(f"\n  총 {len(disclosures)}건 수집 완료")

    if not disclosures:
        print("공시 없음. 종료.")
        return

    # ── STEP 2: 긴급/일반 분류 ──────────────────────────────────────────────
    print("\n[2/3] 긴급/일반 분류...")
    for d in disclosures:
        d["urgency"] = classify_urgency(d["title"])

    urgent = [d for d in disclosures if d["urgency"] == "긴급"]
    normal = [d for d in disclosures if d["urgency"] == "일반"]

    print(f"\n  ✅ 긴급: {len(urgent)}건 / 일반: {len(normal)}건")
    print()

    if urgent:
        print("  🚨 긴급 공시 목록:")
        for d in urgent:
            print(f"    [{d['rcept_dt']}] {d['company_name']} — {d['title']}")
    print()
    print("  📋 일반 공시 목록 (상위 15건):")
    for d in normal[:15]:
        print(f"    [{d['rcept_dt']}] {d['company_name']} — {d['title']}")
    if len(normal) > 15:
        print(f"    ... 외 {len(normal)-15}건")

    # ── STEP 3: 전체 공시 원문 수집 + AI 요약 → 긴급만 텔레그램 ─────────────
    print(f"\n[3/3] 전체 공시 AI 분석 ({len(disclosures)}건) — 긴급만 텔레그램...")
    for d in disclosures:
        tag = "🚨 긴급" if d["urgency"] == "긴급" else "   일반"
        print(f"\n  [{tag}] {d['company_name']} ({d['ticker']}) — {d['title']} [{d['rcept_dt']}]")

        # 원문 수집 (전체)
        doc_text = _fetch_document_text(dart, d["rcp_no"])
        d["document"] = doc_text
        print(f"  원문: {'수집 완료 (' + str(len(doc_text)) + '자)' if doc_text else '없음 (제목 기반 분석)'}")

        # AI 요약 (전체)
        try:
            summary, impact = generate_disclosure_summary(google_key, d)
            d["ai_summary"] = summary
            d["impact"] = impact
            print(f"  AI 요약: ✓ (영향: {impact})")
        except Exception as e:
            d["ai_summary"] = f"AI 요약 실패: {e}"
            d["impact"] = "중립"
            print(f"  AI 요약: ✗ {e}")

        # 텔레그램 — 긴급만
        if d["urgency"] == "긴급":
            msg = format_disclosure_message(d)
            ok = send_alert(tg_token, tg_chat, msg)
            print(f"  텔레그램: {'✓ 발송 완료' if ok else '✗ 발송 실패'}")

    # ── 전체 공시 Supabase 저장 ─────────────────────────────────────────────
    print(f"\n  Supabase 저장 (전체 {len(disclosures)}건)...")
    saved = 0
    failed = 0
    for d in disclosures:
        ok = save_disclosure(sb_url, sb_key, d)
        if ok:
            saved += 1
        else:
            failed += 1

    print(f"  ✓ 저장: {saved}건  ✗ 실패: {failed}건")

    # ── 최종 요약 ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  완료 — 긴급 {len(urgent)}건 알림, 전체 {len(disclosures)}건 DB 저장")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
