# Episode Pipeline

세션 종료 시 raw tool call 데이터를 의미 단위 에피소드로 변환하는 과정.

## 전체 흐름

```
SessionEnd hook 발동
    │
    ▼
┌─────────────────────────────────────────┐
│  Stage 1: Labeling (label/pipeline.py)  │
├─────────────────────────────────────────┤
│  1. Token reconciliation                │
│  2. Contribution marking                │
│  3. Waste signal computation            │
│  4. Session outcome classification      │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  Stage 2: Assembly (episode/assembler)  │
├─────────────────────────────────────────┤
│  1. Segmentation                        │
│  2. Signature extraction                │
│  3. Metrics computation                 │
│  4. Waste classification                │
│  5. Embedding                           │
└─────────────────────────────────────────┘
```

---

## Stage 1: Labeling

`label/pipeline.py` → `run_labeling(db, session_id)`

### 1-1. Token Reconciliation

`TokenBridge.reconcile_session()` — proxy_calls에 기록된 token 사용량을 개별 tool_call에 분배.

proxy_calls의 `tool_use_ids` 필드로 어떤 tool call이 어떤 API call에 속하는지 매핑하여 input_tokens, output_tokens, cost를 할당.

### 1-2. Contribution Marking

`label/contribution.py` → `mark_contributions()`

**Patch reachability 알고리즘:**

```
1. 세션에서 WROTE한 resource(파일) 목록 수집
2. 각 WROTE resource에 대해 같은 파일을 READ한 call 찾기
3. WROTE call 자체 + 같은 파일을 READ한 call = CONTRIBUTED
4. 나머지 = DID_NOT
```

판정 기준:
- `CONTRIBUTED` — 최종 패치에 기여함 (직접 Edit/Write 했거나, 해당 파일을 사전에 Read)
- `DID_NOT` — 최종 결과물과 무관한 call (탐색, 실패한 시도 등)

### 1-3. Waste Signal Computation

`label/waste_signals.py` → `compute_waste_signals()`

1. **Duplicate 감지**: 동일 `input_hash`가 이미 나온 적 있으면 `DUPLICATE_OF` edge 생성
2. **No-new-info 감지**: `result_hash`가 이미 본 결과와 동일하면 `is_wasteful = 1` 마킹 (단, `DID_NOT` call에만)

### 1-4. Session Outcome

`label/outcome.py` → `classify_outcome()`

| Outcome | 조건 |
|---------|------|
| `converged` | WROTE resource 있음 + session.success |
| `looped` | 동일 input_hash 3회 이상 반복 |
| `abandoned` | WROTE resource 없음 |

---

## Stage 2: Episode Assembly

`episode/assembler.py` → `assemble_episodes(db, session_id)`

### 2-1. Segmentation (경계 분할)

```python
for tc in tool_calls:
    current_segment.append(tc)
    if tc.tool_name in (Edit, Write)
       AND tc.contributed_to == "CONTRIBUTED"
       AND len(current_segment) > 1:
        → segment 완성, 새 segment 시작
```

**핵심 원리:** "의미 있는 쓰기(WROTE+CONTRIBUTED)" 하나가 에피소드 하나를 종결.

예시:
```
[Read, Bash, Bash, Read, Edit✓]  → Episode 1 (탐색 후 수정)
[Edit✓]                          → Episode 2 (즉시 수정)
[Bash, Bash, Bash, Write✓]      → Episode 3 (테스트 후 파일 생성)
[Bash, Bash]                     → Episode 4 (미완 - 마지막 잔여)
```

### 2-2. Signature Extraction

`episode/signature.py` → `extract_signature()`

에피소드의 의미론적 시그니처를 추출:

| Facet | 추출 방법 |
|-------|-----------|
| `converged_resource` | 마지막 WROTE 대상 파일 |
| `touched_paths` | edges_touches에서 모든 path: resource |
| `path_prefix` | touched_paths의 공통 prefix |
| `changed_symbols` | Edit/Write 대상 파일의 basename (확장자 제거) |
| `test_names` | test: prefix resource들 |
| `grep_terms` | Grep/Search/Bash grep에서 검색어 추출 (정규식 파싱) |
| `error_signature` | 에러 결과에서 마지막 exception/error 라인 정규화 |
| `lang` | 파일 확장자 빈도 기반 주요 언어 |
| `tool_mix` | tool별 호출 횟수 Counter |

### 2-3. Metrics Computation

`episode/metrics.py` → `compute_episode_metrics()`, `compute_cost_rollup()`

**Cost rollup:** 멤버 tool_call의 token/cost 합산

**분석 메트릭:**
- `read_output_token_ratio` = Read/Grep/Bash output tokens / 전체 output tokens
- `new_information_rate` = 새 result_hash를 가져온 call 비율
- `repeated_read_rate` = 동일 대상 재읽기 비율
- `futility_score` = 가중 복합 점수 (0~1)

### 2-4. Waste Classification

`episode/waste_classifier.py` → `classify_waste_type()`

메트릭과 threshold 비교하여 waste_type 판정. 우선순위:
1. `repeated-loop` (input_hash 반복)
2. `expensive-failure` (에러 call 다수)
3. `read-heavy` (읽기 비율 과다)
4. `futile-exploration` (새 정보 없이 탐색만)
5. `None` → `productive`

### 2-5. Embedding

`embedding/serializer.py` → `serialize_signature()`
`embedding/embedder.py` → `LocalEmbedder.embed()`

1. 에피소드 시그니처를 텍스트로 직렬화:
```
waste_type | outcome | lang | relative_path | conv=relative_path | grep(...) | changed(...) | err(...) | actions(...)
```

2. 경로는 상대경로로 변환 (cross-session similarity 향상)

3. BAAI/bge-small-en-v1.5 (384 dim) 로컬 임베딩

4. float32 packed blob으로 DB에 저장

---

## 데이터 캡처 (Hooks)

에피소드화의 원재료는 hook으로 수집:

| Hook Event | Timing | 캡처 데이터 |
|------------|--------|-------------|
| `SessionStart` | 세션 시작 | session_id, cwd, env snapshot |
| `PreToolUse` | tool 실행 직전 | tool_name, tool_input, input_hash, normalized_input, tool_input_json |
| `PostToolUse` | tool 실행 직후 | tool_response (결과), is_error, resource edge |
| `SessionEnd` | 세션 종료 | → labeling + assembly 트리거 |
| `Stop` | 강제 종료 | → SessionEnd와 동일 처리 |

보충 데이터는 API proxy에서 수집:
- token 사용량 (input/output/cache)
- assistant_text (Claude의 텍스트 응답)
- user_message (유저 프롬프트, system-reminder 제거)
- tool_use_ids (어떤 call이 같은 API turn에 속하는지)

---

## 타이밍

- Hook timeout: PreToolUse 5초, SessionEnd 30초
- 임베딩 레이턴시: ~50ms/episode (로컬 모델)
- 전체 pipeline: 세션 종료 후 1~5초 내 완료 (tool call 수에 비례)
