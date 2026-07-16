# 검색 시스템 상세

에피소드를 검색하는 두 가지 방식: SQL 기반 **Facet 검색**과 벡터 기반 **유사도 검색**.

---

## Facet 검색 (SQL)

**모듈:** `query/facet_search.py` → `search_episodes()`

### 사용법

```bash
# CLI
episodic-db query --path-prefix auth/ --waste-type futile-exploration
episodic-db query --outcome looped --lang python --limit 20
```

### 지원 필터

| 필터 | DB 컬럼 | 매칭 방식 |
|------|---------|-----------|
| `path_prefix` | `episodes.path_prefix` | 정확히 일치 (`=`) |
| `waste_type` | `episodes.waste_type` | 정확히 일치 |
| `outcome` | `episodes.outcome` | 정확히 일치 |
| `lang` | `episodes.lang` | 정확히 일치 |
| `converged_resource` | `episodes.converged_resource` | 정확히 일치 |
| `is_wasteful` | `episodes.is_wasteful` | 정확히 일치 (0 or 1) |
| `grep_terms` | `episodes.grep_terms` | LIKE (부분 문자열 매칭) |

### 복합 필터

여러 필터를 동시에 지정하면 AND로 결합:

```sql
SELECT episode_id, session_id, waste_type, outcome, path_prefix,
       converged_resource, lang, total_cost, is_wasteful,
       read_output_token_ratio, futility_score, created_at
FROM episodes
WHERE path_prefix = 'auth/'
  AND waste_type = 'futile-exploration'
  AND lang = 'python'
ORDER BY created_at DESC
LIMIT 20
```

### grep_terms 검색

`grep_terms`는 JSON 배열로 저장되므로 LIKE로 부분 매칭:

```sql
WHERE grep_terms LIKE '%login%'
  AND grep_terms LIKE '%auth%'
```

### 에피소드 상세 조회

`get_episode_detail(db, episode_id)`:
- 에피소드 전체 필드 반환
- 소속 tool_call 목록 (seq 순서) 포함
- JSON 필드(touched_paths, changed_symbols 등) 자동 파싱

---

## 벡터 유사도 검색

**모듈:** `query/vector_search.py` → `search_similar()`

자연어 쿼리를 임베딩하여 기존 에피소드와의 코사인 유사도로 검색.

### 사용법

```bash
# CLI
episodic-db query --similar "authentication token expiry bug" --limit 5

# 벡터 검색 + facet 필터 결합
episodic-db query --similar "database migration" --path-prefix db/ --waste-type read-heavy
```

### 알고리즘

```python
def search_similar(query_text, limit=5, filters=None):
    # 1. 쿼리 텍스트를 임베딩
    query_vec = embedder.embed([query_text])[0]
    query_vec = normalize(query_vec)

    # 2. SQL 필터 조건으로 후보 에피소드 로드
    episodes = SELECT * FROM episodes
               WHERE embedding IS NOT NULL
               [AND facet filters]

    # 3. Brute-force cosine similarity
    for each episode:
        ep_vec = unpack(episode.embedding)
        similarity = dot(query_vec, normalize(ep_vec))

    # 4. 유사도 내림차순 정렬 → top-K 반환
    return sorted_by_similarity[:limit]
```

### 성능 특성

- Brute-force 방식 (ANN 인덱스 없음)
- 수천 개 에피소드까지는 충분히 빠름 (<100ms)
- 대규모 확장 필요 시 HNSW 인덱스 추가 고려

### 반환 형식

```python
[
    {
        "episode_id": "ep_a1b2c3d4",
        "waste_type": "futile-exploration",
        "outcome": "converged",
        "path_prefix": "auth/",
        "converged_resource": "auth/session.py",
        "total_cost": 0.0499,
        "lang": "python",
        "similarity": 0.847,  # cosine similarity score
    },
    ...
]
```

---

## 검색 전략 가이드

| 알고 있는 것 | 추천 방식 |
|-------------|-----------|
| 정확한 경로/패턴 | Facet 검색 (`--path-prefix`, `--waste-type`) |
| "이런 상황" 자연어 | 벡터 검색 (`--similar`) |
| 경로 + 자연어 | 결합 (`--similar "..." --path-prefix ...`) |
| 특정 세션 | `episodic-db inspect <session_id>` |

### Facet 검색이 유리한 경우

- 특정 디렉토리의 에피소드만 보고 싶을 때
- 특정 waste_type을 모두 열거하고 싶을 때
- 정확한 필터링이 필요할 때

### 벡터 검색이 유리한 경우

- "비슷한 문제를 다뤘던 사례"를 찾고 싶을 때
- 경로명이 다르지만 의미적으로 유사한 에피소드를 찾을 때
- "auth token 만료 문제"처럼 자연어로 검색하고 싶을 때

---

## 프로그래밍 인터페이스

CLI 외에 Python에서 직접 사용:

```python
from episodic_db.config import Config, EmbeddingConfig
from episodic_db.store.db import Database
from episodic_db.query.facet_search import search_episodes, get_episode_detail
from episodic_db.query.vector_search import search_similar

# DB 연결
db = Database(Config().db_path)
db.connect()

# Facet 검색
results = search_episodes(db, path_prefix="auth/", waste_type="read-heavy", limit=10)

# 벡터 검색
results = search_similar(db, "auth token bug", config=EmbeddingConfig(), limit=5)

# 에피소드 상세
detail = get_episode_detail(db, "ep_a1b2c3d4")
print(detail["members"])  # 소속 tool_call 목록
```

---

## Embedding 관리

### 수동 임베딩 생성

```bash
# 특정 세션의 에피소드만
episodic-db embed --session <session_id>

# 임베딩 없는 전체 에피소드
episodic-db embed --all
```

### Embedding Indexer (`embedding/indexer.py`)

`EpisodeIndexer.embed_episodes()`:
1. `embedding IS NULL`인 에피소드 조회
2. 각 에피소드의 `wasted_member_ids`에 해당하는 tool_call 조회
3. `serialize_signature(episode, tool_calls)` → 텍스트
4. Batch embed (100개씩)
5. DB 업데이트

### 임베딩 텍스트 예시

```
futile-exploration | converged | python | auth/ | conv=auth/session.py | grep(login,authenticate,expiry) | changed(session) | err(AssertionError:token-expiry) | actions(Read auth/models.py; Bash: grep -r "login" .)
```
