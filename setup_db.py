"""Supabase alerts 테이블 자동 생성 스크립트.

Supabase 관리 API를 통해 테이블을 생성합니다.
실행: python setup_db.py
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# SUPABASE_URL 에서 프로젝트 ID 추출
# 예: https://lamdbtruvaudfvgknfhd.supabase.co -> lamdbtruvaudfvgknfhd
project_id = SUPABASE_URL.replace("https://", "").split(".")[0]

SQL = """
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

CREATE INDEX IF NOT EXISTS alerts_created_at_idx ON public.alerts (created_at DESC);
ALTER TABLE public.alerts DISABLE ROW LEVEL SECURITY;
"""


def create_table_via_rest() -> bool:
    """Supabase REST API로 테이블 생성 시도."""
    url = f"{SUPABASE_URL}/rest/v1/rpc/exec"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json={"sql": SQL}, timeout=15)
    return resp.status_code < 300


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[오류] SUPABASE_URL / SUPABASE_KEY 환경 변수가 없습니다.")
        sys.exit(1)

    print(f"프로젝트: {project_id}")
    print("alerts 테이블을 생성합니다...\n")

    # Supabase Management API (서비스 롤 키로는 DDL 직접 실행 불가)
    # -> 대시보드에서 수동 실행 안내
    sql_path = Path(__file__).parent / "setup_db.sql"
    print("=" * 55)
    print("  Supabase SQL Editor에서 아래 파일을 실행하세요:")
    print(f"  {sql_path}")
    print()
    print("  접속 경로:")
    print(f"  https://supabase.com/dashboard/project/{project_id}/sql/new")
    print("=" * 55)
    print()
    print("SQL 내용:")
    print(SQL)


if __name__ == "__main__":
    main()
