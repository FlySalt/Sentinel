"""Sentinel — 장 마감 일일 요약 (평일 15:40 KST).

흐름:
  1. 키움 REST API로 관심 종목 당일 종가·등락률 수집
  2. Yahoo Finance로 코스피·코스닥 지수 종가 수집
  3. Supabase alerts 테이블에서 오늘 발생한 알림 목록 조회
  4. Gemini 2.5 Flash-Lite로 마감 요약 생성
  5. 텔레그램 발송 + Supabase daily_summary 저장
"""

import io
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

from sentinel.ai.gemini_client import generate_close_summary
from sentinel.collectors.kiwoom import get_access_token, get_stock_data, get_index_price
from sentinel.notifiers.supabase_writer import get_today_alerts, save_daily_summary
from sentinel.notifiers.telegram import send_alert

KST = ZoneInfo("Asia/Seoul")

REQUIRED_ENV = [
    "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET",
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


# ── 텔레그램 메시지 포맷 ─────────────────────────────────────────────────────

def format_close_message(
    date_str: str,
    kospi: dict | None,
    kosdaq: dict | None,
    stocks: list[dict],
    alerts_today: list[dict],
    ai_text: str,
) -> str:
    """마감 요약 텔레그램 메시지 포맷."""
    now_str = datetime.now(KST).strftime("%m/%d")

    def idx_str(idx: dict | None) -> str:
        if not idx:
            return "N/A"
        sign = "+" if idx["change_pct"] >= 0 else ""
        return f"{sign}{idx['change_pct']:.2f}%"

    header = (
        f"📊 *오늘의 마감 요약* — {now_str}\n\n"
        f"코스피 {idx_str(kospi)} / 코스닥 {idx_str(kosdaq)}\n"
    )

    # 종목 테이블
    lines = ["\n*관심 종목:*"]
    for s in stocks:
        sign = "+" if s["change_pct"] >= 0 else ""
        name_pad = s["name"].ljust(8)
        lines.append(
            f"`{name_pad}` {sign}{s['change_pct']:.2f}%  {s['price']:,}원"
        )

    # 오늘 특이점
    if alerts_today:
        alert_lines = ["\n*오늘 특이점:*"]
        for a in alerts_today:
            t = a.get("triggered_at", "")[:16]  # YYYY-MM-DD HH:MM
            time_part = t[11:16] if len(t) >= 16 else ""
            alert_lines.append(
                f"  {a.get('name','?')} {a.get('change_pct',0):+.2f}% "
                f"감지 ({time_part})"
            )
        alert_text = "\n".join(alert_lines)
    else:
        alert_text = "\n*오늘 특이점:* 없음"

    return (
        header
        + "\n".join(lines)
        + alert_text
        + f"\n\n🤖 *한 줄 평*\n{ai_text}"
    )


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now_kst  = datetime.now(KST)
    date_str = now_kst.strftime("%Y-%m-%d")

    print("=" * 55)
    print("  Sentinel — 장 마감 일일 요약")
    print(f"  실행 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    print("=" * 55)

    if not check_env():
        sys.exit(1)

    config    = load_config()
    watchlist = config.get("watchlist", [])

    # ── STEP 1: 키움 종목 시세 수집 ─────────────────────────────────────────
    print(f"\n[1/4] 관심 종목 종가 수집 ({len(watchlist)}개)...")
    token = get_access_token(
        os.environ["KIWOOM_APP_KEY"],
        os.environ["KIWOOM_APP_SECRET"],
    )

    stocks: list[dict] = []
    for item in watchlist:
        data = get_stock_data(token, item["code"], item["name"])
        if data:
            stocks.append(data)
            sign = "+" if data["change_pct"] >= 0 else ""
            print(f"  {item['name']:10s}: {sign}{data['change_pct']:.2f}%  {data['price']:,}원")

    if not stocks:
        print("  ✗ 종목 시세 수집 실패")

    # ── STEP 2: 코스피·코스닥 지수 ──────────────────────────────────────────
    print("\n[2/4] 코스피·코스닥 지수 수집...")
    kospi  = get_index_price("KOSPI")
    kosdaq = get_index_price("KOSDAQ")

    for name, idx in [("코스피", kospi), ("코스닥", kosdaq)]:
        if idx:
            print(f"  {name}: {idx['change_pct']:+.2f}%  {idx['price']:,.2f}")
        else:
            print(f"  {name}: 수집 실패")

    # ── STEP 3: 오늘 알림 조회 ──────────────────────────────────────────────
    print("\n[3/4] 오늘 특이점 알림 조회...")
    alerts_today = get_today_alerts(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )
    print(f"  오늘 알림: {len(alerts_today)}건")
    for a in alerts_today:
        print(f"    • {a.get('name','?')} {a.get('change_pct',0):+.2f}% "
              f"({a.get('triggered_at','')[:16]})")

    # ── STEP 4: Gemini 마감 요약 생성 ───────────────────────────────────────
    print("\n[4/4] Gemini 마감 요약 생성 (flash-lite)...")
    try:
        ai_text = generate_close_summary(
            api_key     = os.environ["GOOGLE_API_KEY"],
            date_str    = date_str,
            kospi       = kospi,
            kosdaq      = kosdaq,
            stocks      = stocks,
            alerts_today= alerts_today,
        )
        print(f"  ✓ AI 요약 완료")
        print(f"  미리보기: {ai_text[:100].replace(chr(10), ' ')}...")
    except Exception as e:
        ai_text = f"AI 요약 생성 실패: {e}"
        print(f"  ✗ {e}")

    # ── 텔레그램 발송 ────────────────────────────────────────────────────────
    msg = format_close_message(date_str, kospi, kosdaq, stocks, alerts_today, ai_text)
    ok  = send_alert(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
        msg,
    )
    print(f"\n  텔레그램: {'✓ 발송 완료' if ok else '✗ 발송 실패'}")

    # ── Supabase 저장 ─────────────────────────────────────────────────────────
    stock_data_json = {
        s["ticker"]: {
            "name":       s["name"],
            "price":      s["price"],
            "change_pct": s["change_pct"],
        }
        for s in stocks
    }
    if kospi:
        stock_data_json["_KOSPI"]  = {"change_pct": kospi["change_pct"],  "price": kospi["price"]}
    if kosdaq:
        stock_data_json["_KOSDAQ"] = {"change_pct": kosdaq["change_pct"], "price": kosdaq["price"]}

    market_summary = ""
    if kospi and kosdaq:
        market_summary = f"코스피 {kospi['change_pct']:+.2f}% / 코스닥 {kosdaq['change_pct']:+.2f}%"

    ok = save_daily_summary(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
        {
            "date":           date_str,
            "market_summary": market_summary,
            "stock_data":     stock_data_json,
            "alerts_count":   len(alerts_today),
            "ai_summary":     ai_text,
        },
    )
    print(f"  Supabase: {'✓ 저장 완료' if ok else '✗ 저장 실패'}")

    print(f"\n{'=' * 55}")
    print("  장 마감 요약 완료")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
