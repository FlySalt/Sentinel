"""Sentinel — 외국인·기관 수급 추적 (평일 16:30 KST).

흐름:
  1. 키움 REST API로 관심 종목별 외국인·기관 순매수 수집 (ka10085)
  2. Supabase flows 테이블에서 최근 20일 데이터 조회 → 연속 일수 계산
  3. 주목 시그널 판단 (룰 기반)
  4. 주목 시그널 종목만 Gemini 2.5 Flash-Lite로 한 줄 해석
  5. 텔레그램 발송 + Supabase flows 저장

주목 시그널 기준:
  - 외국인 3일 이상 연속 순매수
  - 외국인·기관 동시 순매수
  - 방향 전환 (전일 매도 → 오늘 매수)
  - 오늘 순매수 금액이 최근 20일 최대치
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

from sentinel.ai.gemini_client import generate_flow_comment
from sentinel.collectors.kiwoom import get_access_token, get_investor_trend
from sentinel.notifiers.supabase_writer import get_recent_flows, save_flow
from sentinel.notifiers.telegram import send_alert

KST = ZoneInfo("Asia/Seoul")

REQUIRED_ENV = [
    "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET",
    "GOOGLE_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "SUPABASE_URL", "SUPABASE_KEY",
]

# 최소 주목 금액 기준 (억 원 미만은 노이즈)
_MIN_NOTABLE_AMT = 10_000_000_000  # 100억


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


# ── 연속 순매수 일수 계산 ─────────────────────────────────────────────────────

def calc_consecutive_days(history: list[dict], field: str) -> int:
    """최근 데이터 기준 연속 순매수(양수) 또는 순매도(음수) 일수 계산.

    history: [{"date", "foreign_net", "institution_net"}, ...] 최신순
    field  : "foreign_net" | "institution_net"
    Returns: 양수 = 연속 순매수 일수, 음수 = 연속 순매도 일수
    """
    if not history:
        return 0
    today_val = history[0].get(field, 0) or 0
    direction = 1 if today_val >= 0 else -1
    count = 0
    for row in history:
        val = row.get(field, 0) or 0
        if (direction > 0 and val >= 0) or (direction < 0 and val < 0):
            count += 1
        else:
            break
    return direction * count


def detect_direction_change(history: list[dict], field: str) -> bool:
    """전일 대비 방향 전환 여부 감지.

    history[0] = 오늘, history[1] = 전일
    Returns: True if 방향이 바뀌었을 때
    """
    if len(history) < 2:
        return False
    today_val = history[0].get(field, 0) or 0
    prev_val  = history[1].get(field, 0) or 0
    return (today_val >= 0) != (prev_val >= 0)


def is_record_high(history: list[dict], field: str) -> bool:
    """오늘 순매수 금액이 최근 20일 최대치인지 확인 (절댓값 기준)."""
    if not history:
        return False
    today_abs = abs(history[0].get(field, 0) or 0)
    past_max  = max((abs(row.get(field, 0) or 0) for row in history[1:]), default=0)
    return today_abs > 0 and today_abs >= past_max


# ── 주목 시그널 판단 ─────────────────────────────────────────────────────────

def detect_signals(
    today_flow: dict,
    history: list[dict],
) -> list[str]:
    """주목 시그널 목록 반환 (빈 리스트 = 특이사항 없음).

    today_flow: {"foreign_net": int, "institution_net": int}
    history   : DB에서 조회한 최근 20일 데이터 (최신순, 오늘 미포함)
    """
    # history 앞에 오늘 데이터를 붙여서 계산
    full = [today_flow] + (history or [])

    frgn = today_flow.get("foreign_net", 0) or 0
    inst = today_flow.get("institution_net", 0) or 0

    signals: list[str] = []

    # 1. 외국인 3일 이상 연속 순매수
    frgn_consec = calc_consecutive_days(full, "foreign_net")
    if frgn_consec >= 3:
        signals.append(f"외국인 {frgn_consec}일 연속 순매수")
    elif frgn_consec <= -3:
        signals.append(f"외국인 {abs(frgn_consec)}일 연속 순매도")

    # 2. 외국인·기관 동시 순매수 (각각 100억 이상)
    if frgn >= _MIN_NOTABLE_AMT and inst >= _MIN_NOTABLE_AMT:
        signals.append("외국인·기관 동시 순매수")

    # 3. 방향 전환 감지
    if detect_direction_change(full, "foreign_net"):
        direction = "매도→매수" if frgn >= 0 else "매수→매도"
        signals.append(f"외국인 {direction} 전환")

    if detect_direction_change(full, "institution_net"):
        direction = "매도→매수" if inst >= 0 else "매수→매도"
        signals.append(f"기관 {direction} 전환")

    # 4. 최근 20일 최대 순매수
    if is_record_high(full, "foreign_net") and abs(frgn) >= _MIN_NOTABLE_AMT:
        signals.append("외국인 20일 최대 순매수")

    return signals


# ── 텔레그램 메시지 포맷 ─────────────────────────────────────────────────────

def format_flow_message(date_str: str, flow_results: list[dict]) -> str:
    """수급 추적 텔레그램 메시지 포맷.

    flow_results: [{"ticker","name","foreign_net","institution_net",
                    "foreign_consecutive_days","signals","ai_comment"}, ...]
    """
    now_str = datetime.now(KST).strftime("%m/%d")
    lines   = [f"💹 *오늘의 수급* — {now_str}\n"]

    def amt_str(amt: int) -> str:
        sign = "+" if amt >= 0 else ""
        return f"{sign}{amt / 1e8:.0f}억"

    def trend_icon(consec: int) -> str:
        if consec >= 3:  return "📈"
        if consec <= -3: return "📉"
        return ""

    for f in flow_results:
        has_signal = bool(f.get("signals"))
        star       = " ⚡ *주목*" if has_signal else ""
        frgn_consec = f.get("foreign_consecutive_days", 0)

        frgn_note = ""
        if abs(frgn_consec) >= 2:
            direction = "연속 순매수" if frgn_consec > 0 else "연속 순매도"
            frgn_note = f" {trend_icon(frgn_consec)}({abs(frgn_consec)}일 {direction})"

        lines.append(f"*{f['name']}*{star}")
        lines.append(f"  외국인 {amt_str(f['foreign_net'])}{frgn_note}")
        inst = f.get("institution_net", 0)
        inst_str = amt_str(inst) if inst != 0 else "N/A"
        lines.append(f"  기관   {inst_str}")

        if has_signal and f.get("ai_comment"):
            lines.append(f"  → {f['ai_comment']}")
        lines.append("")

    if not flow_results:
        lines.append("수급 데이터 없음")

    return "\n".join(lines).strip()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now_kst  = datetime.now(KST)
    date_str = now_kst.strftime("%Y-%m-%d")

    print("=" * 55)
    print("  Sentinel — 외국인·기관 수급 추적")
    print(f"  실행 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    print("=" * 55)

    if not check_env():
        sys.exit(1)

    config    = load_config()
    watchlist = config.get("watchlist", [])

    # ── STEP 1: 키움 수급 수집 ───────────────────────────────────────────────
    print(f"\n[1/3] 수급 데이터 수집 ({len(watchlist)}개 종목)...")
    token = get_access_token(
        os.environ["KIWOOM_APP_KEY"],
        os.environ["KIWOOM_APP_SECRET"],
    )

    raw_flows: list[dict] = []
    for item in watchlist:
        trend = get_investor_trend(token, item["code"], item["name"], days=20)
        if not trend:
            print(f"  {item['name']}({item['code']}): 수급 데이터 없음")
            continue
        # trend[0] = 가장 최근일 (오늘 또는 직전 거래일)
        today_trend = trend[0]
        frgn = today_trend.get("foreign_net", 0)
        inst = today_trend.get("institution_net", 0)
        raw_flows.append({
            "ticker":          item["code"],
            "name":            item["name"],
            "foreign_net":     frgn,
            "institution_net": inst,
            "_kiwoom_history": trend,  # 연속 일수 계산용
        })
        print(f"  {item['name']:10s}: 외국인 {frgn/1e8:+.0f}억  기관 {inst/1e8:+.0f}억")

    if not raw_flows:
        print("\n수급 데이터 없음. 종료.")
        # 빈 메시지라도 텔레그램 발송 (정상 동작 확인용)
        send_alert(
            os.environ["TELEGRAM_BOT_TOKEN"],
            os.environ["TELEGRAM_CHAT_ID"],
            f"💹 *오늘의 수급* — {now_kst.strftime('%m/%d')}\n수급 데이터를 가져오지 못했습니다.",
        )
        return

    # ── STEP 2: Supabase 히스토리 조회 + 시그널 판단 ────────────────────────
    print("\n[2/3] 연속 일수 계산 + 시그널 감지...")
    flow_results: list[dict] = []

    for f in raw_flows:
        ticker = f["ticker"]
        name   = f["name"]

        # Supabase에서 최근 20일 DB 히스토리 조회
        db_history = get_recent_flows(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
            ticker,
            days=20,
        )

        # 연속 일수 계산 (오늘 + 과거 DB 데이터)
        full = [f] + db_history
        frgn_consec = calc_consecutive_days(full, "foreign_net")
        inst_consec = calc_consecutive_days(full, "institution_net")

        # 시그널 감지
        signals = detect_signals(f, db_history)

        result = {
            "ticker":                        ticker,
            "name":                          name,
            "date":                          date_str,
            "foreign_net":                   f["foreign_net"],
            "institution_net":               f["institution_net"],
            "foreign_consecutive_days":      frgn_consec,
            "institution_consecutive_days":  inst_consec,
            "direction_changed":             detect_direction_change(full, "foreign_net"),
            "signals":                       signals,
            "ai_comment":                    "",
        }

        signal_str = f" → 시그널: {', '.join(signals)}" if signals else ""
        print(f"  {name:10s}: 외국인 {frgn_consec:+d}일 / 기관 {inst_consec:+d}일{signal_str}")
        flow_results.append(result)

    # ── STEP 3: 주목 종목 AI 코멘트 생성 ────────────────────────────────────
    notable = [f for f in flow_results if f["signals"]]
    print(f"\n[3/3] AI 코멘트 생성 ({len(notable)}건 주목 종목)...")

    for f in notable:
        print(f"\n  {f['name']} ({f['ticker']}) — {', '.join(f['signals'])}")
        try:
            comment = generate_flow_comment(
                api_key         = os.environ["GOOGLE_API_KEY"],
                ticker          = f["ticker"],
                name            = f["name"],
                foreign_net     = f["foreign_net"],
                institution_net = f["institution_net"],
                signals         = f["signals"],
            )
            f["ai_comment"] = comment
            print(f"  ✓ {comment[:80]}")
        except Exception as e:
            f["ai_comment"] = ""
            print(f"  ✗ AI 코멘트 실패: {e}")

    # ── 텔레그램 발송 ────────────────────────────────────────────────────────
    msg = format_flow_message(date_str, flow_results)
    ok  = send_alert(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
        msg,
    )
    print(f"\n  텔레그램: {'✓ 발송 완료' if ok else '✗ 발송 실패'}")

    # ── Supabase 저장 ─────────────────────────────────────────────────────────
    print(f"\n  Supabase 저장 ({len(flow_results)}건)...")
    saved = 0
    for f in flow_results:
        ok = save_flow(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
            f,
        )
        if ok:
            saved += 1
    print(f"  ✓ {saved}/{len(flow_results)}건 저장 완료")

    print(f"\n{'=' * 55}")
    print(f"  완료: 주목 {len(notable)}건, 전체 {len(flow_results)}건 저장")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
