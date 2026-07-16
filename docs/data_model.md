# 데이터 모델

Episodic DB의 저장 구조. SQLite(WAL mode) 기반의 그래프 모델.

---

## 개념 모델 (Entity-Relationship)

```
Session 1──N ToolCall N──1 Episode
   │              │
   │              ├── N──N Resource  (edges_touches)
   │              │
   │              └── 1──N Result
   │
   └── 1──N ProxyCall
```

- **Session**: 하나의 Claude Code 세션 (시작 ~ 종료)
- **ToolCall**: 도구 호출 1건 (Read, Edit, Bash 등)
- **Resource**: 접근한 대상 (파일, git ref 등)
- **Result**: 도구 호출의 출력 결과
- **Episode**: 의미 단위로 묶인 ToolCall 그룹 (파생)
- **ProxyCall**: LLM API 호출 1건 (token 캡처용)

---

## 테이블 정의

### sessions

| Field | Type | 설명 |
|-------|------|------|
| `session_id` | TEXT PK | 세션 고유 ID |
| `started_at` | TEXT | 시작 시각 (ISO 8601 UTC) |
| `ended_at` | TEXT | 종료 시각 |
| `success` | INTEGER | 1=성공, 0=실패 (outcome 기반) |
| `total_tokens` | INTEGER | 세션 전체 토큰 (reconciliation 후) |
| `total_cost` | REAL | 세션 전체 비용 ($) |
| `exec_env` | TEXT (JSON) | 실행 환경 스냅샷 |

`exec_env` 예시:
```json
{
  "os": "Darwin 24.6.0",
  "shell": "/bin/zsh",
  "runtimes": {"node": "v20.11.0", "python": "Python 3.12.0"},
  "project_markers": ["pyproject.toml"],
  "git_branch": "main",
  "git_head": "e4f1a02",
  "git_dirty": true,
  "cwd": "/Users/user/project",
  "platform": "darwin"
}
```

---

### tool_calls

| Field | Type | 설명 |
|-------|------|------|
| `tool_use_id` | TEXT PK | Claude가 부여한 고유 ID |
| `session_id` | TEXT FK | 소속 세션 |
| `seq` | INTEGER | 세션 내 호출 순서 (0-based) |
| `timestamp` | TEXT | 호출 시각 |
| `model` | TEXT | 사용된 모델 |
| `tool_name` | TEXT | 도구 이름 (Edit, Write, Bash, Read, Grep 등) |
| `tool_input_json` | TEXT | 도구 입력 전체 JSON (원본) |
| `input_hash` | TEXT | 입력의 SHA256[:16] (중복 감지용) |
| `normalized_input` | TEXT | 정규화된 입력 요약 (100자 제한) |
| `input_tokens` | INTEGER | Input token (reconciliation 후) |
| `output_tokens` | INTEGER | Output token |
| `cache_creation_tokens` | INTEGER | Cache creation token |
| `cache_read_tokens` | INTEGER | Cache read token |
| `own_cost` | REAL | 직접 비용 |
| `carry_cost` | REAL | 누적 컨텍스트 비용 |
| `total_cost` | REAL | own + carry |
| `latency_ms` | REAL | API 응답 지연시간 |
| `contributed_to` | TEXT | `CONTRIBUTED` / `DID_NOT` (라벨링 결과) |
| `is_wasteful` | INTEGER | 1=낭비로 판정됨 |
| `episode_id` | TEXT | 소속 에피소드 ID |

UNIQUE 제약: `(session_id, seq)`

---

### resources

| Field | Type | 설명 |
|-------|------|------|
| `resource_id` | TEXT PK | 리소스 식별자 |
| `kind` | TEXT | `path` / `git_ref` / `test` |
| `first_seen` | TEXT | 최초 접근 시각 |

`resource_id` 형식:
- 파일: `path:/full/path/to/file.py`
- Git: `git_ref:git status...`
- 테스트: `test:test_name`

---

### results

| Field | Type | 설명 |
|-------|------|------|
| `result_id` | INTEGER PK | 자동 증가 ID |
| `tool_use_id` | TEXT FK | 소속 tool_call |
| `result_hash` | TEXT | 결과 내용 SHA256[:16] |
| `digest_handle` | TEXT | Blob 저장소 해시 (대형 출력) |
| `inline_content` | TEXT | 직접 저장된 결과 (4000자 이하) |
| `model_visible_tokens` | INTEGER | 모델에 노출된 토큰 수 |
| `is_error` | INTEGER | 1=에러 결과 |
| `output_chars` | INTEGER | 출력 문자 수 |
| `output_lines` | INTEGER | 출력 줄 수 |

---

### episodes

에피소드는 **파생 데이터**다. SessionEnd 시 tool_calls로부터 조립된다.

상세 필드는 [schema.md](./schema.md) 참조.

핵심 그룹:
- **식별/분류:** episode_id, session_id, waste_type, outcome, converged_by
- **시그니처 facet:** converged_resource, touched_paths, path_prefix, changed_symbols, test_names, grep_terms, error_signature, lang, tool_mix
- **비용:** total_input_tokens ~ carry_ratio
- **분석 메트릭:** read_output_token_ratio ~ futility_score
- **낭비 분석:** is_wasteful, wasted_member_ids, wasted_own_cost, wasted_tokens
- **임베딩:** embedding_text, embedding_model, embedding_dim, embedding(BLOB)

---

### edges_touches

| Field | Type | 설명 |
|-------|------|------|
| `tool_use_id` | TEXT FK | tool_call |
| `resource_id` | TEXT | 접근한 리소스 |
| `mode` | TEXT | `READ` / `WROTE` |
| `valid_from` | TEXT | 유효 시작 시각 |
| `valid_to` | TEXT | 유효 종료 시각 (WROTE 시 이전 READ 닫힘) |

PK: `(tool_use_id, resource_id, mode)`

**Bi-temporal 규칙:** 파일에 WROTE가 발생하면, 해당 파일의 이전 READ 엣지들의 `valid_to`가 닫힌다. (이전에 읽은 내용이 더 이상 유효하지 않음)

---

### edges_duplicate_of

| Field | Type | 설명 |
|-------|------|------|
| `tool_use_id` | TEXT PK | 중복된 호출 |
| `duplicate_of` | TEXT | 원본 호출 |

---

### proxy_calls

| Field | Type | 설명 |
|-------|------|------|
| `call_index` | INTEGER | 세션 내 API call 순서 |
| `session_id` | TEXT | 세션 ID |
| `timestamp` | TEXT | 호출 시각 |
| `model` | TEXT | 모델명 |
| `tool_use_ids` | TEXT (JSON) | 이 turn에서 생성된 tool_use_id 리스트 |
| `user_message` | TEXT | 유저 메시지 (system-reminder 제거) |
| `assistant_text` | TEXT | 어시스턴트 응답 텍스트 |
| `input_tokens` | INTEGER | Input token |
| `output_tokens` | INTEGER | Output token |
| `cache_creation_tokens` | INTEGER | Cache creation token |
| `cache_read_tokens` | INTEGER | Cache read token |
| `total_cost` | REAL | 이 API call 비용 |
| `latency_ms` | REAL | 응답 지연시간 |

PK: `(session_id, call_index)`

---

## 인덱스

| 인덱스 | 대상 | 용도 |
|--------|------|------|
| `idx_tc_session` | `tool_calls(session_id, seq)` | 세션별 tool_call 조회 |
| `idx_tc_tool_name` | `tool_calls(tool_name)` | 도구별 조회 |
| `idx_tc_episode` | `tool_calls(episode_id)` | 에피소드별 멤버 조회 |
| `idx_results_tc` | `results(tool_use_id)` | tool_call별 결과 조회 |
| `idx_results_hash` | `results(result_hash)` | 해시 기반 중복 감지 |
| `idx_ep_session` | `episodes(session_id)` | 세션별 에피소드 조회 |
| `idx_ep_waste_type` | `episodes(waste_type)` | waste_type 필터 |
| `idx_ep_path_prefix` | `episodes(path_prefix)` | path_prefix 필터 |
| `idx_ep_outcome` | `episodes(outcome)` | outcome 필터 |
| `idx_touches_resource` | `edges_touches(resource_id, mode)` | 리소스별 접근 조회 |
| `idx_proxy_calls_tids` | `proxy_calls(session_id)` | 세션별 proxy call 조회 |

---

## SQLite 설정

```sql
PRAGMA journal_mode = WAL;     -- 동시 읽기/쓰기
PRAGMA foreign_keys = ON;      -- FK 제약 활성화
PRAGMA busy_timeout = 5000;    -- 잠금 대기 5초
```

WAL(Write-Ahead Logging) 모드를 사용하여:
- 프록시(쓰기)와 CLI(읽기)가 동시에 DB를 사용 가능
- Hook handler의 빠른 쓰기 보장

---

## 데이터 생명주기

```
실시간 캡처            세션 종료 후              사용자 조회
──────────────        ──────────────────       ────────────
sessions INSERT       sessions UPDATE          SELECT *
tool_calls INSERT     tool_calls UPDATE        episodes SELECT
results INSERT        (tokens, labels,         (facet/vector)
edges_touches INSERT   episode_id)
proxy_calls INSERT    episodes INSERT
                      (assembler 결과)
```

데이터는 한번 쓰이면 UPDATE만 되고 DELETE되지 않는다 (감사 보존 원칙).
