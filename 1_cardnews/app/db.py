"""SQLite 저장소. 모든 쓰기는 tx()를 거치며 프로세스 안에서 하나의 잠금으로 직렬화한다."""
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CARDNEWS_DATA_DIR", BASE_DIR / "data"))
LOCK = threading.RLock()
KST = timezone(timedelta(hours=9))

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  stage TEXT NOT NULL,
  version INTEGER NOT NULL,
  engine TEXT NOT NULL,
  mock INTEGER NOT NULL,
  window_days INTEGER NOT NULL,
  window_basis TEXT NOT NULL,
  selection_json TEXT NOT NULL DEFAULT '[]',
  selection_version INTEGER,
  candidates_note TEXT,
  notes_json TEXT,
  proposal_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  step TEXT,
  message TEXT,
  error_code TEXT,
  error_message TEXT,
  error_next TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  question TEXT NOT NULL,
  options_json TEXT NOT NULL,
  multi_select INTEGER NOT NULL,
  allow_free_text INTEGER NOT NULL,
  recommendation_json TEXT,
  status TEXT NOT NULL,
  answer_json TEXT,
  answered_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  ord INTEGER NOT NULL,
  data_json TEXT NOT NULL,
  PRIMARY KEY (project_id, id)
);
CREATE TABLE IF NOT EXISTS storyboards (
  project_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  format TEXT NOT NULL,
  hook TEXT,
  instruction TEXT,
  style_json TEXT NOT NULL,
  cards_json TEXT NOT NULL,
  run_id TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (project_id, version)
);
CREATE TABLE IF NOT EXISTS cards (
  project_id TEXT NOT NULL,
  card_id TEXT NOT NULL,
  storyboard_version INTEGER NOT NULL,
  ord INTEGER NOT NULL,
  status TEXT NOT NULL,
  error_code TEXT,
  error_message TEXT,
  error_next TEXT,
  current_version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (project_id, card_id)
);
CREATE TABLE IF NOT EXISTS card_versions (
  project_id TEXT NOT NULL,
  card_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  tool TEXT NOT NULL,
  prompt TEXT NOT NULL,
  file_rel TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  checks_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (project_id, card_id, version)
);
CREATE TABLE IF NOT EXISTS requests (
  project_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  response_json TEXT NOT NULL,
  PRIMARY KEY (project_id, request_id)
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
            # 이전 버전의 DB에는 없는 열을 추가한다(2단계 → 3단계).
            have = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
            for col in ("engine", "engine_session_id", "params_json"):
                if col not in have:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {col} TEXT")
            # 3단계 → 4단계: 스토리보드 포인터와 승인 여부(기존 데이터는 그대로 두고 열만 추가한다)
            pcols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
            if "storyboard_version" not in pcols:
                conn.execute("ALTER TABLE projects ADD COLUMN storyboard_version INTEGER")
            if "storyboard_approved" not in pcols:
                conn.execute("ALTER TABLE projects ADD COLUMN storyboard_approved INTEGER NOT NULL DEFAULT 0")
            # 최종 승인: 승인한 순간의 합성본 서명(카드를 고치면 서명이 달라져 승인이 풀린다)
            for col in ("final_approved_sig", "final_approved_at"):
                if col not in pcols:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT")
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
