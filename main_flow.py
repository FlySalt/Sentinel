"""Sentinel — 외국인·기관 수급 추적 (평일 16:30 KST).

흐름:
  1. ka10131로 코스피 전체 기관·외국인 연속매매 현황 수집 (1회 호출)
     → 오늘 순매매금액 + 연속일수 + 방향 전환 여부 이미 포함
  2. 관심 종목 필터링 (watchlist 중 ka10131 결과에 없으면 ka10008 폴백)
  3. 주목 시그널 판단 (룰 기반)
  4. 주목 시그널 종목만 Gemini 2.5 Flash-Lite로 한 줄 해석
  5. 텔레그램 발송 + Supabase flows 저장

주목 시그널 기준:
  - 외국인 3일 이상 연속 순매수/순매도
  - 외국인·기관 동시 순매수 (각각 100억 이상)
  - 방향 전환 (연속일수 부호 변경)
  - 오늘 순매수 금액이 최근 연속금액 대비 급등

데이터 단위: ka10131 응답 금액 필드는 백만원(百萬원) 단위
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
from sentinel.collectors.kiwoom import (
    get_access_token,
    get_market_investor_trend,
    get_investor_trend,
    _parse_float,
)
from sentinel.notifiers.supabase_writer import save_flow
from sentinel.notifiers.telegram import send_alert

KST = ZoneInfo("Asia/Seoul")

REQUIRED_ENV = [
    "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET",
    "GOOGLE_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "SUPABASE_URL", "SUPABASE_KEY",
]

# 주목 최소 금액 기준 (백만원 단위 — 100억 = 10,000 백만원)
_MIN_NOTABLE_MN = 10_000   # 100억원


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


# ── ka10131 데이터 파싱 ─────────────────────────────────────────────────────────

def _parse_mn(value) -> int:
    """백만원 단위 문자열 → 정수 변환. '--1234' 같은 이중부호도 처리."""
    s = str(value or "0").strip()
    # '--' 이중 부호 → 음수
    if s.startswith("--"):
        s = "-" + s[2:]
    try:
        return int(float(s.replace(",", "")))
    except (ValueError, TypeError):
        return 0


def parse_market_row(row: dict) -> dict:
    """ka10131 응답 1행 → 정규화된 수급 dict.

    금액 단위: 백만원 → 원으로 변환하여 저장
    """
    def mn_to_won(v) -> int:
        return _parse_mn(v) * 1_000_000   # 백만원 → 원

    frgn_today   = mn_to_won(row.get("frgnr_nettrde_amt", "0"))
    orgn_today   = mn_to_won(row.get("orgn_nettrde_amt",  "0"))
    frgn_consec  = _parse_mn(row.get("frgnr_cont_netprps_dys", "0"))
    orgn_consec  = _parse_mn(row.get("orgn_cont_netprps_dys",  "0"))

    return {
        "ticker":                       row.get("stk_cd", ""),
        "name":                         row.get("stk_nm", ""),
        "foreign_net":                  frgn_today,
        "institution_net":              orgn_today,
        "foreign_consecutive_days":     frgn_consec,
        "institution_consecutive_days": orgn_consec,
        # 방향 전환 = 연속일수가 ±1 (막 바뀐 것)
        "direction_changed": abs(frgn_consec) == 1 and frgn_consec != 0,
    }


# ── 주목 시그널 판단 ─────────────────────────────────────────────────────────────

def detect_signals(flow: dict) -> list[str]:
    """주목 시그널 목록 반환 (빈 리스트 = 특이사항 없음).

    flow: parse_market_row() 결과
    """
    frgn       = flow["foreign_net"]          # 원
    orgn       = flow["institution_net"]      # 원
    frgn_mn    = frgn // 1_000_000            # 백만원
    orgn_mn    = orgn // 1_000_000
    frgn_c     = flow["foreign_consecutive_days"]
    orgn_c     = flow["institution_consecutive_days"]
    dir_chg    = flow["direction_changed"]

    signals: list[str] = []

    # 1. 외국인 3일 이상 연속
    if frgn_c >= 3:
        signals.append(f"외국인 {frgn_c}일 연속 순매수")
    elif frgn_c <= -3:
        signals.append(f"외국인 {abs(frgn_c)}일 연속 순매도")

    # 2. 기관 3일 이상 연속
    if orgn_c >= 3:
        signals.append(f"기관 {orgn_c}일 연속 순매수")
    elif orgn_c <= -3:
        signals.append(f"기관 {abs(orgn_c)}일 연속 순매도")

    # 3. 외국인·기관 동시 순매수 (각각 100억 이상)
    if frgn_mn >= _MIN_NOTABLE_MN and orgn_mn >= _MIN_NOTABLE_MN:
        signals.append("외국인·기관 동시 순매수")

    # 4. 방향 전환 (연속일수 ±1 = 막 전환)
    if dir_chg:
        direction = "매도→매수" if frgn >= 0 else "매수→매도"
        signals.append(f"외국인 {direction} 전환")

    return signals


# ── 텔레그램 메시지 포맷 ─────────────────────────────────────────────────────────

def format_flow_message(date_str: str, flow_results: list[dict]) -> str:
    """수급 추적 텔레그램 메시지 포맷."""
    now_str = datetime.now(KST).strftime("%m/%d")
    lines   = [f"💹 *오늘의 수급* — {now_str}\n"]

    def amt_str(won: int) -> str:
        mn = won // 1_000_000
        sign = "+" if mn >= 0 else ""
        return f"{sign}{mn:,}백만"

    def trend_icon(consec: int) -> str:
        if consec >= 3:  return "📈"
        if consec <= -3: return "📉"
        return ""

    for f in flow_results:
        has_signal = bool(f.get("signals"))
        star       = " ⚡ *주목*" if has_signal else ""
        frgn_c     = f.get("foreign_consecutive_days", 0)
        orgn_c     = f.get("institution_consecutive_days", 0)

        frgn_note = ""
        if abs(frgn_c) >= 2:
            direction = "연속 순매수" if frgn_c > 0 else "연속 순매도"
            frgn_note = f" {trend_icon(frgn_c)}({abs(frgn_c)}일 {direction})"

        orgn_note = ""
        if abs(orgn_c) >= 2:
            direction = "연속 순매수" if orgn_c > 0 else "연속 순매도"
            orgn_note = f" {trend_icon(orgn_c)}({abs(orgn_c)}일 {direction})"

        lines.append(f"*{f['name']}*{star}")
        lines.append(f"  외국인 {amt_str(f['foreign_net'])}{frgn_note}")
        inst = f.get("institution_net", 0)
        inst_str = amt_str(inst) if inst != 0 else "N/A"
        lines.append(f"  기관   {inst_str}{orgn_note}")

        if has_signal and f.get("ai_comment"):
            lines.append(f"  → {f['ai_comment']}")
        lines.append("")

    if not flow_results:
        lines.append("수급 데이터 없음")

    return "\n".join(lines).strip()


# ── 메인 ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    now_kst  = datetime.now(KST)
    date_str = now_kst.strftime("%Y-%m-%d")

    print("=" * 60)
    print("  Sentinel — 외국인·기관 수급 추적 (ka10131)")
    print(f"  실행 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    print("=" * 60)

    if not check_env():
        sys.exit(1)

    config    = load_config()
    watchlist = config.get("watchlist", [])
    watch_map = {item["code"]: item["name"] for item in watchlist}

    # ── STEP 1: 키움 토큰 발급 ──────────────────────────────────────────────────
    token = get_access_token(
        os.environ["KIWOOM_APP_KEY"],
        os.environ["KIWOOM_APP_SECRET"],
    )

    # ── STEP 2: ka10131 코스피 전체 수급 현황 조회 ──────────────────────────────
    print(f"\n[1/3] ka10131 코스피 수급 현황 조회 (관심 {len(watchlist)}개 종목 필터링)...")
    market_map = get_market_investor_trend(token, mrkt_tp="001")
    print(f"  코스피 순매수+순매도 상위 {len(market_map)}건 수신 (최대 200)")
    flow_results: list[dict] = []

    for code, name in watch_map.items():
        if code in market_map:
            flow = parse_market_row(market_map[code])
            flow["date"] = date_str
            print(f"  {name:10s}: 외국인 {flow['foreign_net']//100_000_000:+,}억"
                  f"  기관 {flow['institution_net']//100_000_000:+,}억"
                  f"  (외국인 {flow['foreign_consecutive_days']:+d}일"
                  f" / 기관 {flow['institution_consecutive_days']:+d}일)")
        else:
            # ka10131 상위 100위 밖 → ka10008 폴백 (외국인만)
            print(f"  {name:10s}: ka10131 범위 외 → ka10008 폴백")
            history = get_investor_trend(token, code, name, days=1)
            if history:
                h = history[0]
                flow = {
                    "date":                          date_str,
                    "ticker":                        code,
                    "name":                          name,
                    "foreign_net":                   h["foreign_net"],
                    "institution_net":               0,
                    "foreign_consecutive_days":      1 if h["foreign_net"] >= 0 else -1,
                    "institution_consecutive_days":  0,
                    "direction_changed":             False,
                }
            else:
                flow = {
                    "date": date_str, "ticker": code, "name": name,
                    "foreign_net": 0, "institution_net": 0,
                    "foreign_consecutive_days": 0,
                    "institution_consecutive_days": 0,
                    "direction_changed": False,
                }

        flow["ticker"]   = code
        flow["name"]     = name
        flow["signals"]  = detect_signals(flow)
        flow["ai_comment"] = ""
        flow_results.append(flow)

    # ── STEP 3: 주목 종목 AI 코멘트 생성 ────────────────────────────────────────
    notable = [f for f in flow_results if f["signals"]]
    print(f"\n[2/3] AI 코멘트 생성 ({len(notable)}건 주목 종목)...")

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

    # ── STEP 4: 텔레그램 발송 + Supabase 저장 ───────────────────────────────────
    msg = format_flow_message(date_str, flow_results)
    ok  = send_alert(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
        msg,
    )
    print(f"\n[3/3] 텔레그램: {'✓ 발송 완료' if ok else '✗ 발송 실패'}")

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

    print(f"\n{'=' * 60}")
    print(f"  완료: 주목 {len(notable)}건, 전체 {len(flow_results)}건 저장")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
