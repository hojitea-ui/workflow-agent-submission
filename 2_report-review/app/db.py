"""SQLite 저장소. 모든 쓰기는 tx()를 거치며 프로세스 안에서 하나의 잠금으로 직렬화한다."""
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RV_DATA_DIR", BASE_DIR / "data"))
LOCK = threading.RLock()
KST = timezone(timedelta(hours=9))

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  conditions TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  stage TEXT NOT NULL,          -- created / analyzing / findings_ready / revising / done
  version INTEGER NOT NULL,
  findings_json TEXT,           -- 분석 결과(지적 목록)
  choices_json TEXT NOT NULL DEFAULT '{}',  -- 지적 id -> "a" | "b" | "skip" | {"custom": "..."}
  result_json TEXT,             -- 이번 라운드의 수정·재검수 결과
  paragraphs_json TEXT,         -- 이번 라운드를 시작할 때의 문단(분석 때 저장, 라운드가 끝나면 고친 글로 바뀜)
  round INTEGER NOT NULL DEFAULT 1,
  history_json TEXT NOT NULL DEFAULT '[]',  -- 끝난 이전 라운드들: [{round, findings, choices, result}]
  sources_json TEXT NOT NULL DEFAULT '[]',  -- 지금까지 쓴 참고 자료(번호는 라운드를 넘어 이어짐)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL,           -- analyze / revise
  status TEXT NOT NULL,         -- queued / running / succeeded / failed / cancelled / interrupted
  step TEXT,
  message TEXT,
  error_code TEXT,
  error_message TEXT,
  error_next TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "app.db", timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with LOCK:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            have = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}  # 이전 버전 DB에 없는 열을 추가한다
            for col, ddl in (("paragraphs_json", "TEXT"), ("round", "INTEGER NOT NULL DEFAULT 1"),
                             ("history_json", "TEXT NOT NULL DEFAULT '[]'"), ("sources_json", "TEXT NOT NULL DEFAULT '[]'")):
                if col not in have:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
        finally:
            conn.close()


@contextmanager
def tx():
    with LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()


@contextmanager
def read():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()
