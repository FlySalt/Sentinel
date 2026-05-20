"""Sentinel 3주차 — 특이점 실시간 감시 알림 (1회 실행).

흐름:
  1. 키움 REST API 토큰 발급
  2. 관심 종목 시세 + 거래량 수집
  3. 룰 기반 특이점 판단 (등락률 ±5% OR 거래량 3배)
  4. Gemini 2.5 Flash-Lite 상황 설명 생성
  5. 텔레그램 발송 + Supabase 저장
"""

import io
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Windows 콘솔 UTF-8 출력 강제
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import find_dotenv, load_dotenv

# .env 탐색: 현재 dir → 상위 dir 순으로 검색 (find_dotenv가 자동 탐색)
load_dotenv(find_dotenv(usecwd=True))

from sentinel.ai.gemini_client import generate_alert_summary
from sentinel.analyzers.detector import detect_anomalies
from sentinel.collectors.kiwoom import get_access_token, get_stock_data
from sentinel.notifiers.supabase_writer import save_alert
from sentinel.notifiers.telegram import format_alert_message, send_alert

# ── 장 시간 체크 ────────────────────────────────────────────────────────────
MARKET_OPEN = (9, 0)
MARKET_CLOSE = (15, 30)


def is_market_open() -> bool:
    # Windows에서 TZ 환경변수가 무시되므로 zoneinfo로 KST 명시
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    # 주말 제외
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


# ── config 로드 ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    """config.yaml을 현재 디렉터리 또는 상위 디렉터리에서 탐색."""
    for search_dir in [Path(__file__).parent, Path(__file__).parent.parent]:
        cfg_path = search_dir / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("config.yaml을 찾을 수 없습니다.")


# ── 환경 변수 검증 ───────────────────────────────────────────────────────────

REQUIRED_ENV = [
    "KIWOOM_APP_KEY",
    "KIWOOM_APP_SECRET",
    "GOOGLE_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "SUPABASE_URL",
    "SUPABASE_KEY",
]


def check_env() -> bool:
    missing = [v for v in REQUIRED_ENV if not os.getenv(v)]
    if missing:
        print(f"[오류] 환경 변수 누락: {missing}")
        return False
    return True


# ── 메인 ────────────────────────────────────────────────────────────────────

def main(skip_market_check: bool = False) -> None:
    print("=" * 50)
    print("  Sentinel — 특이점 감시 알림 시작")
    print(f"  실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 장 시간 체크
    if not skip_market_check and not is_market_open():
        print("[종료] 장 운영 시간(09:00~15:30, 평일)이 아닙니다.")
        return

    if not check_env():
        sys.exit(1)

    config = load_config()
    watchlist: list = config["watchlist"]
    thresholds: dict = config["thresholds"]
    lookback: int = thresholds.get("volume_lookback_days", 5)

    # ── STEP 1: 키움 토큰 발급 ──────────────────────────────────────────────
    print("\n[1/5] 키움 API 토큰 발급...")
    try:
        token = get_access_token(
            os.environ["KIWOOM_APP_KEY"],
            os.environ["KIWOOM_APP_SECRET"],
        )
        print("  ✓ 토큰 발급 완료")
    except Exception as e:
        print(f"  ✗ 토큰 발급 실패: {e}")
        sys.exit(1)

    # ── STEP 2: 관심 종목 시세 수집 ─────────────────────────────────────────
    print(f"\n[2/5] 시세 수집 ({len(watchlist)}개 종목)...")
    stock_data_list = []
    for item in watchlist:
        data = get_stock_data(token, item["code"], item["name"], lookback)
        if data:
            sign = "+" if data["change_pct"] >= 0 else ""
            print(
                f"  {data['name']:10s} {data['price']:>8,}원  "
                f"{sign}{data['change_pct']:.2f}%  "
                f"거래량비율 {data['volume_ratio']:.1f}x"
            )
        stock_data_list.append(data)

    # ── STEP 3: 특이점 판단 ─────────────────────────────────────────────────
    print("\n[3/5] 특이점 판단...")
    alerts = detect_anomalies(stock_data_list, thresholds)
    print(f"  감지된 특이점: {len(alerts)}건")

    if not alerts:
        print("\n현재 조건(등락률 ±5% / 거래량 3배)에 해당하는 종목이 없습니다.")
        print("정상 종료.")
        return

    # ── STEP 4~5: 특이점별 AI 분석 → 알림 ──────────────────────────────────
    for idx, alert in enumerate(alerts, 1):
        print(f"\n{'─' * 45}")
        print(f"  특이점 {idx}/{len(alerts)}: {alert['name']} ({alert['ticker']})")
        for reason in alert["alert_reasons"]:
            print(f"    • {reason}")

        # STEP 4: Gemini 상황 설명 생성
        print("  [4/5] AI 분석 생성 중...")
        try:
            summary = generate_alert_summary(os.environ["GOOGLE_API_KEY"], alert)
            alert["ai_summary"] = summary
            preview = summary[:80].replace("\n", " ")
            print(f"  ✓ {preview}...")
        except Exception as e:
            alert["ai_summary"] = f"AI 분석 생성 실패: {e}"
            print(f"  ✗ AI 분석 실패: {e}")

        # STEP 5a: 텔레그램 발송
        print("  [5a/5] 텔레그램 발송...")
        msg = format_alert_message(alert)
        ok = send_alert(
            os.environ["TELEGRAM_BOT_TOKEN"],
            os.environ["TELEGRAM_CHAT_ID"],
            msg,
        )
        print(f"  {'✓ 발송 완료' if ok else '✗ 발송 실패'}")

        # STEP 5b: Supabase 저장
        print("  [5b/5] Supabase 저장...")
        ok = save_alert(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
            alert,
        )
        print(f"  {'✓ 저장 완료' if ok else '✗ 저장 실패'}")

    print(f"\n{'=' * 50}")
    print(f"  완료: {len(alerts)}건 처리")
    print(f"{'=' * 50}")


def mock_main() -> None:
    """--mock: 키움 API 없이 나머지 파이프라인(Gemini, 텔레그램, Supabase) 검증용."""
    print("[MOCK] 키움 실제 API 대신 가상 데이터 사용")
    load_dotenv(find_dotenv(usecwd=True))
    if not check_env():
        sys.exit(1)

    # 가상 특이점 데이터 (삼성전자 급등 시나리오)
    mock_alerts = [
        {
            "ticker": "005930",
            "name": "삼성전자",
            "price": 72_500,
            "change_pct": 5.83,
            "volume": 42_000_000,
            "avg_volume": 12_000_000,
            "volume_ratio": 3.5,
            "alert_type": "price,volume",
            "alert_reasons": [
                "급등 +5.83% (기준 ±5.0%)",
                "거래량 3.5배 급증 (기준 3.0배, 평균 12,000,000주)",
            ],
        }
    ]

    for alert in mock_alerts:
        print(f"\n--- {alert['name']} ({alert['ticker']}) ---")

        print("[4/5] AI 분석 생성 중...")
        try:
            summary = generate_alert_summary(os.environ["GOOGLE_API_KEY"], alert)
            alert["ai_summary"] = summary
            print(f"  AI: {summary[:100]}...")
        except Exception as e:
            alert["ai_summary"] = f"AI 분석 실패: {e}"
            print(f"  ✗ {e}")

        print("[5a/5] 텔레그램 발송...")
        msg = format_alert_message(alert)
        ok = send_alert(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], msg)
        print(f"  {'✓ 완료' if ok else '✗ 실패'}")

        print("[5b/5] Supabase 저장...")
        ok = save_alert(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"], alert)
        print(f"  {'✓ 완료' if ok else '✗ 실패'}")

    print("\n[MOCK] 완료")


if __name__ == "__main__":
    if "--mock" in sys.argv:
        mock_main()
    else:
        # --force 플래그로 장 시간 체크 우회 (테스트용)
        force = "--force" in sys.argv
        if force:
            print("[주의] --force: 장 시간 체크 우회 모드")
        main(skip_market_check=force)
