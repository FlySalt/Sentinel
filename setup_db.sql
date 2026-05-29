-- Sentinel alerts 테이블 생성
-- Supabase 대시보드 > SQL Editor에서 실행하세요.

CREATE TABLE IF NOT EXISTS public.alerts (
    id          uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at  timestamptz DEFAULT now(),
    ticker      text        NOT NULL,
    name        text        NOT NULL,
    price       integer     NOT NULL,
    change_pct  numeric(8, 2) NOT NULL,
    volume_ratio numeric(8, 2) NOT NULL,
    ai_summary   text,
    alert_type   text,
    triggered_at text        -- KST 시각 문자열 (예: "2026-05-21 14:43:00 KST")
);

-- 최신순 조회를 위한 인덱스
CREATE INDEX IF NOT EXISTS alerts_created_at_idx ON public.alerts (created_at DESC);

-- RLS 비활성화 (서비스 롤 키로 서버 사이드에서만 접근)
ALTER TABLE public.alerts DISABLE ROW LEVEL SECURITY;

-- ── briefings 테이블 (예측 브리핑) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.briefings (
    id               uuid          DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at       timestamptz   DEFAULT now(),
    date             text          NOT NULL,       -- YYYY-MM-DD
    content          text,                         -- AI 브리핑 본문
    confidence_score integer,                      -- 신뢰도 0~100
    risk_level       text,                         -- 없음/낮음/중간/높음
    factor_scores    jsonb                          -- 팩터별 값·판단
);

CREATE INDEX IF NOT EXISTS briefings_date_idx ON public.briefings (date DESC);
ALTER TABLE public.briefings DISABLE ROW LEVEL SECURITY;

-- ── disclosures 테이블 (DART 공시) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.disclosures (
    id               uuid          DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at       timestamptz   DEFAULT now(),
    ticker           text          NOT NULL,
    company_name     text          NOT NULL,
    title            text          NOT NULL,
    disclosure_type  text,
    urgency          text          DEFAULT '일반',  -- 긴급/일반
    ai_summary       text,
    impact           text                           -- 긍정/중립/부정
);

CREATE INDEX IF NOT EXISTS disclosures_created_at_idx ON public.disclosures (created_at DESC);
CREATE INDEX IF NOT EXISTS disclosures_ticker_idx     ON public.disclosures (ticker);
ALTER TABLE public.disclosures DISABLE ROW LEVEL SECURITY;

-- ── daily_summary 테이블 (장 마감 일일 요약) ──────────────────────────────
CREATE TABLE IF NOT EXISTS public.daily_summary (
    id              uuid          DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at      timestamptz   DEFAULT now(),
    date            text          NOT NULL,          -- YYYY-MM-DD
    market_summary  text,                            -- "코스피 +0.8% / 코스닥 -0.3%"
    stock_data      jsonb,                           -- 종목별 종가·등락률
    alerts_count    integer       DEFAULT 0,         -- 오늘 발생한 특이점 수
    ai_summary      text                             -- Gemini 마감 요약
);

CREATE INDEX IF NOT EXISTS daily_summary_date_idx ON public.daily_summary (date DESC);
ALTER TABLE public.daily_summary DISABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON public.daily_summary TO service_role;

-- ── flows 테이블 (외국인·기관 수급) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.flows (
    id                              uuid          DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at                      timestamptz   DEFAULT now(),
    date                            text          NOT NULL,    -- YYYY-MM-DD
    ticker                          text          NOT NULL,
    name                            text          NOT NULL,
    foreign_net                     bigint        DEFAULT 0,   -- 외국인 순매수 (원)
    institution_net                 bigint        DEFAULT 0,   -- 기관 순매수 (원)
    foreign_consecutive_days        integer       DEFAULT 0,   -- 양수=연속매수, 음수=연속매도
    institution_consecutive_days    integer       DEFAULT 0,
    direction_changed               boolean       DEFAULT false,
    ai_comment                      text
);

CREATE INDEX IF NOT EXISTS flows_date_idx   ON public.flows (date DESC);
CREATE INDEX IF NOT EXISTS flows_ticker_idx ON public.flows (ticker, date DESC);
ALTER TABLE public.flows DISABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON public.flows TO service_role;
