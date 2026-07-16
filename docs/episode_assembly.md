# 에피소드 조립 상세

라벨링 완료 후 tool call 체인을 의미 단위 에피소드로 분할하고, 각 에피소드에 검색용 시그니처를 부여한다.

오케스트레이터: `episode/assembler.py` → `assemble_episodes(db, session_id, config)`

---

## 1. Segmentation (구간 분할)

**모듈:** `episode/assembler.py` → `_segment_tool_calls()`

### 원리

"**의미 있는 쓰기(WROTE + CONTRIBUTED)**" 하나가 에피소드 하나를 종결한다.

### 알고리즘

```python
segments = []
current_segment = []

for tc in tool_calls (seq 순서대로):
    current_segment.append(tc)

    is_write = tc.tool_name in ("Edit", "Write")
    is_contributed = tc.contributed_to == "CONTRIBUTED"

    if is_write AND is_contributed AND len(current_segment) > 1:
        segments.append(current_segment)
        current_segment = []

# 남은 마지막 segment도 추가
if current_segment:
    segments.append(current_segment)
```

### 예시

```
Tool calls:  [Grep, Read, Read, Bash, Edit✓, Read, Bash, Write✓, Bash, Bash]
                                        ↑ cut                       ↑ cut

Segments:
  Episode 1: [Grep, Read, Read, Bash, Edit✓]     → converged
  Episode 2: [Read, Bash, Write✓]                → converged
  Episode 3: [Bash, Bash]                         → abandoned (잔여)
```

### 엣지 케이스

- **Edit 하나만 있는 경우:** `len(current_segment) > 1` 조건 때문에 cut 안 됨. 다음 Edit에서 합쳐짐
- **Edit 없이 끝난 경우:** 마지막 잔여 segment가 그대로 하나의 에피소드가 됨 (보통 `abandoned`)
- **모든 call이 DID_NOT인 경우:** cut 조건을 못 만족 → 전체가 하나의 에피소드

---

## 2. Signature Extraction (시그니처 추출)

**모듈:** `episode/signature.py` → `extract_signature()`

에피소드의 검색용 facet을 추출한다. 모든 facet은 로그에서 결정론적으로 추출.

### 추출 상세

#### `converged_resource`
```python
wrote_resources = edges_touches WHERE mode='WROTE' AND tool_use_id IN members
converged_resource = wrote_resources[-1]  # 마지막 WROTE 대상
# "path:" prefix 제거
```

#### `touched_paths`
```python
all_resources = edges_touches WHERE tool_use_id IN members
touched_paths = [r.replace("path:", "") for r in all_resources if r.startswith("path:")]
```

#### `path_prefix`
```python
path_prefix = os.path.commonprefix(touched_paths)
# 슬래시로 끝나지 않으면 dirname 취함
# 예: ["auth/session.py", "auth/models.py"] → "auth/"
```

#### `changed_symbols`
```python
# Edit/Write call의 normalized_input에서 파일명 추출
# 확장자 제거한 basename을 symbol로 사용
# 예: "Edit auth/session.py" → "session"
```

#### `test_names`
```python
# resource_id가 "test:" prefix인 것들
test_names = [r.replace("test:", "") for r in touched_resources if r.startswith("test:")]
```

#### `grep_terms`
```python
# Grep/Search tool의 normalized_input에서 검색어 추출
# Bash의 grep 명령에서 패턴 추출
# 정규화: 소문자, 2자 이하 제거, 최대 20개
```

추출 로직:
1. `Grep: pattern` → 공백/특수문자로 토크나이즈
2. `Bash: grep "pattern" files` → 따옴표 내 문자열 + grep 뒤 패턴
3. 모든 term을 소문자 변환, 2자 이하 제거, 정렬, 20개 제한

#### `error_signature`
```python
# results 테이블에서 is_error=1인 결과 내용 검색
# Fallback: is_error 아니더라도 "error"/"exception"/"traceback" 포함된 결과

# 마지막 20줄에서 에러 패턴 찾기:
#   - "XxxError: message" 형태의 Python exception
#   - "error", "failed" 키워드 포함 라인

# 정규화:
#   - "line 123" → "line N"
#   - "/full/path" → "PATH"
#   - 'specific value' → 'X'
#   - 200자 제한
```

#### `lang`
```python
# touched_paths의 확장자 빈도 기반
# .py → python, .ts/.tsx → typescript, .js/.jsx → javascript, ...
# 가장 빈번한 언어 1개 반환
```

#### `tool_mix`
```python
# Counter(tool_name for tc in members)
# 예: {"Bash": 5, "Read": 3, "Edit": 1}
```

---

## 3. Metrics Computation (메트릭 산출)

**모듈:** `episode/metrics.py`

### Cost Rollup (`compute_cost_rollup`)

멤버 tool_call의 token/cost를 단순 합산:

```sql
SELECT SUM(input_tokens), SUM(output_tokens), SUM(cache_creation_tokens),
       SUM(cache_read_tokens), SUM(own_cost), SUM(carry_cost), SUM(total_cost)
FROM tool_calls WHERE tool_use_id IN (members)
```

`carry_ratio = carry_cost / total_cost`

### 분석 Metrics (`compute_episode_metrics`)

| Metric | 공식 | 의미 |
|--------|------|------|
| `read_output_token_ratio` | Read/Grep/Bash의 output_tokens / 전체 output_tokens | 높으면 읽기만 하는 에피소드 |
| `new_information_rate` | 새 result_hash를 가져온 call 수 / 전체 call 수 | 높으면 매 호출마다 새 정보 |
| `repeated_read_rate` | 동일 input_hash 재읽기 수 / 전체 Read/Grep 수 | 높으면 같은 것을 반복 읽음 |
| `futility_score` | 가중합 (아래) | 복합 무용도 점수 (0~1) |

**futility_score 공식:**
```
futility_score = 0.3 * (1 - new_information_rate)
               + 0.3 * repeated_read_rate
               + 0.2 * read_output_token_ratio
               + 0.2 * min(num_calls / 50, 1.0)
```

`new_information_rate` 세부:
- 각 tool_call의 result_hash를 순서대로 확인
- 이전에 본 적 없는 hash면 "새 정보"로 카운트
- result가 없는 call도 "새 정보"로 카운트 (결과 없음 ≠ 중복)

---

## 4. Waste Classification (낭비 분류)

**모듈:** `episode/waste_classifier.py` → `classify_waste_type()`

메트릭과 threshold를 비교하여 waste_type을 판정. **우선순위 순서로 검사** — 먼저 걸리면 즉시 반환.

### 판정 순서

```python
# 1. repeated-loop (최우선)
if max_repeat(input_hash) >= 3:
    return "repeated-loop"
if max_repeat >= 2 AND num_calls >= 20:
    return "repeated-loop"

# 2. expensive-failure
if count(is_wasteful calls) >= 3:
    return "expensive-failure"

# 3. read-heavy
if num_calls >= 10 AND read_output_token_ratio >= 0.70:
    return "read-heavy"
if num_calls >= 10 AND read_output_token_ratio >= 0.60:
    return "read-heavy"

# 4. futile-exploration
if num_calls >= 25 AND new_information_rate < 0.3 AND no Edit/Write:
    return "futile-exploration"
if no_new_info_calls >= 8 AND no Edit/Write:
    return "futile-exploration"
if futility_score > 0.6:
    return "futile-exploration"

# 5. productive (낭비 아님)
return None  → "productive"로 저장
```

### 에피소드 Outcome (세션 outcome과 별도)

에피소드별 outcome은 조립 시 독립적으로 판정:

```python
if has_edit_or_write:
    outcome = "converged"
elif max_repeat(input_hash) >= 3:
    outcome = "looped"
else:
    outcome = "abandoned"
```

---

## 5. Embedding (벡터 생성)

**호출:** `assembler.py` → `_embed_episode()` (조립 직후 inline 실행)

### 흐름

```
episode_data + tool_calls
    ↓
serialize_signature()  → 텍스트 직렬화
    ↓
LocalEmbedder.embed()  → float32 벡터 (384-dim)
    ↓
struct.pack()  → BLOB
    ↓
UPDATE episodes SET embedding = ?, embedding_text = ?, embedding_model = ?, embedding_dim = ?
```

### 직렬화 규칙 (`embedding/serializer.py`)

고정 순서로 facet을 `" | "` 구분자로 연결:

```
waste_type | outcome | lang | path_prefix | conv=resource | grep(...) | changed(...) | test(...) | err(...) | actions(...)
```

경로는 **상대경로로 변환** (cross-session similarity 향상):
- `/workspace/swebench_workspaces/django__django-11039/django/auth/` → `django/auth/`
- `/Users/user/projects/myapp/src/` → `src/` (마지막 3단계까지)

`actions(...)` 항목: 낭비로 마킹된 call의 normalized_input (최대 10개, 세미콜론 구분)

### Embedder 옵션

| Backend | 모델 | 차원 | API Key |
|---------|------|------|---------|
| `local` (기본) | `BAAI/bge-small-en-v1.5` | 384 | 불필요 |
| `openai` | `text-embedding-3-small` | 1536 | `OPENAI_API_KEY` 필요 |

설정: `Config.embedding` (model, dim, backend)

### 실패 처리

임베딩 생성 실패 시 (모델 로딩 실패 등) → `embedding_text`만 저장하고 `embedding` BLOB은 NULL. 나중에 `episodic-db embed --all`로 재생성 가능.

---

## 6. 최종 저장

에피소드 조립이 완료되면:

1. `episodes` 테이블에 INSERT OR REPLACE
2. 멤버 tool_call의 `episode_id` 컬럼 업데이트
3. COMMIT

---

## 전체 코드 흐름 (의사 코드)

```python
def assemble_episodes(db, session_id, config):
    tool_calls = get_session_tool_calls(session_id)
    segments = _segment_tool_calls(tool_calls)

    for segment in segments:
        member_ids = [tc.tool_use_id for tc in segment]
        episode_id = f"ep_{random_8hex}"

        # 추출/계산
        sig = extract_signature(conn, member_ids, tool_calls, session_id)
        metrics = compute_episode_metrics(conn, member_ids, tool_calls)
        cost = compute_cost_rollup(conn, member_ids)
        waste_type = classify_waste_type(metrics, len(segment), segment, config.thresholds)

        # 에피소드 데이터 조합
        episode_data = {sig + metrics + cost + waste + outcome + ...}

        # 저장
        insert_episode(conn, episode_data)
        _embed_episode(conn, episode_data, segment, config)

        # tool_call → episode 매핑
        UPDATE tool_calls SET episode_id = ? WHERE tool_use_id IN (member_ids)
```
