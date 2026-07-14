"""SQLite schema DDL for Episodic DB."""

SCHEMA_VERSION = 4

PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
"""

TABLES = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    success INTEGER,
    total_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0.0,
    exec_env TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_use_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    seq INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    model TEXT,
    tool_name TEXT NOT NULL,
    tool_input_json TEXT,
    input_hash TEXT,
    normalized_input TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    own_cost REAL DEFAULT 0.0,
    carry_cost REAL DEFAULT 0.0,
    total_cost REAL DEFAULT 0.0,
    latency_ms REAL,
    contributed_to TEXT,
    is_wasteful INTEGER DEFAULT 0,
    episode_id TEXT,
    UNIQUE(session_id, seq)
);

CREATE TABLE IF NOT EXISTS resources (
    resource_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_use_id TEXT NOT NULL REFERENCES tool_calls(tool_use_id),
    result_hash TEXT,
    digest_handle TEXT,
    inline_content TEXT,
    model_visible_tokens INTEGER DEFAULT 0,
    is_error INTEGER DEFAULT 0,
    output_chars INTEGER DEFAULT 0,
    output_lines INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    created_at TEXT NOT NULL,
    converged_by TEXT,
    waste_type TEXT,
    outcome TEXT,
    converged_resource TEXT,
    touched_paths TEXT,
    path_prefix TEXT,
    changed_symbols TEXT,
    test_names TEXT,
    grep_terms TEXT,
    error_signature TEXT,
    lang TEXT,
    tool_mix TEXT,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_creation INTEGER DEFAULT 0,
    total_cache_read INTEGER DEFAULT 0,
    own_cost REAL DEFAULT 0.0,
    carry_cost REAL DEFAULT 0.0,
    total_cost REAL DEFAULT 0.0,
    carry_ratio REAL DEFAULT 0.0,
    read_output_token_ratio REAL,
    new_information_rate REAL,
    repeated_read_rate REAL,
    futility_score REAL,
    is_wasteful INTEGER DEFAULT 0,
    wasted_member_ids TEXT,
    wasted_own_cost REAL DEFAULT 0.0,
    wasted_carry_cost REAL DEFAULT 0.0,
    wasted_tokens INTEGER DEFAULT 0,
    embedding_text TEXT,
    embedding_model TEXT,
    embedding_dim INTEGER,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS edges_touches (
    tool_use_id TEXT NOT NULL REFERENCES tool_calls(tool_use_id),
    resource_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    PRIMARY KEY (tool_use_id, resource_id, mode)
);

CREATE TABLE IF NOT EXISTS edges_duplicate_of (
    tool_use_id TEXT NOT NULL PRIMARY KEY,
    duplicate_of TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxy_calls (
    call_index INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    timestamp TEXT,
    model TEXT,
    tool_use_ids TEXT,
    user_message TEXT,
    assistant_text TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0.0,
    latency_ms REAL,
    PRIMARY KEY (session_id, call_index)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tc_session ON tool_calls(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_tc_tool_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tc_episode ON tool_calls(episode_id);
CREATE INDEX IF NOT EXISTS idx_results_tc ON results(tool_use_id);
CREATE INDEX IF NOT EXISTS idx_results_hash ON results(result_hash);
CREATE INDEX IF NOT EXISTS idx_ep_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_ep_waste_type ON episodes(waste_type);
CREATE INDEX IF NOT EXISTS idx_ep_path_prefix ON episodes(path_prefix);
CREATE INDEX IF NOT EXISTS idx_ep_outcome ON episodes(outcome);
CREATE INDEX IF NOT EXISTS idx_touches_resource ON edges_touches(resource_id, mode);
CREATE INDEX IF NOT EXISTS idx_proxy_calls_tids ON proxy_calls(session_id);
"""


def get_full_schema() -> str:
    return PRAGMAS + TABLES + INDEXES
