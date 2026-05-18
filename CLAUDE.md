# Sentinel — 시장 감시·예측 자동화 앱

## 프로젝트 개요
주식 시장을 24시간 감시하고 예측 브리핑을 제공하는 자동화 앱.
텔레그램으로 알림 발송 + Supabase DB 저장 + Next.js 대시보드 표시.

## 기술 스택
- 자동화: Python 3.11 + GitHub Actions
- AI: Google Gemini API
  - 단순 작업: gemini-2.5-flash-lite
  - 예측 브리핑: gemini-2.5-pro
  - 공시 요약: gemini-2.5-flash
- 국내 시세: 키움 REST API (openapi.kiwoom.com)
- 공시: OpenDartReader
- 뉴스: Google News RSS + 네이버 검색 API
- 미국 지수: Alpha Vantage
- DB: Supabase (PostgreSQL)
- 알림: 텔레그램 Bot
- UI: Next.js + shadcn/ui (5주차 구현 예정)
- 배포: Vercel

## 핵심 규칙
- 모든 API 키는 .env 파일로만 관리 (절대 코드에 하드코딩 금지)
- 관심 종목은 config.yaml로 관리 (추후 Supabase UI로 전환)
- 모든 API 호출에 예외처리 필수 (한 종목 오류가 전체 중단 금지)
- 장 중 시간(09:00~15:30)에만 감시 실행

## 파일 구조
sentinel/
├── collectors/
│   ├── kiwoom.py        # 키움 시세·수급·환율 수집
│   ├── alpha_vantage.py # 미국 지수·금리·VIX
│   └── news.py          # Google News RSS + 네이버
├── analyzers/
│   └── detector.py      # 룰 기반 특이점 판단
├── notifiers/
│   ├── telegram.py      # 텔레그램 발송
│   └── supabase_writer.py # Supabase 저장
├── ai/
│   └── gemini_client.py # Gemini API 래퍼
├── config.yaml          # 종목·임계값 설정
├── .env                 # API 키 (gitignore)
├── CLAUDE.md            # 이 파일
└── main.py              # 실행 진입점

## Supabase 테이블 구조
alerts 테이블:
- id, created_at, ticker, name, price, change_pct,
  volume_ratio, ai_summary, alert_type

briefings 테이블:
- id, created_at, date, content, confidence_score,
  factor_scores (jsonb), risk_level

## 현재 진행 상황
- 3주차: 특이점 감시 알림 (기능 1) 구현 중
- 4주차 예정: 예측 브리핑 + DART 공시
- 5주차 예정: Next.js UI