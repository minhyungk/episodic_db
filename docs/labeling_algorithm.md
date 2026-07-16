# 라벨링 알고리즘 상세

세션 종료 후 실행되는 결정론적 분석 파이프라인. LLM 없이 그래프 순회와 해시 비교만으로 각 tool call의 기여도와 낭비 여부를 판정한다.

오케스트레이터: `label/pipeline.py` → `run_labeling(db, session_id)`

실행 순서:
1. Token Reconciliation
2. Contribution Marking
3. Waste Signal Computation
4. Outcome Classification

---

## 1. Token Reconciliation

**모듈:** `proxy/token_bridge.py` → `TokenBridge.reconcile_session()`

Proxy가 캡처한 API-level token 데이터를 개별 tool_call에 분배한다.

### 왜 필요한가

- Claude Code의 hook payload에는 token 사용량이 없다
- Token 정보는 API 응답에만 포함됨
- 하나의 API call에 여러 tool_use가 포함될 수 있음 (병렬 tool use)

### 알고리즘

```
FOR each proxy_call in session (ordered by call_index):
    tool_use_ids = JSON.parse(proxy_call.tool_use_ids)
    n_tools = len(tool_use_ids)

    carry_cost = (cache_read_tokens / 1M) * $0.30
    own_cost = total_cost - carry_cost

    FOR each tool_use_id:
        tool_call.input_tokens = proxy_call.input_tokens      # 전체 (공유)
        tool_call.output_tokens = proxy_call.output_tokens / n_tools  # 균등 분배
        tool_call.cache_creation = proxy_call.cache_creation  # 전체 (공유)
        tool_call.cache_read = proxy_call.cache_read          # 전체 (공유)
        tool_call.own_cost = own_cost / n_tools
        tool_call.carry_cost = carry_cost / n_tools
        tool_call.total_cost = proxy_call.total_cost / n_tools
        tool_call.latency_ms = proxy_call.latency_ms
        tool_call.model = proxy_call.model (if not already set)

sessions.total_tokens = SUM(all proxy_calls tokens)
sessions.total_cost = SUM(all proxy_calls cost)
```

### own_cost vs carry_cost

- **own_cost:** 이 turn에서 새로 생성된 비용 (input + output + cache_write)
- **carry_cost:** 이전 컨텍스트를 읽은 비용 (cache_read)
- **carry_ratio:** carry_cost / total_cost — 높을수록 "이전 문맥 비용이 큰" call

---

## 2. Contribution Marking

**모듈:** `label/contribution.py` → `mark_contributions()`

"이 tool call이 최종 결과물(patch)에 기여했는가?"를 판정한다.

### 핵심 인사이트

세션이 끝나면 **어떤 파일이 Write/Edit 되었는지** 이미 알고 있다. "기여했나?"는 주관적 판단이 아니라 "**patch 자원에서 역방향 도달 가능한가?**"라는 그래프 질문이 된다.

### 알고리즘

```
1. wrote_resources = 세션에서 WROTE된 resource 목록 수집
   (edges_touches에서 mode='WROTE'인 resource_id들)

2. IF wrote_resources가 비어있으면:
   → 모든 tool_call을 'DID_NOT'으로 마킹
   → 종료

3. wrote_call_ids = WROTE edge를 가진 tool_call들의 ID (직접 편집한 call)

4. reachable = wrote_call_ids (초기 집합)

5. FOR each wrote_resource:
   reader_ids = 같은 resource를 READ한 tool_call ID들
   reachable.add(reader_ids)  # 해당 파일을 사전에 읽은 call도 기여

6. FOR each tool_call in session:
   IF tool_call.id IN reachable:
       contributed_to = "CONTRIBUTED"
   ELSE:
       contributed_to = "DID_NOT"
```

### 기여 판정 기준

| 조건 | 결과 |
|------|------|
| 파일을 직접 Edit/Write 했음 | `CONTRIBUTED` |
| 나중에 Edit된 파일을 사전에 Read 했음 | `CONTRIBUTED` |
| 위 어디에도 해당 안 됨 | `DID_NOT` |

### 한계 (의도적 감수)

- "이해를 도왔지만 patch에 직접 닿지 않은" Read (예: 관련 파일 구조 파악)를 `DID_NOT`으로 오판할 수 있음
- 이는 direction-level 조언에는 치명적이지 않으므로 감수
- 정밀한 판정이 필요하면 Semantic DB 층에서 보강

---

## 3. Waste Signal Computation

**모듈:** `label/waste_signals.py` → `compute_waste_signals()`

중복 호출과 새 정보 없는 호출을 식별한다.

### 알고리즘

```
Phase 1: Duplicate 감지 (input_hash 기반)
─────────────────────────────────────────
seen_hashes = {}
FOR each tool_call (순서대로):
    IF input_hash IN seen_hashes:
        → DUPLICATE_OF edge 생성 (edges_duplicate_of 테이블)
    ELSE:
        seen_hashes[input_hash] = tool_use_id

Phase 2: No-new-info 감지 (result_hash 기반)
─────────────────────────────────────────────
seen_result_hashes = {}
no_new_info_ids = set()
FOR each tool_call:
    FOR each result of this call:
        IF result_hash IN seen_result_hashes:
            → no_new_info_ids.add(tool_use_id)
        ELSE:
            seen_result_hashes[result_hash] = tool_use_id

Phase 3: Waste 마킹
───────────────────
FOR each tool_use_id IN no_new_info_ids:
    IF contributed_to == 'DID_NOT':
        → is_wasteful = 1
```

**핵심:** 새 정보를 제공하지 못한 call(`result_hash` 중복)이면서 동시에 최종 결과에 기여하지 못한 call만 waste로 마킹.

---

## 4. Outcome Classification

**모듈:** `label/outcome.py` → `classify_outcome()`

세션 전체의 결말을 분류한다.

### 판정 로직

```python
def classify_outcome(session_id):
    wrote_resources = 세션의 WROTE resource 목록
    tool_calls = 세션의 전체 tool_call

    if not tool_calls:
        return "abandoned"

    if wrote_resources AND session.success:
        return "converged"

    input_hashes = [tc.input_hash for tc in tool_calls]
    max_repeat = max(Counter(input_hashes).values())
    if max_repeat >= 3:
        return "looped"

    if not wrote_resources:
        return "abandoned"

    return "converged"
```

| Outcome | 의미 | 조건 |
|---------|------|------|
| `converged` | 정상 수렴 | 파일 편집 있음 + 성공 (또는 편집 있음) |
| `looped` | 무한 루프 | 동일 작업 3회+ 반복 |
| `abandoned` | 포기/미완 | 편집 없이 종료 |

---

## 파이프라인 전체 흐름 요약

```
run_labeling(db, session_id)
│
├─ 1. TokenBridge.reconcile_session()
│      proxy_calls → tool_calls 에 token/cost 분배
│
├─ 2. mark_contributions()
│      WROTE resource 역추적 → CONTRIBUTED / DID_NOT
│
├─ 3. compute_waste_signals()
│      input_hash 중복 → DUPLICATE_OF edge
│      result_hash 중복 + DID_NOT → is_wasteful = 1
│
└─ 4. classify_outcome()
       세션 결말 판정 → sessions.success 업데이트
```

이 라벨링 결과는 이후 에피소드 조립 단계에서 segmentation과 waste classification의 입력으로 사용된다.
