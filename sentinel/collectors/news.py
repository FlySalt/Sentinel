"""Google News RSS — 글로벌 매크로 뉴스 헤드라인 수집.

API 키 불필요. XML 파싱만으로 최신 헤드라인 수집.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

_RSS_URL = "https://news.google.com/rss/search?q=stock+market+economy&hl=ko&gl=KR&ceid=KR:ko"
_TIMEOUT = 10
_MAX_ITEMS = 10

# 리스크 키워드: 뉴스 제목에 포함되면 리스크 요인으로 판단
RISK_KEYWORDS = [
    "tariff", "관세", "trade war", "무역전쟁",
    "recession", "경기침체",
    "rate hike", "금리인상",
    "war", "전쟁", "conflict", "분쟁",
    "sanctions", "제재",
    "inflation", "인플레이션",
    "default", "디폴트",
    "crash", "폭락",
    "shutdown", "셧다운",
    "trump", "트럼프",
    "fed", "연준",
]

POSITIVE_KEYWORDS = [
    "rally", "surge", "bull",
    "rate cut", "금리인하",
    "stimulus", "부양",
    "ceasefire", "휴전",
    "deal", "합의",
    "growth", "성장",
]


def fetch_macro_news(max_items: int = _MAX_ITEMS) -> dict:
    """Google News RSS에서 글로벌 매크로 뉴스를 수집.

    Returns:
    {
      "headlines": ["제목1", "제목2", ...],
      "risk_count": 3,        # 리스크 키워드 포함 건수
      "positive_count": 1,    # 긍정 키워드 포함 건수
      "risk_level": "중간",   # 없음/낮음/중간/높음
      "error": None | str,
    }
    """
    try:
        resp = requests.get(_RSS_URL, timeout=_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        return {
            "headlines": [],
            "risk_count": 0,
            "positive_count": 0,
            "risk_level": "없음",
            "error": str(e),
        }

    headlines: list[str] = []
    for item in root.findall(".//item")[:max_items]:
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            headlines.append(title_el.text.strip())

    risk_count = sum(
        1 for h in headlines
        if any(kw.lower() in h.lower() for kw in RISK_KEYWORDS)
    )
    positive_count = sum(
        1 for h in headlines
        if any(kw.lower() in h.lower() for kw in POSITIVE_KEYWORDS)
    )

    # 리스크 레벨: 리스크 건수 기준
    if risk_count == 0:
        risk_level = "없음"
    elif risk_count <= 2:
        risk_level = "낮음"
    elif risk_count <= 4:
        risk_level = "중간"
    else:
        risk_level = "높음"

    return {
        "headlines": headlines,
        "risk_count": risk_count,
        "positive_count": positive_count,
        "risk_level": risk_level,
        "error": None,
    }
