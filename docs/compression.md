# Trajectory Compression

에이전트 세션 로그(trajectory)를 sLLM의 고정 context window에 맞춰 압축하는 모듈.

## 목적

Episodic DB에 저장된 세션 로그를 sLLM 학습 데이터로 주입할 때, context window(예: 32k tokens)를 초과하는 긴 세션을 결정론적으로 압축한다.

**핵심 제약:**
- LLM 개입 없이 결정론적(deterministic)으로 동작
- 동일 입력 → 항상 동일 출력 (재현 가능)
- 추가 API 비용 0

**설계 원칙:** 낭비/실패 과정이 가장 중요한 학습 재료. 중간 과정을 최대한 보존하되, 학습 가치가 낮은 노이즈부터 먼저 압축한다.

**사용 시점:** DB에서 데이터를 로드한 후, sLLM context window에 주입하기 직전 (query time)

## 한 줄 요약

1. **tool_result 내용을 truncate** (결과 텍스트만 100자로 자름, 메시지 구조는 유지)
2. 이걸로 budget에 안 맞으면 → **DB의 `futility_score`, `waste_type`, `is_wasteful` 등을 기반으로 점수를 매겨서 학습 가치가 낮은 순서대로 skeletonize 및 제거**

## 용어: Skeletonize란?

메시지를 삭제하는 게 아니라 **뼈대만 남기는 것.**

```
원본:
  assistant: [tool_use: Bash, command: "find /repo -name '*.py' -exec grep -l 'def power' {} \;"]
  user:      [tool_result: "sympy/printing/pretty.py\nsympy/core/power.py\n..." (2000 chars)]

skeleton 처리 후:
  assistant: [Bash] find /repo -name '*.py' -exec grep -l 'def power' {}
  user:      [result: 2000 chars]
```

- **남기는 것:** 어떤 tool을 어떤 인자로 불렀는지 (행동의 뼈대)
- **잘라내는 것:** 결과물 내용 (대부분 긴 텍스트 출력)

메시지 구조(순서, role)는 유지되므로 sLLM은 대화 흐름을 읽을 수 있지만, 실제 결과 내용은 사라진다. 이게 "삭제"와의 차이: skeleton은 "여기서 find를 실행했다"는 맥락을 남기고, 삭제는 그 메시지가 존재했다는 것 자체를 없앤다.

## Scoring: 메시지별 학습 가치 판정

모든 메시지에 0~5 점의 score를 부여. 점수가 높을수록 학습 가치가 높아 마지막까지 보존된다.

### DB-aware scoring (conn 제공 시)

에피소드/tool_call 테이블의 pre-computed 라벨 사용:

| Score | 조건 | 의미 |
|-------|------|------|
| 5 | `is_wasteful=1` + waste_type이 `expensive-failure` 또는 `repeated-loop` | 비용 높은 삽질 (핵심 학습 재료) |
| 4 | `is_wasteful=1` (기타 waste episode) | 낭비로 판정된 call |
| 3 | `is_wasteful=0`이지만 해당 episode의 `waste_type != productive` | 낭비 에피소드의 맥락 call |
| 2 | productive episode의 verification call | 정상 검증 |
| 1 | productive exploration | 일반 탐색 |
| 0 | noise | 불필요한 탐색 |

### Keyword fallback scoring (conn 없을 때)

DB 연결 없이도 동작. 순수 pattern matching:

| Score | 판정 규칙 | 예시 |
|-------|-----------|------|
| 4 | assistant text에 실패 키워드 (`wrong`, `the problem is`, `incorrect`, `mistake`, ...) | "The problem is that str() loses structure" |
| 3 | tool_result에 `is_error=True` 또는 에러 키워드 (`traceback`, `error`, `failed`, ...) | "Traceback: NameError..." |
| 2 | Bash command에 `python`, `pytest`, `test` 포함 | `python test_fix.py` |
| 1 | 위 어디에도 안 걸리는 나머지 | `grep -n "def foo"` |
| 0 | command가 noise 패턴으로 시작 (`git log`, `git show`, `find `, `ls `, `cat `, ...) | `git log --oneline | head` |

## 전체 흐름

```
                    ┌─────────────────────────┐
                    │  DB에서 messages 로드     │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Scoring (메시지별 점수)   │
                    │                         │
                    │  conn 있음?              │
                    │  ├─ Y: DB labels 사용    │
                    │  │   (is_wasteful,       │
                    │  │    waste_type)        │
                    │  └─ N: keyword fallback  │
                    │       (pattern matching) │
                    └────────────┬────────────┘
                                 │
                                 ▼
              ┌──────────────────────────────────────┐
              │  Pass 1: tool_result truncation       │
              │  (모든 result → 100 chars + marker)   │
              └──────────────────┬───────────────────┘
                                 │
                          ┌──────▼──────┐
                          │ budget 이내? │
                          └──┬───────┬──┘
                           Y │       │ N
                 ┌───────────▼┐      │
                 │  RETURN    │      │
                 └────────────┘      ▼
              ┌──────────────────────────────────────┐
              │  Pass 2: value-based compression      │
              │                                      │
              │  for cut_below in 1, 2, 3, 4, 5:     │
              │    score < cut_below → skeleton 처리  │
              │    score >= cut_below → 원문 유지     │
              │    Edit → 항상 원문 유지              │
              │                                      │
              │    매 단계에서 budget 체크 → 맞으면   │
              │    RETURN                            │
              └──────────────────┬───────────────────┘
                                 │ 여전히 over
                                 ▼
              ┌──────────────────────────────────────┐
              │  Pass 3: 전부 tier-1 skeleton         │
              │  (Edit 제외 모든 메시지 최소 압축)     │
              └──────────────────┬───────────────────┘
                                 │
                          ┌──────▼──────┐
                          │ budget 이내? │
                          └──┬───────┬──┘
                           Y │       │ N
                 ┌───────────▼┐      │
                 │  RETURN    │      ▼
                 └────────────┘
              ┌──────────────────────────────────────┐
              │  Pass 4: score 0-1 완전 제거          │
              │  (score 2+ 만 skeleton으로 유지)      │
              └──────────────────┬───────────────────┘
                                 │
                          ┌──────▼──────┐
                          │ budget 이내? │
                          └──┬───────┬──┘
                           Y │       │ N
                 ┌───────────▼┐      │
                 │  RETURN    │      ▼
                 └────────────┘
              ┌──────────────────────────────────────┐
              │  Pass 5: 최소 보존                    │
              │  Edit + score 4+ (핵심 실패) 만 남김  │
              │  → RETURN                            │
              └──────────────────────────────────────┘
```

**압축 강도 순서:**

```
약함                                                          강함
├────────────┼──────────────┼────────────┼──────────────┼──────────┤
Pass 1       Pass 2          Pass 3       Pass 4          Pass 5
result만     noise skeleton  전부 skeleton noise 제거      Edit+실패만
truncate     (구조 유지)     (구조 유지)   (구조 축소)     (최소한)

보존:  ████████████████████████████████████████████████░░░░░░░░░░
score5 ████████████████████████████████████████████████████████████
score4 ████████████████████████████████████████████████████████████
score3 █████████████████████████████████████████████░░░░░░░░░░░░░░
score2 ████████████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░
score1 █████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
score0 ██████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
```

## 압축 전략

### Pass 1: tool_result truncation

모든 tool_result를 100 chars로 자름. 메시지 구조는 유지.

```
tool_result 원본: "1  import sys\n2  import os\n..." (5000 chars)
         압축후: "1  import sys\n2  import os\n[TRUNCATED original_chars=5000]"
```

보존 예외: Edit 결과, is_error=True 결과

**맞으면 여기서 종료.** 대부분의 32k budget 세션은 이것만으로 충분.

### Pass 2: Value-based selective compression

score 기준으로 낮은 것부터 단계적으로 압축. 매 단계에서 budget 체크:

```
cut_below=1: score 0 만 skeleton 처리
cut_below=2: score 0-1 skeleton 처리
cut_below=3: score 0-2 skeleton 처리
...
```

**Tier별 skeleton 처리 수준:**

| Tier | assistant text | tool_use | tool_result |
|------|---------------|----------|-------------|
| 0 | 제거 | `[Bash] cmd[:80]` | 제거 |
| 1 | 80 chars (실패 추론은 300 chars) | `[Bash] cmd[:80]` | `[result: N chars]` |
| 2 | 80 chars (실패 추론은 300 chars) | `[Bash] cmd[:200]` | `[result] content[:200]` |
| 3 | 80 chars (실패 추론은 300 chars) | `[Bash] cmd[:200]` | `[FAILURE] content[:500]` (에러만) |
| 4+ | 500 chars | `[Bash] cmd[:200]` | `[FAILURE] content[:500]` (에러만) |

### Pass 3: 전부 tier 1 이하로 skeleton

모든 메시지를 최소 skeleton으로 (Edit 제외).

### Pass 4: 낮은 score 메시지 완전 제거

score 0-1 제거, score 2+ 만 skeleton으로 유지.

### Pass 5: 최소 보존

Edit + score 4+ (핵심 실패 추론)만 남김.

## 실제 압축 결과

`session_swebench-sympy__sympy-20639` (179 messages, ~44k tokens):

| Budget | Tokens | 압축률 | 메시지 수 | 압축된 메시지 |
|--------|--------|--------|-----------|-------------|
| 32k | 21,907 | 50% | 179 (전부) | 0 (Pass 1만) |
| 16k | 13,831 | 32% | 179 (전부) | 143 |
| 12k | 9,678 | 22% | 179 (전부) | 64 |
| 8k | 3,383 | 8% | 31 | 149 |

**16k에서도 모든 179개 메시지가 남아있음** — 노이즈만 skeleton 처리되고 실패 추론 19개가 full text로 보존.

## 정보 보존 우선순위

높음 → 낮음 (마지막까지 남는 순서):

1. **낭비 에피소드의 핵심 call** (score 5) — expensive-failure, repeated-loop
2. **실패 추론 + 코드 수정** (score 4) — "the problem is...", Edit
3. **낭비 에피소드 맥락** (score 3) — 왜 그 방향으로 갔는지
4. **검증 시도** (score 2) — python/pytest 실행
5. **일반 탐색** (score 1) — grep, read
6. **노이즈** (score 0) — git log, ls, find → 가장 먼저 압축

## Integration Guide

### Input 형식

`messages: list[dict]` — Claude API Messages 형식 그대로.

```json
[
  {
    "role": "user",
    "content": [
      {"type": "text", "text": "Fix the bug in power.py"}
    ]
  },
  {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "I'll look at the printing code."},
      {"type": "tool_use", "id": "toolu_abc123", "name": "Bash",
       "input": {"command": "grep -n '_print_Pow' sympy/printing/pretty.py"}}
    ]
  },
  {
    "role": "user",
    "content": [
      {"type": "tool_result", "tool_use_id": "toolu_abc123",
       "content": "1814:    def _print_Pow(self, e):\n1815:        ...(2000 chars)",
       "is_error": false}
    ]
  }
]
```

proxy_calls 테이블이나 세션 로그 파일에서 로드한 conversation messages를 그대로 넣으면 됨.

### Output 형식

`CompressedResult` dataclass:

```python
@dataclass
class CompressedResult:
    original_chars: int          # 원본 크기
    compressed_chars: int        # 압축 후 크기
    compressed_messages: list[dict]  # 압축된 메시지 (같은 dict 형식)
    dropped_middle: int          # skeleton/제거된 메시지 수

    @property
    def ratio(self) -> float:    # compressed / original (0.0~1.0)
```

`compressed_messages`는 input과 동일한 `list[dict]` 형식. skeleton 처리된 메시지도 role/content 구조는 유지되므로 그대로 sLLM에 넣을 수 있음.

### 압축 전후 비교

```
# 원본 메시지 (tool_result가 2000 chars):
{"role": "user", "content": [
  {"type": "tool_result", "tool_use_id": "toolu_abc",
   "content": "/repo/sympy/printing/pretty.py\n/repo/sympy/core/power.py\n...(2000 chars)",
   "is_error": false}
]}

# Pass 1 적용 후 (truncate):
{"role": "user", "content": [
  {"type": "tool_result", "tool_use_id": "toolu_abc",
   "content": "/repo/sympy/printing/pretty.py\n/repo/sympy/core/power.py\n[TRUNCATED original_chars=2000]",
   "is_error": false}
]}

# Skeleton 처리 후 (score 낮은 메시지):
{"role": "user", "content": [
  {"type": "text", "text": "[result: 2000 chars]"}
]}
```

### 사용법

```python
from episodic_db.compress import fit_to_budget, score_messages

# === 기본 사용 (DB 없이, keyword fallback) ===
messages = load_conversation_from_somewhere()
result = fit_to_budget(messages, max_tokens=32000)

# sLLM에 주입
inject_to_context(result.compressed_messages)


# === DB 라벨 활용 (더 정확한 waste 기반 scoring) ===
from episodic_db.store.db import Database
db = Database(Path("episodic.db"))
conn = db.connect()

result = fit_to_budget(
    messages,
    max_tokens=32000,
    conn=conn,                   # waste 라벨 조회용
    session_id="session_xyz",    # 어떤 세션의 라벨을 볼지
)


# === scoring만 따로 확인 (디버깅/분석) ===
scores = score_messages(messages, conn=conn, session_id="session_xyz")
# → [1, 0, 4, 3, 2, 0, 0, 5, ...] 각 메시지의 학습 가치

# 높은 점수 = 낭비/실패 관련 = 학습 가치 높음 = 마지막까지 보존
# 낮은 점수 = 노이즈 = 먼저 skeleton/제거 대상
```

### 파라미터

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `messages` | (필수) | Claude API 형식 대화 메시지 리스트 |
| `max_tokens` | 32000 | 목표 context window 크기 |
| `head_messages` | 3 | 항상 보존할 첫 N개 메시지 (문제 정의) |
| `preserve_failures` | True | 실패 정보 보존 여부 |
| `conn` | None | SQLite connection (waste 라벨 조회용) |
| `session_id` | None | 세션 ID (conn과 함께 사용) |

### 의존성

- 외부 패키지 없음 (`json`, `sqlite3`, `dataclasses`만 사용)
- `compress.py` 단일 파일로 동작
- DB 연결은 optional — 없으면 keyword fallback으로 scoring

## Claude Compaction과의 비교

| | Claude Compaction | 이 모듈 (compress.py) |
|---|---|---|
| 방식 | LLM이 요약 생성 | 규칙 기반 scoring + tier별 압축 |
| 결정론성 | 비결정론적 (매번 다른 결과) | 결정론적 (동일 입력 → 동일 출력) |
| Scoring | 없음 (일괄 요약) | 메시지별 학습 가치 점수화 |
| 비용 | 요약에 추가 API call | 0 |
| 정보 손실 | 요약 품질에 의존, 무엇이 빠졌는지 불투명 | 명시적 — 마커 + score 기반 우선순위 |
| 용도 | 실시간 대화 이어가기 | 과거 낭비/실패를 학습 데이터로 압축 |

## 파일 위치

- `src/episodic_db/compress.py` — 압축 모듈
- `demo_compress.py` — 데모 + 벤치마크 스크립트
