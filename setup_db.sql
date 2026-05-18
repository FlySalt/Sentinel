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
    ai_summary  text,
    alert_type  text
);

-- 최신순 조회를 위한 인덱스
CREATE INDEX IF NOT EXISTS alerts_created_at_idx ON public.alerts (created_at DESC);

-- RLS 비활성화 (서비스 롤 키로 서버 사이드에서만 접근)
ALTER TABLE public.alerts DISABLE ROW LEVEL SECURITY;
