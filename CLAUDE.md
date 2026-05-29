# Sentinel — 시장 감시·예측 자동화 앱

## 프로젝트 개요
주식 시장을 24시간 감시하고 예측 브리핑을 제공하는 자동화 앱.
텔레그램 알림 + Supabase DB 저장 + Next.js 웹 대시보드.

## v1 / v2 계획
- v1 (현재, 8주 프로젝트): 공개용, 수업 공유
  Supabase + Vercel + GitHub Actions(또는 NAS)
  8주 완성 후 해당 repo 업데이트 없음
- v2 (8주 이후, 별도 repo): 개인/가족용
  SQLite + NAS Docker + Tailscale
  Auth.js 가족 4계정 + 포트폴리오 + 자산관리
  Synology Drive로 PC-NAS 자동 동기화

## 기술 스택
- 자동화: Python 3.11
  스케줄러: GitHub Actions self-hosted runner (현재) 또는 Synology 작업 스케줄러 (전환 예정)
- AI:
  - 단순 작업 (감시 알림): gemini-2.5-flash-lite
  - 예측 브리핑 (복잡한 추론): gemini-2.5-pro
  - 공시 요약 (문서 처리): gemini-2.5-flash
- 국내 시세·수급·환율: 키움 REST API (openapi.kiwoom.com)
- 미국 지수·금리·VIX: Alpha Vantage (무료 25회/일)
- 공시: OpenDartReader + OpenDART API
- 글로벌 뉴스: Google News RSS (무료, 키 불필요)
- 국내 뉴스: 네이버 검색 API
- DB: Supabase (PostgreSQL)
- 알림: 텔레그램 Bot
- UI: Next.js 15 + shadcn/ui + Tailwind CSS (5주차~)
- 배포: Vercel (v1) / NAS Docker + Tailscale (v2)

## 핵심 규칙
- 모든 API 키는 .env / GitHub Secrets로만 관리 (코드 하드코딩 절대 금지)
- 관심 종목은 config.yaml로 관리 (추후 Supabase UI로 전환)
- 모든 API 호출에 예외처리 필수 (한 종목 오류가 전체 중단 금지)
- 장 중 시간(09:00~15:30 KST)에만 감시 실행
- 평일(월~금)만 자동화 실행

## 파일 구조
sentinel/
├── .github/
│   └── workflows/
│       └── sentinel.yml       # GitHub Actions cron (현재)
├── collectors/
│   ├── kiwoom.py              # 키움 시세·수급·환율 수집
│   ├── alpha_vantage.py       # 미국 지수·금리·VIX 수집
│   └── news.py                # Google News RSS + 네이버 뉴스
├── analyzers/
│   └── detector.py            # 룰 기반 특이점 판단
├── notifiers/
│   ├── telegram.py            # 텔레그램 발송
│   └── supabase_writer.py     # Supabase 저장
├── ai/
│   └── gemini_client.py       # Gemini API 래퍼
├── config.yaml                # 종목 리스트·임계값
├── .env                       # API 키 (gitignore)
├── main.py                    # 특이점 감시 실행 ✅ 완료
├── main_briefing.py           # 예측 브리핑 실행 (4주차)
├── main_dart.py               # DART 공시 실행 (4주차)
├── requirements.txt
└── CLAUDE.md

## Supabase 테이블 구조
alerts 테이블 (✅ 완료):
  id, created_at, ticker, name, price,
  change_pct, volume_ratio, ai_summary, alert_type

briefings 테이블 (4주차):
  id, created_at, date, content,
  confidence_score, factor_scores(jsonb), risk_level

disclosures 테이블 (4주차):
  id, created_at, ticker, company_name,
  title, disclosure_type, urgency, ai_summary, impact

watchlist 테이블 (5주차 UI 연동 시):
  id, ticker, name, created_at

## 주차별 구현 현황
3주차 ✅: 특이점 감시 알림 완성 (main.py)
4주차 진행중:
  - main_briefing.py: 야간 미국장 → 한국장 예측 브리핑
  - main_dart.py: DART 긴급 공시 모니터링
5주차: Next.js 대시보드 + Vercel 배포
  (거시지표 카드, Fear&Greed 게이지, 뉴스 스트림, 공시 피드)
6주차: 설정·히스토리 화면 + 다크모드 + PWA
7주차: 다듬기 + 시연 준비 + 발표 자료
8주차: 최종 발표

## 기능 2 스펙: 예측 브리핑 (main_briefing.py)
트리거: 매일 06:00 KST
수집 데이터:
  - Alpha Vantage: 나스닥, S&P500, SOX, VIX, DXY,
    코스피200 야간선물, 미국 10년물 국채금리
  - 키움 REST API: USD/KRW 환율
  - Google News RSS: 글로벌 매크로 뉴스 헤드라인
판단 로직:
  - 7개 팩터 각각 긍정/부정/중립 판단
  - 긍정 개수 기반 신뢰도 0~100% 산출
  - 매크로 뉴스 리스크 높으면 신뢰도 -20% 하향
  - 리스크 레벨: 없음/낮음/중간/높음
AI: gemini-2.5-pro (팩터 종합 해석 + 종목별 영향 예측)
출력: 텔레그램 발송 + Supabase briefings 저장

## 기능 3 스펙: DART 공시 모니터링 (main_dart.py)
트리거: 평일 09:00~18:00 매시간 정각
수집: OpenDartReader → 관심 종목 최근 1시간 이내 신규 공시
분류 (룰 기반):
  긴급: 유상증자, 무상증자, 자사주 취득/소각,
        최대주주 변경, 합병, 분할, 대규모 투자(1000억+)
  일반: 그 외 모든 공시
처리:
  긴급만 → gemini-2.5-flash로 3줄 요약 + 영향 분석
  일반 → 저장만, 알림 없음
출력: 긴급만 텔레그램 + 전체 Supabase disclosures 저장

## Next.js 대시보드 화면 구성 (5주차~)
대시보드:
  거시지표 카드 그리드 + 스파크라인
  (코스피·코스닥·나스닥·S&P500·환율·VIX·금리)
  Fear & Greed 게이지
  오늘의 예측 브리핑 카드 (신뢰도 점수 포함)
  최근 특이점 알림 목록
  긴급 공시 피드
  뉴스 스트림 (카테고리별)
설정:
  관심종목 추가/삭제
  알림 임계값 슬라이더
  텔레그램 ON/OFF
히스토리:
  날짜별 예측 브리핑 아카이브
  과거 알림 전체 목록

## 코드 작성 원칙
- 함수 시그니처 명확히, 입출력 타입 주석 상세히
- 나중에 TypeScript 포팅 쉽도록 변수명 영어로 통일
- 모듈 간 의존성 최소화 (각 collector 독립 실행 가능하게)
- v2에서 DB만 교체하면 되도록 DB 접속 코드는 별도 모듈로 분리