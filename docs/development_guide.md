# 개발 가이드

프로젝트에 기여하거나 이어서 개발하기 위한 가이드.

---

## 환경 설정

```bash
# 가상환경
python -m venv .venv
source .venv/bin/activate

# 개발 의존성 포함 설치
pip install -e ".[bench,openai]"

# 테스트 실행
python -m pytest tests/ -v
```

---

## 테스트

### 테스트 파일

| 파일 | 대상 |
|------|------|
| `tests/conftest.py` | 공통 fixture (tmp DB, config) |
| `tests/test_hook_handler.py` | Hook 이벤트 처리 |
| `tests/test_embedding.py` | 임베딩 직렬화 + 검색 |
| `tests/test_integration.py` | 전체 파이프라인 통합 테스트 |
| `tests/test_pretooluse_retrieval.py` | PreToolUse 검색 시나리오 |

### 테스트 fixture

```python
@pytest.fixture
def db(config):
    """임시 SQLite DB (메모리가 아닌 tmpdir에 생성)"""
    database = Database(config.db_path)
    database.connect()
    yield database
    database.close()
```

### 테스트 실행

```bash
# 전체
python -m pytest tests/ -v

# 특정 모듈
python -m pytest tests/test_hook_handler.py -v

# 특정 테스트
python -m pytest tests/test_integration.py::test_full_pipeline -v
```

---

## 코드 구조 규칙

### 모듈 간 의존 방향

```
cli.py
  ↓
capture/  ←→  proxy/
  ↓              ↓
store/  (공유 데이터 계층)
  ↑
label/  →  episode/  →  embedding/  →  query/
```

- `store/`는 최하위 계층 — 다른 모듈을 import하지 않음
- `label/`과 `episode/`는 `store/`만 의존
- `embedding/`은 `store/`와 자체 하위 모듈만 의존
- `capture/`와 `proxy/`는 `store/` 의존 + `label/`, `episode/` 호출 (SessionEnd 시)

### 결정론 경계

**Episodic DB 내부에 LLM 호출이 들어가면 안 된다.** 모든 연산은:
- 해시 비교 (SHA256)
- 그래프 순회 (SQL JOIN)
- Threshold if-else
- 수학 연산 (합산, 비율, 가중합)
- 문자열 파싱 (정규식)

유일한 예외: 임베딩 생성 (sentence-transformers 로컬 모델). 이것도 API LLM이 아닌 로컬 추론.

---

## 주요 확장 포인트

### 새 waste_type 추가

1. `config.py`의 `WasteThresholds`에 threshold 추가
2. `episode/waste_classifier.py`의 `classify_waste_type()`에 판정 조건 추가
3. 판정 우선순위 위치 결정 (기존 분류와의 상호작용 고려)
4. `docs/waste_types.md` 업데이트

### 새 signature facet 추가

1. `store/schema.py`의 `episodes` 테이블에 컬럼 추가
2. `episode/signature.py`의 `extract_signature()`에 추출 로직 추가
3. `store/nodes.py`의 `insert_episode()`에 컬럼 추가
4. `embedding/serializer.py`의 직렬화에 포함 여부 결정
5. `query/facet_search.py`에 필터 옵션 추가

### 새 Hook 이벤트 지원

1. `capture/settings_factory.py`의 `HOOK_EVENTS`에 추가
2. `capture/hook_handler.py`에 핸들러 함수 추가 + `handlers` dict에 등록

### 새 프록시 백엔드 추가

1. `proxy/` 아래에 새 서버 모듈 작성 (`proxy/new_backend.py`)
2. `cli.py`의 `start` 명령에서 모드 분기 추가
3. `config.py`의 `proxy_mode` 옵션에 새 값 추가

---

## DB 마이그레이션

`store/db.py`의 `_migrate()` 메서드에서 버전별 마이그레이션 실행:

```python
def _migrate(self, conn):
    current = get_current_version()
    if current < 2:
        conn.execute("ALTER TABLE proxy_calls ADD COLUMN assistant_text TEXT")
        update_version(2)
    if current < 3:
        conn.execute("ALTER TABLE proxy_calls ADD COLUMN user_message TEXT")
        update_version(3)
    if current < 4:
        conn.execute("ALTER TABLE tool_calls ADD COLUMN tool_input_json TEXT")
        update_version(4)
```

새 컬럼 추가 시:
1. `SCHEMA_VERSION` 증가
2. `TABLES` DDL에 새 컬럼 추가 (새 DB 생성 시)
3. `_migrate()`에 `ALTER TABLE` 추가 (기존 DB 업그레이드 시)

---

## 디버깅 팁

### Hook이 동작 안 할 때

1. `EPISODIC_DB_ACTIVE=1`이 설정되어 있는지 확인
2. `EPISODIC_DB_PATH`가 유효한 경로인지 확인
3. `.claude/settings.json`이 올바른 위치에 있는지 확인
4. `EPISODIC_DB_DEBUG_LOG` 설정 후 로그 확인

### Proxy가 연결 안 될 때

1. 포트가 사용 가능한지 확인: `lsof -i :8080`
2. `ANTHROPIC_BASE_URL`이 올바른지 확인
3. Bedrock 모드에서는 AWS credentials 확인

### 에피소드가 생성 안 될 때

1. `episodic-db status`로 tool_calls 수 확인
2. SessionEnd hook이 정상 발동되었는지 확인 (debug log)
3. 30초 timeout 내에 파이프라인이 완료되는지 확인

### 임베딩이 생성 안 될 때

1. `sentence-transformers` 패키지 설치 확인
2. 모델 다운로드 여부 확인 (첫 실행 시 다운로드 필요)
3. 수동 재생성: `episodic-db embed --all`

---

## 주요 상수 / 매직 넘버

| 값 | 위치 | 의미 |
|----|------|------|
| 4000 chars | `Config.blob_inline_max_chars` | 이 이상이면 blob 저장 |
| 16 chars | `_hash_input()` | SHA256 해시 길이 (hex) |
| 5초 / 30초 | `settings_factory.py` | Hook timeout |
| 20개 | `signature.py` | grep_terms, changed_symbols 최대 개수 |
| 200자 | `signature.py` | error_signature 최대 길이 |
| 100개 | `indexer.py` | 임베딩 배치 크기 |
| 50 | `metrics.py` | futility_score 정규화 기준 call 수 |

---

## 미구현 / TODO

현재 명세에는 있지만 구현이 완료되지 않은 항목:

1. **context_snowball waste_type** — threshold는 정의되어 있으나 classifier에서 미판정
2. **Session-level outcome과 episode-level outcome 분리** — 현재 둘 다 있으나 상호 독립적
3. **Bi-temporal resource validity** — `valid_from`/`valid_to` 필드는 있으나 쿼리에서 활용 안 됨
4. **ANN 인덱스** — 명세에 HNSW 언급되나 현재 brute-force
5. **test_names facet** — `test:` prefix resource가 생성되는 경로가 hook에 없음 (수동 등록 필요)
6. **repo별 threshold 튜닝** — 분위수 통계 기반 자동 조정 미구현

---

## 패키지 빌드 / 배포

```bash
# 빌드
pip install build
python -m build

# 설치 확인
pip install dist/episodic_db-0.1.0-py3-none-any.whl
episodic-db --help
```

진입점 (`pyproject.toml`):
- `episodic-db` → `episodic_db.cli:main`
- `episodic-bench` → `episodic_db.bench.runner:main`
