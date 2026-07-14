# PreToolUse 실시간 벡터 검색 검증 보고서

**검증일**: 2026-07-14  
**목적**: 세션이 진행 중인 시점에 PreToolUse hook에서 벡터 검색으로 유사 과거 에피소드를 retrieve할 수 있는지 확인

---

## 검증 방법

### 가정한 상황

실제 Claude Code 세션을 돌리지 않고, 다음을 모의 구현:

1. **과거 세션 데이터** — 10개의 에피소드를 DB에 삽입 (각각 embedding 포함)
2. **현재 세션의 PreToolUse 이벤트** — agent가 tool을 호출하기 직전의 상황을 시뮬레이션
3. **검색** — 현재 action 정보를 쿼리로 변환하여 과거 에피소드 중 유사한 것을 cosine similarity로 retrieve

### 사전 구축된 과거 에피소드 (10개)

| ID | 도메인 | waste_type | outcome | 핵심 내용 |
|----|--------|-----------|---------|-----------|
| ep_001 | src/auth/ | repeated_read | looped | JWT token expired 에러 반복 읽기 |
| ep_002 | src/auth/ | productive | converged | token refresh 로직 성공 구현 |
| ep_003 | src/db/ | context_snowball | abandoned | DB migration 중 lock 에러로 포기 |
| ep_004 | src/db/migrations/ | productive | converged | index 추가 마이그레이션 성공 |
| ep_005 | src/api/ | futile_exploration | abandoned | route 찾기 실패, 무의미 탐색 |
| ep_006 | src/api/ | productive | converged | FastAPI route handler 성공 구현 |
| ep_007 | tests/ | read_heavy | looped | e2e 테스트 디버깅 중 과도한 읽기 |
| ep_008 | src/components/ | productive | converged | React Button 컴포넌트 구현 |
| ep_009 | src/auth/ | repeated_read | looped | TS session/cookie 미들웨어 반복 |
| ep_010 | src/auth/ | productive | converged | TS auth middleware 성공 구현 |

각 에피소드는 `serialize_signature()`로 텍스트화 후 `BAAI/bge-small-en-v1.5` (384dim)로 임베딩됨.

---

## 시나리오별 결과

### Scenario 1: Auth 토큰 에러 디버깅 중 grep 호출

**가정한 실시간 상황**: Agent가 Python auth 모듈에서 token expired 에러를 디버깅하며, `Grep` tool로 "token" 검색하려는 순간

**구성된 쿼리**:
```
python auth token expired error debugging | Grep: token
```

**Retrieve 결과**:

| 순위 | Episode | Similarity | 유형 | 의미 |
|------|---------|-----------|------|------|
| 1 | ep_001 | 0.8701 | repeated_read (wasteful) | "같은 상황에서 반복 읽기로 시간 낭비했음" |
| 2 | ep_002 | 0.7568 | productive | "refresh_token 로직으로 해결한 적 있음" |
| 3 | ep_009 | 0.7246 | repeated_read (wasteful) | "TS에서도 비슷한 패턴으로 루프 빠짐" |

**해석**: agent는 "과거에 token 관련으로 반복 읽기 루프에 빠진 적 있으니 이번엔 바로 refresh_token 방식으로 가자"라는 판단 가능.

---

### Scenario 2: DB 마이그레이션 명령 실행 직전

**가정한 실시간 상황**: Agent가 `alembic upgrade head` Bash 명령을 실행하려는 순간

**구성된 쿼리**:
```
database migration schema upgrade | Bash: alembic upgrade head
```

**Retrieve 결과**:

| 순위 | Episode | Similarity | 유형 | 의미 |
|------|---------|-----------|------|------|
| 1 | ep_003 | 0.7777 | context_snowball (wasteful) | "DB lock 에러로 컨텍스트 눈덩이 → 포기" |
| 2 | ep_004 | 0.7230 | productive | "index 추가 migration은 성공적으로 완료됨" |
| 3 | ep_005 | 0.5941 | futile_exploration | (관련도 낮음) |

**해석**: "migration 돌리기 전에 DB lock 확인하자, 전에 lock 걸려서 포기한 적 있다"는 교훈 제공.

---

### Scenario 3: API 라우트 파일 편집

**가정한 실시간 상황**: Agent가 `src/api/endpoints.py` 파일을 Edit하려는 순간

**구성된 쿼리**:
```
python FastAPI route handler endpoint implementation | Edit src/api/endpoints.py
```

**Retrieve 결과**:

| 순위 | Episode | Similarity | 유형 | 의미 |
|------|---------|-----------|------|------|
| 1 | ep_006 | 0.8458 | productive | "router 등록 방식으로 성공 구현" |
| 2 | ep_005 | 0.6551 | futile_exploration (wasteful) | "route 찾다가 헤맴" |
| 3 | ep_010 | 0.6445 | productive | (관련도 낮음) |

**해석**: "endpoint 추가할 때 register_route 패턴 참고, 파일 탐색으로 시간 쓰지 말 것"

---

### Scenario 4: Wasteful + Productive 동시 반환

**가정한 실시간 상황**: Agent가 TS auth session middleware를 디버깅하며 grep 호출

**구성된 쿼리**:
```
typescript auth session middleware debugging | Grep: session cookie middleware
```

**Retrieve 결과**:

| 순위 | Episode | Similarity | 분류 |
|------|---------|-----------|------|
| 1 | ep_009 | 0.8560 | WASTEFUL (repeated_read) |
| 2 | ep_010 | 0.8551 | PRODUCTIVE (converged) |
| 3 | ep_001 | 0.7052 | WASTEFUL (repeated_read) |
| 4 | ep_002 | 0.6941 | PRODUCTIVE |
| 5 | ep_008 | 0.6576 | PRODUCTIVE |

**핵심**: 반면교사(ep_009: "이렇게 하면 루프 빠짐")와 해결책(ep_010: "이렇게 하면 됨")이 동시에 나옴.

---

### Scenario 5: 지연시간

| 측정 항목 | 결과 |
|-----------|------|
| 모델 로딩 (최초 1회) | ~1초 |
| 검색 쿼리 평균 | **9.7ms** |
| Hook 타임아웃 | 5,000ms |
| 여유분 | 4,990ms |

---

### Scenario 6: 크로스 언어 검색

**가정한 실시간 상황**: TS 파일(`src/auth/validate.ts`)을 편집하는데, 쿼리에 "token expired" 포함

**구성된 쿼리**:
```
authentication validation token expired error | Edit src/auth/validate.ts
```

**Retrieve 결과**:

| 순위 | Episode | Similarity | 언어 |
|------|---------|-----------|------|
| 1 | ep_001 | 0.7782 | **Python** |
| 2 | ep_010 | 0.6994 | TypeScript |
| 3 | ep_009 | 0.6913 | TypeScript |
| 4 | ep_002 | 0.6667 | **Python** |

**핵심**: 언어가 다르더라도 "auth + token + error"라는 의미가 같으면 검색됨. Python에서의 해결 경험이 TS 작업에 참고 가능.

---

### Scenario 7: 필터로 반면교사만 조회

`is_wasteful=True` SQL 필터 적용 시 productive 에피소드가 완전히 배제됨:

| 순위 | Episode | waste_type |
|------|---------|-----------|
| 1 | ep_001 | repeated_read |
| 2 | ep_009 | repeated_read |
| 3 | ep_007 | read_heavy |
| 4 | ep_003 | context_snowball |
| 5 | ep_005 | futile_exploration |

---

## 쿼리 구성 방식

PreToolUse 시점에서 검색 쿼리는 다음 두 부분을 `|`로 결합:

```
{context} | {tool_name} {tool_input_summary}
```

- **context**: 현재 세션에서 agent가 뭘 하고 있는지에 대한 요약 (예: "python auth token expired error debugging")
- **tool action**: 지금 호출하려는 tool의 normalized input (예: "Grep: token", "Edit src/api/routes.py")

이 결합 텍스트가 동일한 임베딩 모델로 벡터화되어, 과거 에피소드의 `embedding_text`와 cosine similarity 비교됨.

---

## 결론

| 질문 | 답 |
|------|---|
| 실시간 검색 가능? | O — 10ms 미만 (hook 5초 타임아웃 대비 충분) |
| 의미 유사도로 찾는가? | O — 같은 키워드 없어도 유사 도메인 에피소드 검색 |
| 반면교사 제공 가능? | O — wasteful 에피소드가 상위 랭킹에 포함 |
| 해결책도 함께? | O — productive 에피소드도 동시에 반환 |
| 크로스 언어 검색? | O — Python/TS 간 유사 상황 모두 검색 |
| 필터링 가능? | O — is_wasteful, path_prefix, lang 등으로 facet 검색 |

**한계점**:
- 에피소드 수가 수만 개 이상 시 brute-force cosine이 느려질 수 있음 (→ ANN 인덱스 필요)
- context 정보의 품질이 검색 정확도에 직결됨 (hook에서 어떤 컨텍스트를 전달하느냐가 핵심)
- 모델 최초 로딩 시간 ~1초 (프로세스 상주로 해결 가능)
