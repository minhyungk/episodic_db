# Episode Schema

## episodes 테이블

| Field | Type | Description |
|-------|------|-------------|
| `episode_id` | TEXT PK | 고유 식별자 (`ep_` + 8자 hex) |
| `session_id` | TEXT | 소속 세션 ID |
| `created_at` | TEXT | 에피소드 생성 시각 (ISO 8601 UTC) |
| `converged_by` | TEXT | 에피소드를 종결시킨 tool_use_id (마지막 Write/Edit) |
| `waste_type` | TEXT | 분류 결과. 아래 [Waste Types](#waste-types) 참조 |
| `outcome` | TEXT | `converged` / `looped` / `abandoned` |
| `converged_resource` | TEXT | 최종 Write/Edit 대상 파일 경로 |
| `touched_paths` | TEXT (JSON) | 에피소드가 접근한 모든 파일 경로 리스트 |
| `path_prefix` | TEXT | touched_paths의 common prefix (디렉토리) |
| `changed_symbols` | TEXT (JSON) | Edit/Write 대상 파일에서 추출한 심볼명 (함수/모듈명) |
| `test_names` | TEXT (JSON) | 접근한 테스트 리소스 이름들 |
| `grep_terms` | TEXT (JSON) | Grep/Search/Bash grep에서 사용된 검색 키워드 |
| `error_signature` | TEXT | 에피소드 내 에러 메시지 정규화 요약 (200자 이내) |
| `lang` | TEXT | 주요 언어 (파일 확장자 기반 감지) |
| `tool_mix` | TEXT (JSON) | tool별 호출 횟수 (`{"Bash": 5, "Read": 3, ...}`) |

### Cost Fields

| Field | Type | Description |
|-------|------|-------------|
| `total_input_tokens` | INTEGER | 에피소드 전체 input token 합산 |
| `total_output_tokens` | INTEGER | 에피소드 전체 output token 합산 |
| `total_cache_creation` | INTEGER | cache creation token 합산 |
| `total_cache_read` | INTEGER | cache read token 합산 |
| `own_cost` | REAL | 에피소드 자체 API 비용 (USD) |
| `carry_cost` | REAL | 이전 컨텍스트 누적 비용 |
| `total_cost` | REAL | own_cost + carry_cost |
| `carry_ratio` | REAL | carry_cost / total_cost (0~1) |

### Metrics Fields

| Field | Type | Description |
|-------|------|-------------|
| `read_output_token_ratio` | REAL | Read/Grep/Bash output token이 전체 output 중 차지하는 비율 (0~1). 높을수록 읽기만 하는 에피소드 |
| `new_information_rate` | REAL | 새로운 정보를 가져온 call의 비율 (0~1). result_hash 기반 중복 감지 |
| `repeated_read_rate` | REAL | 동일 대상 재읽기 비율 (0~1) |
| `futility_score` | REAL | 복합 무용도 점수 (0~1). `0.3*(1-new_info_rate) + 0.3*repeated_read_rate + 0.2*read_output_ratio + 0.2*(num_calls/50)` |

### Waste/Labeling Fields

| Field | Type | Description |
|-------|------|-------------|
| `is_wasteful` | INTEGER | 1이면 waste 에피소드, 0이면 productive |
| `wasted_member_ids` | TEXT (JSON) | waste로 판정된 개별 tool call ID 리스트 |
| `wasted_own_cost` | REAL | waste call들의 비용 합 |
| `wasted_carry_cost` | REAL | waste call들의 누적 컨텍스트 비용 |
| `wasted_tokens` | INTEGER | waste call들의 토큰 합 |

### Embedding Fields

| Field | Type | Description |
|-------|------|-------------|
| `embedding_text` | TEXT | 임베딩 입력 텍스트 (serialize_signature 출력, 상대경로 변환됨) |
| `embedding_model` | TEXT | 사용된 임베딩 모델 (e.g. `BAAI/bge-small-en-v1.5`) |
| `embedding_dim` | INTEGER | 벡터 차원수 (384) |
| `embedding` | BLOB | float32 packed 벡터 (`struct.pack(f'{dim}f', *vec)`) |

---

## outcome 분류 기준

| Value | Condition |
|-------|-----------|
| `converged` | Edit/Write call이 하나라도 있음 (작업 완료) |
| `looped` | Edit/Write 없이 동일 input_hash가 3회 이상 반복 |
| `abandoned` | Edit/Write 없고 반복도 아닌 경우 (세션 종료 등으로 미완) |

---

## 에피소드 경계 (Segmentation)

Tool call chain을 **WROTE + CONTRIBUTED** 경계에서 분할:
- `tool_name in (Edit, Write)` AND `contributed_to == "CONTRIBUTED"` 이면서 segment 길이 > 1일 때 cut
- 하나의 "의미 있는 쓰기 작업 완료"가 에피소드 하나를 형성

---

## tool_calls 테이블 (참고)

| Field | Type | Description |
|-------|------|-------------|
| `tool_use_id` | TEXT PK | Claude가 부여한 tool call 고유 ID |
| `session_id` | TEXT | 소속 세션 |
| `seq` | INTEGER | 세션 내 호출 순서 (0-based) |
| `timestamp` | TEXT | 호출 시각 |
| `model` | TEXT | 사용된 모델 |
| `tool_name` | TEXT | Edit, Write, Bash, Read, Grep 등 |
| `tool_input_json` | TEXT | tool input 전체 JSON (원본) |
| `input_hash` | TEXT | normalized_input의 SHA256 (중복 감지용) |
| `normalized_input` | TEXT | 정규화된 입력 요약 (100자 제한, 경로만 등) |
| `contributed_to` | TEXT | `CONTRIBUTED` / `DID_NOT` (labeling 결과) |
| `episode_id` | TEXT | 소속 에피소드 ID |

---

## proxy_calls 테이블 (참고)

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | TEXT | 세션 ID |
| `call_index` | INTEGER | 세션 내 API call 순서 |
| `model` | TEXT | 호출된 모델 |
| `input_tokens` / `output_tokens` | INTEGER | 토큰 사용량 |
| `tool_use_ids` | TEXT (JSON) | 이 call에서 생성된 tool_use_id 리스트 |
| `assistant_text` | TEXT | assistant 응답 텍스트 |
| `user_message` | TEXT | 유저 메시지 (system-reminder 제거됨) |
| `latency_ms` | REAL | API 응답 지연시간 |

---

## Waste Types (priority order)

분류는 `classify_waste_type()` — 먼저 매칭되면 후순위는 체크하지 않음.

| Type                 | 조건                                                                 | 의미                         |
| -------------------- | -------------------------------------------------------------------- | ---------------------------- |
| `repeated-loop`      | 같은 input_hash ≥3회 반복, 또는 ≥2회 + episode 내 call ≥20           | 동일 작업 반복 (루프에 빠짐) |
| `expensive-failure`  | is_wasteful 플래그 call ≥3개                                         | 비용 높은 에러 연속 발생     |
| `read-heavy`         | call ≥10 + read_output_token_ratio ≥0.60                             | 읽기만 반복, 산출물 없음     |
| `futile-exploration` | (call ≥25 + new_info_rate <0.3 + edit 없음) 또는 futility_score >0.6 | 새 정보 없이 탐색만 지속     |
| `productive`         | 위 조건 모두 미해당 (classifier → None)                              | 정상 작업 — waste 아님       |

- Threshold 값은 `config.py`의 `WasteThresholds` dataclass에서 조정
- `productive`는 classifier가 None을 반환할 때 assembler에서 부여하는 fallback label