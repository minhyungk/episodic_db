"""
Multi-Agent Dependency Tracking & Backtracking Simulation
=========================================================

목적: 현재 Episodic DB 스키마 위에 multi-agent dependency를 추가하고,
      사고 시점에서 backtracking으로 원인을 파악할 수 있는지 검증.

실행: python experiments/multiagent_dependency_sim.py
"""

import json
import sqlite3
import tempfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
# 1. 스키마 확장: 기존 테이블 + multi-agent dependency 테이블
# ─────────────────────────────────────────────────────────────────

MULTIAGENT_SCHEMA_EXTENSION = """
-- 에이전트 레지스트리
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_agent_id TEXT,          -- 부모(spawn한) 에이전트
    agent_type TEXT,               -- 'main', 'subagent', 'worktree'
    spawn_reason TEXT,             -- 왜 생성되었는지 (자연어 요약)
    spawned_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT DEFAULT 'running'  -- 'running', 'completed', 'failed', 'cancelled'
);

-- 에이전트 간 의존성 엣지
CREATE TABLE IF NOT EXISTS agent_dependencies (
    dep_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_agent_id TEXT NOT NULL,   -- 의존 관계의 원인 에이전트
    target_agent_id TEXT NOT NULL,   -- 영향 받는 에이전트
    dep_type TEXT NOT NULL,          -- 의존성 유형 (아래 참조)
    resource_id TEXT,                -- 공유 자원 (파일 등)
    source_tool_use_id TEXT,         -- 원인 tool_call
    target_tool_use_id TEXT,         -- 영향 받은 tool_call
    created_at TEXT NOT NULL,
    metadata TEXT                    -- 추가 컨텍스트 (JSON)
);
-- dep_type 종류:
--   'file_handoff'   : A가 쓴 파일을 B가 읽음
--   'spawn'          : A가 B를 생성함
--   'message'        : A가 B에게 메시지/결과 전달
--   'conflict'       : A와 B가 같은 파일을 동시 수정 시도
--   'blocks'         : A의 완료를 B가 대기

-- 사고(incident) 기록
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,          -- 사고 발생 에이전트
    tool_use_id TEXT,                -- 사고 시점 tool_call
    incident_type TEXT NOT NULL,     -- 'test_failure', 'conflict', 'loop', 'error'
    description TEXT,
    detected_at TEXT NOT NULL,
    resolved INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_deps_source ON agent_dependencies(source_agent_id);
CREATE INDEX IF NOT EXISTS idx_deps_target ON agent_dependencies(target_agent_id);
CREATE INDEX IF NOT EXISTS idx_deps_resource ON agent_dependencies(resource_id);
CREATE INDEX IF NOT EXISTS idx_agents_session ON agents(session_id);
CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_agent_id);
"""

# ─────────────────────────────────────────────────────────────────
# 2. Backtracking 엔진
# ─────────────────────────────────────────────────────────────────

@dataclass
class BacktrackNode:
    agent_id: str
    tool_use_id: str | None
    timestamp: str
    description: str
    depth: int
    dep_type: str | None = None


def backtrack_from_incident(conn: sqlite3.Connection, incident_id: str, max_depth: int = 10) -> list[BacktrackNode]:
    """사고 지점에서 dependency를 따라 역방향 추적.

    추적 축:
    - time: 같은 에이전트 내에서 이전 tool_call (NEXT chain)
    - agent: 다른 에이전트로부터의 dependency edge
    - file: 공유 파일을 통한 간접 의존

    Returns: 인과 체인 (incident → 원인 순서)
    """
    cur = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,))
    incident = cur.fetchone()
    if not incident:
        return []

    chain: list[BacktrackNode] = []
    visited: set[tuple[str, str | None]] = set()
    queue: deque[tuple[str, str | None, int]] = deque()

    # 시작: 사고 에이전트 + tool_call
    start_agent = incident["agent_id"]
    start_tool = incident["tool_use_id"]
    queue.append((start_agent, start_tool, 0))

    while queue:
        agent_id, tool_use_id, depth = queue.popleft()

        if depth > max_depth:
            continue
        if (agent_id, tool_use_id) in visited:
            continue
        visited.add((agent_id, tool_use_id))

        # 현재 노드 정보 수집
        desc = _describe_node(conn, agent_id, tool_use_id)
        node = BacktrackNode(
            agent_id=agent_id,
            tool_use_id=tool_use_id,
            timestamp=_get_timestamp(conn, agent_id, tool_use_id),
            description=desc,
            depth=depth,
        )
        chain.append(node)

        # --- 축 1: Agent dependency (다른 에이전트에서 온 영향) ---
        cur = conn.execute(
            """SELECT * FROM agent_dependencies
               WHERE target_agent_id = ? AND (target_tool_use_id = ? OR target_tool_use_id IS NULL)
               ORDER BY created_at DESC""",
            (agent_id, tool_use_id),
        )
        for dep in cur.fetchall():
            source_node = BacktrackNode(
                agent_id=dep["source_agent_id"],
                tool_use_id=dep["source_tool_use_id"],
                timestamp=dep["created_at"],
                description=f"[{dep['dep_type']}] → {dep['resource_id'] or ''}",
                depth=depth + 1,
                dep_type=dep["dep_type"],
            )
            chain.append(source_node)
            queue.append((dep["source_agent_id"], dep["source_tool_use_id"], depth + 1))

        # --- 축 2: File dependency (같은 파일을 이전에 쓴 에이전트) ---
        if tool_use_id:
            cur = conn.execute(
                """SELECT et.resource_id, et.tool_use_id as writer_tool, tc.session_id
                   FROM edges_touches et
                   JOIN tool_calls tc ON et.tool_use_id = tc.tool_use_id
                   WHERE et.resource_id IN (
                       SELECT resource_id FROM edges_touches WHERE tool_use_id = ? AND mode = 'READ'
                   )
                   AND et.mode = 'WROTE'
                   AND et.tool_use_id != ?
                   ORDER BY et.valid_from DESC
                   LIMIT 5""",
                (tool_use_id, tool_use_id),
            )
            for row in cur.fetchall():
                writer_agent = _get_agent_for_tool_call(conn, row["writer_tool"])
                if writer_agent and writer_agent != agent_id:
                    queue.append((writer_agent, row["writer_tool"], depth + 1))

        # --- 축 3: Time (같은 에이전트 내 이전 tool_call) ---
        if tool_use_id:
            cur = conn.execute(
                """SELECT tool_use_id FROM tool_calls
                   WHERE session_id = (SELECT session_id FROM tool_calls WHERE tool_use_id = ?)
                   AND seq < (SELECT seq FROM tool_calls WHERE tool_use_id = ?)
                   AND is_wasteful = 1
                   ORDER BY seq DESC LIMIT 3""",
                (tool_use_id, tool_use_id),
            )
            for row in cur.fetchall():
                queue.append((agent_id, row["tool_use_id"], depth + 1))

    return chain


def _describe_node(conn, agent_id, tool_use_id):
    if tool_use_id:
        cur = conn.execute(
            "SELECT tool_name, normalized_input FROM tool_calls WHERE tool_use_id = ?",
            (tool_use_id,),
        )
        row = cur.fetchone()
        if row:
            return f"[{agent_id}] {row['tool_name']}: {row['normalized_input'] or ''}"
    return f"[{agent_id}] (spawn/message)"


def _get_timestamp(conn, agent_id, tool_use_id):
    if tool_use_id:
        cur = conn.execute("SELECT timestamp FROM tool_calls WHERE tool_use_id = ?", (tool_use_id,))
        row = cur.fetchone()
        if row:
            return row["timestamp"]
    cur = conn.execute("SELECT spawned_at FROM agents WHERE agent_id = ?", (agent_id,))
    row = cur.fetchone()
    return row["spawned_at"] if row else ""


def _get_agent_for_tool_call(conn, tool_use_id):
    cur = conn.execute(
        """SELECT a.agent_id FROM agents a
           JOIN tool_calls tc ON tc.session_id = a.session_id
           WHERE tc.tool_use_id = ?""",
        (tool_use_id,),
    )
    row = cur.fetchone()
    return row["agent_id"] if row else None


# ─────────────────────────────────────────────────────────────────
# 3. 시뮬레이션 시나리오
# ─────────────────────────────────────────────────────────────────

def setup_db(db_path: Path) -> sqlite3.Connection:
    """기존 episodic_db 스키마 + multiagent 확장 생성."""
    from episodic_db.store.schema import get_full_schema
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(get_full_schema())
    conn.executescript(MULTIAGENT_SCHEMA_EXTENSION)
    conn.commit()
    return conn


def simulate_scenario_1(conn: sqlite3.Connection):
    """시나리오 1: File Conflict로 인한 테스트 실패

    Main agent가 3개 subagent를 spawn하여 병렬 작업:
    - Agent-A: auth/session.py 수정 (token expiry)
    - Agent-B: auth/middleware.py 수정 (validation 로직)
    - Agent-C: tests/test_auth.py 실행

    사고: Agent-C의 테스트가 실패함.
    원인: Agent-A가 session.py의 함수 시그니처를 바꿨는데,
          Agent-B의 middleware.py가 이전 시그니처를 참조.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Sessions (각 agent = 별도 session)
    sessions = [
        ("session-main", now),
        ("session-a", now),
        ("session-b", now),
        ("session-c", now),
    ]
    for sid, ts in sessions:
        conn.execute("INSERT INTO sessions (session_id, started_at) VALUES (?, ?)", (sid, ts))

    # Agents
    agents = [
        ("main-agent", "session-main", None, "main", "orchestrator"),
        ("agent-a", "session-a", "main-agent", "subagent", "fix token expiry in session.py"),
        ("agent-b", "session-b", "main-agent", "subagent", "update validation in middleware.py"),
        ("agent-c", "session-c", "main-agent", "subagent", "run auth tests"),
    ]
    for aid, sid, parent, atype, reason in agents:
        conn.execute(
            "INSERT INTO agents (agent_id, session_id, parent_agent_id, agent_type, spawn_reason, spawned_at, status) VALUES (?,?,?,?,?,?,?)",
            (aid, sid, parent, atype, reason, now, "completed"),
        )

    # Resources
    for rid, kind in [("path:auth/session.py", "path"), ("path:auth/middleware.py", "path"), ("path:tests/test_auth.py", "path")]:
        conn.execute("INSERT OR IGNORE INTO resources (resource_id, kind, first_seen) VALUES (?,?,?)", (rid, kind, now))

    # Tool calls - Agent A: reads then edits session.py
    tool_calls_a = [
        ("tc-a1", "session-a", 0, "Read", "Read auth/session.py", "hash_a1"),
        ("tc-a2", "session-a", 1, "Edit", "Edit auth/session.py", "hash_a2"),
    ]
    for tid, sid, seq, tool, norm, ihash in tool_calls_a:
        conn.execute(
            "INSERT INTO tool_calls (tool_use_id, session_id, seq, timestamp, tool_name, normalized_input, input_hash) VALUES (?,?,?,?,?,?,?)",
            (tid, sid, seq, now, tool, norm, ihash),
        )
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-a1", "path:auth/session.py", "READ", now))
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-a2", "path:auth/session.py", "WROTE", now))

    # Tool calls - Agent B: reads session.py (stale!) then edits middleware.py
    tool_calls_b = [
        ("tc-b1", "session-b", 0, "Read", "Read auth/session.py", "hash_b1"),
        ("tc-b2", "session-b", 1, "Read", "Read auth/middleware.py", "hash_b2"),
        ("tc-b3", "session-b", 2, "Edit", "Edit auth/middleware.py", "hash_b3"),
    ]
    for tid, sid, seq, tool, norm, ihash in tool_calls_b:
        conn.execute(
            "INSERT INTO tool_calls (tool_use_id, session_id, seq, timestamp, tool_name, normalized_input, input_hash) VALUES (?,?,?,?,?,?,?)",
            (tid, sid, seq, now, tool, norm, ihash),
        )
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-b1", "path:auth/session.py", "READ", now))
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-b2", "path:auth/middleware.py", "READ", now))
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-b3", "path:auth/middleware.py", "WROTE", now))

    # Tool calls - Agent C: runs test, fails
    tool_calls_c = [
        ("tc-c1", "session-c", 0, "Bash", "Bash: pytest tests/test_auth.py", "hash_c1"),
    ]
    for tid, sid, seq, tool, norm, ihash in tool_calls_c:
        conn.execute(
            "INSERT INTO tool_calls (tool_use_id, session_id, seq, timestamp, tool_name, normalized_input, input_hash, is_wasteful) VALUES (?,?,?,?,?,?,?,?)",
            (tid, sid, seq, now, tool, norm, ihash, 1),
        )
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-c1", "path:tests/test_auth.py", "READ", now))
    # C reads the output of both session.py and middleware.py (test imports them)
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-c1", "path:auth/session.py", "READ", now))
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-c1", "path:auth/middleware.py", "READ", now))

    # Results for C - error
    conn.execute(
        "INSERT INTO results (tool_use_id, result_hash, inline_content, is_error, output_chars, output_lines) VALUES (?,?,?,?,?,?)",
        ("tc-c1", "err_hash", "FAILED test_auth.py::test_token_validation - TypeError: check_expiry() got unexpected argument 'strict'", 1, 120, 3),
    )

    # Dependencies
    deps = [
        # Main spawned all three
        ("main-agent", "agent-a", "spawn", None, None, None),
        ("main-agent", "agent-b", "spawn", None, None, None),
        ("main-agent", "agent-c", "spawn", None, None, None),
        # A wrote session.py, C read it
        ("agent-a", "agent-c", "file_handoff", "path:auth/session.py", "tc-a2", "tc-c1"),
        # B wrote middleware.py using stale session.py, C read middleware.py
        ("agent-b", "agent-c", "file_handoff", "path:auth/middleware.py", "tc-b3", "tc-c1"),
        # B read session.py (stale version before A's edit)
        ("agent-a", "agent-b", "conflict", "path:auth/session.py", "tc-a2", "tc-b1"),
    ]
    for src, tgt, dtype, res, src_tc, tgt_tc in deps:
        conn.execute(
            "INSERT INTO agent_dependencies (source_agent_id, target_agent_id, dep_type, resource_id, source_tool_use_id, target_tool_use_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (src, tgt, dtype, res, src_tc, tgt_tc, now),
        )

    # Incident
    conn.execute(
        "INSERT INTO incidents (incident_id, agent_id, tool_use_id, incident_type, description, detected_at) VALUES (?,?,?,?,?,?)",
        ("inc-001", "agent-c", "tc-c1", "test_failure",
         "test_token_validation failed: check_expiry() signature mismatch", now),
    )

    conn.commit()
    return "inc-001"


def simulate_scenario_2(conn: sqlite3.Connection):
    """시나리오 2: Cascade Failure (연쇄 실패)

    Agent-D가 config.py를 수정 → Agent-E가 그 config를 import해서 서버 시작 실패 →
    Agent-F가 서버 접근 실패로 API 테스트 전부 fail.

    3-hop dependency chain.
    """
    now = datetime.now(timezone.utc).isoformat()

    for sid in ["session-d", "session-e", "session-f"]:
        conn.execute("INSERT INTO sessions (session_id, started_at) VALUES (?, ?)", (sid, now))

    agents2 = [
        ("agent-d", "session-d", "main-agent", "subagent", "refactor config module"),
        ("agent-e", "session-e", "main-agent", "subagent", "start dev server"),
        ("agent-f", "session-f", "main-agent", "subagent", "run API integration tests"),
    ]
    for aid, sid, parent, atype, reason in agents2:
        conn.execute(
            "INSERT INTO agents (agent_id, session_id, parent_agent_id, agent_type, spawn_reason, spawned_at, status) VALUES (?,?,?,?,?,?,?)",
            (aid, sid, parent, atype, reason, now, "failed"),
        )

    for rid, kind in [("path:src/config.py", "path"), ("path:src/server.py", "path")]:
        conn.execute("INSERT OR IGNORE INTO resources (resource_id, kind, first_seen) VALUES (?,?,?)", (rid, kind, now))

    # D edits config.py (introduces typo in port variable)
    conn.execute(
        "INSERT INTO tool_calls (tool_use_id, session_id, seq, timestamp, tool_name, normalized_input, input_hash) VALUES (?,?,?,?,?,?,?)",
        ("tc-d1", "session-d", 0, now, "Edit", "Edit src/config.py", "hash_d1"),
    )
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-d1", "path:src/config.py", "WROTE", now))

    # E tries to start server (reads config.py), fails
    conn.execute(
        "INSERT INTO tool_calls (tool_use_id, session_id, seq, timestamp, tool_name, normalized_input, input_hash, is_wasteful) VALUES (?,?,?,?,?,?,?,?)",
        ("tc-e1", "session-e", 0, now, "Bash", "Bash: python src/server.py", "hash_e1", 1),
    )
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-e1", "path:src/config.py", "READ", now))
    conn.execute("INSERT INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?,?,?,?)",
                 ("tc-e1", "path:src/server.py", "READ", now))
    conn.execute(
        "INSERT INTO results (tool_use_id, result_hash, inline_content, is_error, output_chars, output_lines) VALUES (?,?,?,?,?,?)",
        ("tc-e1", "err_e1", "NameError: name 'PORT' is not defined (config.py line 12)", 1, 60, 1),
    )

    # F tries to hit server, connection refused
    conn.execute(
        "INSERT INTO tool_calls (tool_use_id, session_id, seq, timestamp, tool_name, normalized_input, input_hash, is_wasteful) VALUES (?,?,?,?,?,?,?,?)",
        ("tc-f1", "session-f", 0, now, "Bash", "Bash: curl http://localhost:8000/api/health", "hash_f1", 1),
    )
    conn.execute(
        "INSERT INTO results (tool_use_id, result_hash, inline_content, is_error, output_chars, output_lines) VALUES (?,?,?,?,?,?)",
        ("tc-f1", "err_f1", "curl: (7) Failed to connect to localhost port 8000: Connection refused", 1, 70, 1),
    )

    # Dependencies: D→E (file_handoff config.py), E→F (blocks - server not running)
    deps2 = [
        ("agent-d", "agent-e", "file_handoff", "path:src/config.py", "tc-d1", "tc-e1"),
        ("agent-e", "agent-f", "blocks", None, "tc-e1", "tc-f1"),
    ]
    for src, tgt, dtype, res, src_tc, tgt_tc in deps2:
        conn.execute(
            "INSERT INTO agent_dependencies (source_agent_id, target_agent_id, dep_type, resource_id, source_tool_use_id, target_tool_use_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (src, tgt, dtype, res, src_tc, tgt_tc, now),
        )

    # Incident at F
    conn.execute(
        "INSERT INTO incidents (incident_id, agent_id, tool_use_id, incident_type, description, detected_at) VALUES (?,?,?,?,?,?)",
        ("inc-002", "agent-f", "tc-f1", "error",
         "Connection refused to localhost:8000 — all API tests failed", now),
    )

    conn.commit()
    return "inc-002"


# ─────────────────────────────────────────────────────────────────
# 4. 실행 + 결과 출력
# ─────────────────────────────────────────────────────────────────

def format_chain(chain: list[BacktrackNode]) -> str:
    lines = []
    for node in chain:
        indent = "  " * node.depth
        dep_marker = f" ({node.dep_type})" if node.dep_type else ""
        lines.append(f"{indent}{'→ ' if node.depth > 0 else '★ '}{node.description}{dep_marker}")
        lines.append(f"{indent}  at {node.timestamp}")
    return "\n".join(lines)


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "multiagent_sim.db"
        conn = setup_db(db_path)

        print("=" * 70)
        print("Multi-Agent Dependency Backtracking Simulation")
        print("=" * 70)

        # --- Scenario 1 ---
        print("\n" + "─" * 70)
        print("SCENARIO 1: File Conflict → Test Failure")
        print("─" * 70)
        print("""
설정:
  main-agent → spawn → agent-a (session.py 수정)
                      → agent-b (middleware.py 수정, session.py 참조)
                      → agent-c (테스트 실행)

사고: agent-c의 test_token_validation 실패
     TypeError: check_expiry() got unexpected argument 'strict'
""")
        inc1 = simulate_scenario_1(conn)
        chain1 = backtrack_from_incident(conn, inc1, max_depth=5)

        print("Backtrack 결과:")
        print(format_chain(chain1))

        print("\n분석:")
        print("  Root Cause: agent-a가 session.py의 check_expiry() 시그니처를 변경했는데,")
        print("  agent-b가 변경 전 버전을 읽고 middleware.py를 작성함.")
        print("  → 'conflict' dependency로 정확히 추적됨.")

        # --- Scenario 2 ---
        print("\n" + "─" * 70)
        print("SCENARIO 2: Cascade Failure (3-hop dependency chain)")
        print("─" * 70)
        print("""
설정:
  agent-d: config.py 수정 (PORT 변수명 실수로 삭제)
  agent-e: server.py 시작 시도 (config.py import → NameError)
  agent-f: API 테스트 실행 (서버 미작동 → connection refused)

사고: agent-f의 curl 실패
""")
        inc2 = simulate_scenario_2(conn)
        chain2 = backtrack_from_incident(conn, inc2, max_depth=5)

        print("Backtrack 결과:")
        print(format_chain(chain2))

        print("\n분석:")
        print("  Root Cause: agent-d가 config.py에서 PORT 변수를 삭제/오타 →")
        print("  agent-e 서버 시작 실패 → agent-f 연결 실패.")
        print("  → 'file_handoff' + 'blocks' dependency chain으로 3-hop 추적 성공.")

        # --- 통계 ---
        print("\n" + "─" * 70)
        print("SUMMARY: 구현 가능성 검증 결과")
        print("─" * 70)

        cur = conn.execute("SELECT COUNT(*) as cnt FROM agent_dependencies")
        dep_count = cur.fetchone()["cnt"]
        cur = conn.execute("SELECT COUNT(*) as cnt FROM agents")
        agent_count = cur.fetchone()["cnt"]

        print(f"  총 에이전트: {agent_count}")
        print(f"  총 dependency: {dep_count}")
        print(f"  시나리오 1 backtrack depth: {max(n.depth for n in chain1)}")
        print(f"  시나리오 2 backtrack depth: {max(n.depth for n in chain2)}")
        print()
        print("  결론: 기존 Episodic DB 스키마(tool_calls, edges_touches, results) 위에")
        print("  agents + agent_dependencies + incidents 3개 테이블만 추가하면")
        print("  multi-agent backtracking이 가능함.")
        print()
        print("  기존 edges_touches의 file READ/WROTE 관계가")
        print("  cross-agent file_handoff 감지의 핵심 재료로 활용됨.")

        conn.close()


if __name__ == "__main__":
    main()
