"""룰 기반 특이점 감지 — 등락률 ±N% 또는 거래량 Nx 초과 종목 추출."""


def detect_anomalies(stock_data_list: list, thresholds: dict) -> list:
    """감시 조건에 해당하는 종목만 추려서 반환.

    Args:
        stock_data_list: get_stock_data() 결과 리스트 (None 포함 가능)
        thresholds: config.yaml thresholds 섹션

    Returns:
        조건 충족 종목 dict 리스트. 각 항목에 alert_type, alert_reasons 추가.
    """
    price_thr: float = thresholds.get("price_change_pct", 5.0)
    volume_thr: float = thresholds.get("volume_multiplier", 3.0)

    alerts = []
    for data in stock_data_list:
        if data is None:
            continue

        reasons = []
        alert_types = []

        if abs(data["change_pct"]) >= price_thr:
            direction = "급등" if data["change_pct"] > 0 else "급락"
            reasons.append(f"{direction} {data['change_pct']:+.2f}% (기준 ±{price_thr}%)")
            alert_types.append("price")

        if data["volume_ratio"] >= volume_thr:
            reasons.append(
                f"거래량 {data['volume_ratio']:.1f}배 급증 (기준 {volume_thr}배, "
                f"평균 {data['avg_volume']:,}주)"
            )
            alert_types.append("volume")

        if reasons:
            alerts.append(
                {
                    **data,
                    "alert_type": ",".join(alert_types),
                    "alert_reasons": reasons,
                }
            )

    return alerts
