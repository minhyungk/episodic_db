# Multi-Agent Dependency Tracking & Backtracking

**상태:** 시뮬레이션 검증 완료 (미래 구현 안)  
**목적:** 멀티에이전트 환경에서 사고 발생 시 dependency(time, agent, file)를 따라 backtracking으로 root cause를 파악  
**타겟 하네스:** Claude Code (subagent, worktree agent 등)  

---

## 1. 동기

Claude Code는 `Agent` tool로 subagent를 spawn하거나 worktree 격리 에이전트를 병렬 실행할 수 있다. 이때:

- Agent-A가 수정한 파일을 Agent-B가 읽으면서 생기는 **간접 의존**
- 한 에이전트의 실패가 다른 에이전트로 전파되는 **cascade failure**
- 동시 수정으로 인한 **conflict**

현재 Episodic DB는 단일 세션 단위로만 분석한다. 멀티에이전트 관계를 추적하면 "왜 테스트가 깨졌는지"를 **에이전트 경계를 넘어** 추적할 수 있다.

---

## 2. 스키마 확장 설계

기존 테이블은 **그대로 유지**. 3개 테이블만 추가한다.

### 2.1 `agents` 테이블

```sql
CREATE TABLE agents (
    agent_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,         -- 기존 sessions 테이블과 연결
    parent_agent_id TEXT,             -- 이 에이전트를 spawn한 부모
    agent_type TEXT,                  -- 'main', 'subagent', 'worktree'
    spawn_reason TEXT,                -- spawn 사유 (프롬프트 요약)
    spawned_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT DEFAULT 'running'     -- 'running', 'completed', 'failed', 'cancelled'
);
```

**기존 sessions와의 관계:** 각 에이전트는 자체 session_id를 가진다 (Claude Code의 각 subagent가 독립 세션). `agents.session_id` → `sessions.session_id` FK로 연결.

### 2.2 `agent_dependencies` 테이블

```sql
CREATE TABLE agent_dependencies (
    dep_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_agent_id TEXT NOT NULL,    -- 의존 관계의 원인
    target_agent_id TEXT NOT NULL,    -- 영향 받는 쪽
    dep_type TEXT NOT NULL,           -- 의존성 유형
    resource_id TEXT,                 -- 관련 자원 (file path 등)
    source_tool_use_id TEXT,          -- 원인 tool_call
    target_tool_use_id TEXT,          -- 영향 받은 tool_call
    created_at TEXT NOT NULL,
    metadata TEXT                     -- 추가 컨텍스트 (JSON)
);
```

**dep_type 종류:**

| dep_type | 의미 | 감지 방법 |
|----------|------|-----------|
| `spawn` | A가 B를 생성 | Agent tool hook |
| `file_handoff` | A가 쓴 파일을 B가 읽음 | edges_touches 교차 비교 |
| `message` | A→B 결과/메시지 전달 | SendMessage hook |
| `conflict` | A와 B가 같은 파일 동시 수정 | edges_touches WROTE 중복 |
| `blocks` | B가 A의 완료를 대기 | 시간적 순서 + 실패 패턴 |

### 2.3 `incidents` 테이블

```sql
CREATE TABLE incidents (
    incident_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,           -- 사고 발생 에이전트
    tool_use_id TEXT,                 -- 사고 시점 tool_call
    incident_type TEXT NOT NULL,      -- 'test_failure', 'conflict', 'loop', 'error'
    description TEXT,
    detected_at TEXT NOT NULL,
    resolved INTEGER DEFAULT 0
);
```

---

## 3. Backtracking 알고리즘

사고 지점에서 3개 축을 따라 역방향 BFS:

```
backtrack_from_incident(incident_id, max_depth=10):
    queue = [(incident.agent_id, incident.tool_use_id, depth=0)]
    visited = set()

    while queue:
        (agent, tool_call, depth) = dequeue()

        # 축 1: Agent dependency
        SELECT FROM agent_dependencies
        WHERE target_agent_id = agent AND target_tool_use_id = tool_call
        → 각 source를 queue에 추가

        # 축 2: File dependency
        이 tool_call이 READ한 file을 찾고,
        그 file을 다른 에이전트가 WROTE한 tool_call을 역추적
        (기존 edges_touches 테이블 활용)

        # 축 3: Time (같은 에이전트 내)
        같은 세션에서 이전 seq이면서 is_wasteful=1인 call 추적
```

**출력:** 인과 체인 (incident → 중간 노드들 → root cause)

---

## 4. 시뮬레이션 결과

### 시나리오 1: File Conflict → Test Failure

**설정:**
```
main-agent → spawn → agent-a (auth/session.py 수정: check_expiry() 시그니처 변경)
                   → agent-b (auth/middleware.py 수정: 이전 시그니처 참조)
                   → agent-c (pytest 실행)
```

**사고:** agent-c의 `test_token_validation` 실패  
`TypeError: check_expiry() got unexpected argument 'strict'`

**Backtracking 결과:**
```
★ [agent-c] Bash: pytest tests/test_auth.py
  at 2026-07-16T22:31:45
  → [file_handoff] → path:auth/session.py (file_handoff)
    at 2026-07-16T22:31:45
  → [file_handoff] → path:auth/middleware.py (file_handoff)
    at 2026-07-16T22:31:45
  → [agent-a] Edit: Edit auth/session.py
    at 2026-07-16T22:31:45
  → [agent-b] Edit: Edit auth/middleware.py
    at 2026-07-16T22:31:45
```

**root cause 정확히 식별:** agent-a의 session.py 시그니처 변경 + agent-b가 변경 전 버전 읽은 conflict.

### 시나리오 2: Cascade Failure (3-hop chain)

**설정:**
```
agent-d: config.py 수정 (PORT 변수 삭제)
agent-e: python src/server.py 시작 실패 (NameError: PORT)
agent-f: curl localhost:8000 → Connection refused
```

**사고:** agent-f의 API 테스트 전면 실패

**Backtracking 결과:**
```
★ [agent-f] Bash: curl http://localhost:8000/api/health
  at 2026-07-16T22:31:45
  → [blocks] →  (blocks)
    at 2026-07-16T22:31:45
  → [agent-e] Bash: python src/server.py
    at 2026-07-16T22:31:45
    → [file_handoff] → path:src/config.py (file_handoff)
      at 2026-07-16T22:31:45
    → [agent-d] Edit: Edit src/config.py
      at 2026-07-16T22:31:45
```

**3-hop cascade 정확히 추적:** agent-f → (blocks) → agent-e → (file_handoff) → agent-d의 config.py 수정.

---

## 5. 기존 DB와의 호환성

| 기존 요소 | 멀티에이전트에서의 활용 |
|-----------|----------------------|
| `tool_calls` | 그대로 사용. 각 agent의 세션별로 기존처럼 기록 |
| `edges_touches` | **핵심 재료.** cross-agent file_handoff 감지에 직접 활용 |
| `results` | 에러 내용으로 incident 자동 감지에 활용 |
| `sessions` | agent별 1개 session. agents 테이블이 session을 에이전트에 매핑 |
| `episodes` | 에이전트별로 기존처럼 조립. 추가로 cross-agent episode도 가능 |
| `proxy_calls` | 에이전트별 token 사용량 추적 그대로 동작 |

**기존 코드에 영향 없음.** 새 3개 테이블은 opt-in으로만 활성화.

---

## 6. Dependency 자동 감지 방법

실제 구현 시 dependency를 어떻게 자동으로 감지할 것인가:

### 6.1 `spawn` — Hook에서 감지

Claude Code의 `Agent` tool 사용 시 PreToolUse hook에서 감지 가능:
```python
if tool_name == "Agent":
    # parent_agent_id = current agent
    # child_agent_id = new subagent's session_id
    insert_dependency(parent, child, "spawn")
```

### 6.2 `file_handoff` — SessionEnd 시 교차 비교

```sql
-- Agent B가 읽은 파일 중, 다른 Agent A가 쓴 파일 찾기
SELECT a_write.tool_use_id as source, b_read.tool_use_id as target, a_write.resource_id
FROM edges_touches a_write
JOIN edges_touches b_read ON a_write.resource_id = b_read.resource_id
JOIN tool_calls tc_a ON a_write.tool_use_id = tc_a.tool_use_id
JOIN tool_calls tc_b ON b_read.tool_use_id = tc_b.tool_use_id
WHERE a_write.mode = 'WROTE' AND b_read.mode = 'READ'
  AND tc_a.session_id != tc_b.session_id   -- 다른 에이전트
  AND a_write.valid_from <= b_read.valid_from  -- 시간 순서 맞음
```

### 6.3 `conflict` — 동일 파일 WROTE 감지

```sql
-- 같은 파일을 두 에이전트가 모두 WROTE
SELECT et1.tool_use_id, et2.tool_use_id, et1.resource_id
FROM edges_touches et1
JOIN edges_touches et2 ON et1.resource_id = et2.resource_id
JOIN tool_calls tc1 ON et1.tool_use_id = tc1.tool_use_id
JOIN tool_calls tc2 ON et2.tool_use_id = tc2.tool_use_id
WHERE et1.mode = 'WROTE' AND et2.mode = 'WROTE'
  AND tc1.session_id != tc2.session_id
```

### 6.4 `blocks` — 실패 패턴 매칭

에이전트 B가 실패했을 때, 그 실패가 에이전트 A의 미완/실패로 인한 것인지:
- B의 에러 메시지에서 A의 산출물(서버, 파일)을 참조하는지 확인
- A의 status가 'failed'이면서 B가 A의 산출물에 의존하는 경우

### 6.5 `message` — SendMessage hook

Claude Code의 `SendMessage` tool 사용 시:
```python
if tool_name == "SendMessage":
    target_agent = tool_input["to"]
    insert_dependency(current_agent, target_agent, "message")
```

---

## 7. 활성화 방법 (On/Off)

기존 코드를 건드리지 않고 환경변수로 토글:

```bash
# 활성화
export EPISODIC_DB_MULTIAGENT=1

# 비활성화 (기본)
unset EPISODIC_DB_MULTIAGENT
```

hook_handler에서:
```python
if os.environ.get("EPISODIC_DB_MULTIAGENT"):
    # agent 등록, dependency 감지 로직 실행
```

---

## 8. Backtracking 질의 인터페이스 (미래)

```bash
# 특정 사고에서 backtrack
episodic-db backtrack --incident inc-001

# 특정 에이전트의 모든 dependency 보기
episodic-db deps --agent agent-a

# 특정 파일을 중심으로 관련 에이전트 그래프
episodic-db deps --file auth/session.py
```

출력 예시:
```
★ [agent-c] Bash: pytest tests/test_auth.py  ← INCIDENT
  │
  ├─ [file_handoff] path:auth/session.py
  │   └─ [agent-a] Edit: Edit auth/session.py  ← ROOT CAUSE
  │
  └─ [file_handoff] path:auth/middleware.py
      └─ [agent-b] Edit: Edit auth/middleware.py
          │
          └─ [conflict] path:auth/session.py ← stale read
              └─ [agent-a] Edit: Edit auth/session.py  ← ROOT CAUSE
```

---

## 9. 실험 코드

시뮬레이션 스크립트: `experiments/multiagent_dependency_sim.py`

실행:
```bash
source .venv/bin/activate
python experiments/multiagent_dependency_sim.py
```

이 스크립트는:
1. 임시 SQLite DB에 기존 스키마 + 확장 테이블 생성
2. 2개 시나리오(file conflict, cascade failure) 시뮬레이션
3. Backtracking 알고리즘 실행
4. 결과 출력 + root cause 식별 여부 확인

**기존 코드에 영향 없음** — 별도 실험 디렉토리에 독립 스크립트.

---

## 10. Forward/Backward 방향별 분석

### 현재 구조 (단일 에이전트)

| 방향 | 동작 | 상태 |
|------|------|------|
| **Backward** | WROTE에서 역추적 (`mark_contributions`) | 잘 동작. 순차 실행이라 인과 명확 |
| **Forward** | NEXT chain 따라가기, WROTE→READ 전파 | 구조는 있으나 전용 API 없음 |

단일 에이전트에서는 tool call이 순차적(seq 0, 1, 2...)이라 시간 순서가 자명하고 양방향 모두 문제없다.

### 멀티에이전트 확장 시 문제점

#### 문제 1: 병렬 실행 → "Should-Have-Waited" Stale Read

```
Agent-A: Read session.py (t=100ms) → Edit session.py (t=200ms)
Agent-B: Read session.py (t=150ms) → Edit middleware.py (t=300ms)
                            ↑
                      B는 A의 Edit(200ms) 전에 읽었으므로 stale
```

현재 `file_handoff` 감지 SQL은 "A가 먼저 쓴 후 B가 읽은" 경우만 잡는다:
```sql
WHERE a_write.valid_from <= b_read.valid_from  -- A wrote → B read
```

**"B가 먼저 읽고 A가 나중에 쓴" stale read는 감지 안 됨.** 이걸 `conflict` dep_type으로 잡아야 한다:

```sql
-- Stale read: B가 READ한 파일을 A가 거의 동시에 WROTE (B의 데이터가 outdated)
SELECT ...
WHERE a_write.resource_id = b_read.resource_id
  AND a_write.mode = 'WROTE' AND b_read.mode = 'READ'
  AND tc_a.session_id != tc_b.session_id
  AND b_read.valid_from < a_write.valid_from   -- B가 먼저 읽음
  AND a_write.valid_from < b_write.valid_from  -- B가 stale 기반으로 씀
```

#### 문제 2: Forward Propagation (영향도 분석)

"Agent-A가 config.py를 방금 깨뜨렸다 — 누가 영향받나?"

이건 forward 질의인데:
- `agent_dependencies`에서 `source_agent_id = A`로 SELECT하면 직접 영향 추적 가능
- **한계:** 에이전트가 아직 실행 중이면 "앞으로 읽을 예정"인 것까지는 예측 못 함
- `edges_touches`에서 `resource_id = 'path:config.py' AND mode = 'READ'`인 다른 세션을 찾는 것으로 현재까지의 영향도는 파악 가능

#### 문제 3: 순환 의존

```
Agent-A writes X → Agent-B reads X, writes Y → Agent-A reads Y → 실패
```

Backtracking: A 실패 → Y 읽음 → B가 Y 씀 → B가 X 읽음 → A가 X 씀 → **다시 A**

현재 시뮬레이션은 `visited` set으로 무한루프를 방지하지만, 인과 체인 자체가 원형이라 단일 "root cause"가 없다.

**대응:** cycle 감지 시 "cycle detected" 마커로 표시하고 cycle 내 모든 노드를 나열. Backtracking 출력에서 명시적으로 표현해야 함.

#### 문제 4: `edges.py`의 `valid_to` cross-session 간섭 버그

현재 코드:
```python
# edges.py line 19-24
if mode == "WROTE":
    conn.execute(
        """UPDATE edges_touches SET valid_to = ?
           WHERE resource_id = ? AND mode = 'READ' AND valid_to IS NULL
           AND tool_use_id != ?""",
        (now, resource_id, tool_use_id),
    )
```

이 쿼리는 **세션을 구분하지 않는다.** Agent-A가 WROTE하면 Agent-B의 열린 READ도 `valid_to`가 닫힌다.

단일 에이전트에서는 한 DB에 한 세션만 활성이므로 문제없었지만, 멀티에이전트에서는 **의도치 않은 side effect**.

**수정 필요:**
```sql
-- 현재 세션의 READ만 닫아야 함
UPDATE edges_touches SET valid_to = ?
WHERE resource_id = ? AND mode = 'READ' AND valid_to IS NULL
  AND tool_use_id != ?
  AND tool_use_id IN (
      SELECT tool_use_id FROM tool_calls WHERE session_id = ?
  )
```

또는 다른 세션의 READ를 닫는 대신 `conflict` dependency를 생성.

### 방향별 종합

| 방향 | 현재 (단일) | 멀티에이전트 | 필요 작업 |
|------|-------------|-------------|-----------|
| **Backward** | 잘 됨 | 대부분 됨 | stale read 감지 추가 |
| **Forward** | 구조 있음, API 없음 | 필요 (영향도) | `deps --from agent-a` 질의 구현 |
| **순환** | 불가능 (순차) | 가능함 | cycle detection + 출력 형식 |
| **valid_to** | 정상 | cross-session 버그 | session 범위 제한 또는 conflict 생성 |

---

## 11. 결론

| 질문 | 답변 |
|------|------|
| 기존 스키마 위에 구현 가능한가? | **가능.** 3개 테이블 추가 + 기존 edges_touches 활용 |
| 기존 기능이 깨지는가? | **아니오.** 완전 opt-in (환경변수 토글) |
| File dependency 감지가 되는가? | **됨.** edges_touches의 READ/WROTE 교차 비교로 자동 감지 |
| Multi-hop backtracking이 되는가? | **됨.** BFS로 3-hop cascade failure까지 추적 검증 완료 |
| Backward 추적은? | **잘 됨.** 단, stale read (should-have-waited) 감지 추가 필요 |
| Forward 추적은? | **가능하나 API 미구현.** agent_dependencies source 기준 SELECT |
| 순환 의존은? | **감지 필요.** visited set + cycle marker 출력 |
| 기존 코드 수정 사항? | `edges.py`의 valid_to 업데이트에 session 범위 제한 필요 |
| 감지 시점은? | SessionEnd (배치) + 실시간 spawn/message (hook) |

### 구현 로드맵 (향후)

1. **Phase 1:** agents 테이블 + spawn dependency (Agent/SendMessage hook 감지)
2. **Phase 2:** file_handoff 자동 감지 (SessionEnd 시 cross-session 교차 비교)
3. **Phase 3:** conflict/blocks + stale read 감지 + `edges.py` valid_to 버그 수정
4. **Phase 4:** Forward propagation 질의 (영향도 분석)
5. **Phase 5:** CLI backtrack/deps 명령 + cycle detection + 시각화
