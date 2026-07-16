# 시작 가이드

## 요구사항

- Python 3.11+
- macOS / Windows / Linux
- Claude Code CLI (`claude` 명령어)

---

## 설치

```bash
# 프로젝트 클론
git clone <repo-url>
cd episodic_db-1

# 가상환경 생성 + 설치
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
```

선택적 의존성:
```bash
pip install -e ".[openai]"    # OpenAI embedding 사용 시
pip install -e ".[bench]"     # SWE-bench 벤치마크 실행 시
```

---

## 기본 사용법

### 1. Episodic DB 시작

```bash
# 기본 모드 (Direct API proxy)
episodic-db start --port 8080 --project-dir /path/to/your/project

# Bedrock 모드 (AWS Bedrock 경유)
episodic-db start --port 8080 --bedrock --project-dir /path/to/your/project
```

시작하면 다음이 자동으로 수행된다:
1. `~/.episodic_db/episodic.db` SQLite 데이터베이스 생성
2. `/path/to/your/project/.claude/settings.json`에 hook 등록
3. `127.0.0.1:8080`에서 API 프록시 시작

**중요:** `episodic-db start` 후 반드시 환경변수를 설정해야 한다:

```bash
# Direct 모드
export ANTHROPIC_BASE_URL=http://127.0.0.1:8080
export EPISODIC_DB_ACTIVE=1
export EPISODIC_DB_PATH=~/.episodic_db/episodic.db

# Bedrock 모드
export ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8080
export EPISODIC_DB_ACTIVE=1
export EPISODIC_DB_PATH=~/.episodic_db/episodic.db
```

### 2. Claude Code 실행

같은 터미널(환경변수가 설정된)에서 Claude Code를 실행하면 자동으로 캡처된다:

```bash
cd /path/to/your/project
claude  # 평소처럼 사용
```

### 3. 결과 확인

```bash
# DB 상태 확인
episodic-db status

# 특정 세션 상세 보기
episodic-db inspect <session_id>

# 에피소드 검색
episodic-db query --waste-type futile-exploration
episodic-db query --path-prefix auth/ --outcome converged
episodic-db query --similar "authentication token bug" --limit 5
```

### 4. 종료

```bash
episodic-db stop
```

또는 `episodic-db start`를 실행한 터미널에서 Ctrl+C.

---

## 동작 흐름 (타임라인)

```
[사용자] episodic-db start
    → Proxy 시작 (127.0.0.1:8080)
    → .claude/settings.json에 hook 등록

[사용자] claude (프로젝트에서 Claude Code 시작)
    ├── SessionStart hook → sessions 테이블에 기록
    │
    ├── 에이전트가 도구 사용할 때마다:
    │   ├── PreToolUse hook → tool_calls + resources + edges 기록
    │   └── PostToolUse hook → results 기록
    │
    ├── API 호출마다:
    │   └── Proxy가 token 사용량 캡처 → proxy_calls 기록
    │
    └── 세션 종료 시:
        └── SessionEnd hook 발동 (30초 timeout)
            ├── Token reconciliation (proxy_calls → tool_calls 매핑)
            ├── Contribution marking (patch reachability)
            ├── Waste signal detection (중복/반복)
            ├── Outcome classification (converged/looped/abandoned)
            ├── Episode segmentation (구간 분할)
            ├── Signature extraction (facet 추출)
            ├── Waste classification (threshold 판정)
            └── Embedding (벡터 생성 + DB 저장)
```

---

## 데이터베이스 위치

기본: `~/.episodic_db/episodic.db`

변경: `episodic-db --db /custom/path/my.db start ...`

---

## 디버깅

디버그 로그 활성화:

```bash
export EPISODIC_DB_DEBUG_LOG=/tmp/episodic_debug.log
```

이 환경변수를 설정하면 hook_handler가 수신한 이벤트와 처리 결과를 파일에 기록한다.

---

## 벤치마크 실행

SWE-bench 벤치마크로 Episodic DB의 캡처/분석 기능을 검증:

```bash
# ANTHROPIC_API_KEY 또는 AWS credentials가 설정된 상태에서
episodic-bench --port 8080 --log-dir logs/ --limit 5

# 특정 SWE-bench 인스턴스
episodic-bench --swebench-ids "django__django-11039,django__django-11179"

# Bedrock 모드
episodic-bench --port 8080 --bedrock --model us.anthropic.claude-sonnet-4-5-20250514-v1:0
```

벤치마크가 끝나면 `logs/bench_report.json`에 결과 요약이 저장되고, 각 세션을 `episodic-db inspect`로 검사할 수 있다.

---

## 설정 옵션

`Config` dataclass (`src/episodic_db/config.py`):

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `db_path` | `~/.episodic_db/episodic.db` | SQLite DB 파일 경로 |
| `blob_dir` | `~/.episodic_db/blobs` | 대형 출력 blob 저장 디렉토리 |
| `proxy_port` | 8080 | 프록시 서버 포트 |
| `proxy_mode` | `"direct"` | `"direct"` 또는 `"bedrock"` |
| `blob_inline_max_chars` | 4000 | 이 크기 이하면 inline 저장, 초과시 blob |
| `embedding.model` | `"BAAI/bge-small-en-v1.5"` | 임베딩 모델 |
| `embedding.dim` | 384 | 벡터 차원 |
| `embedding.backend` | `"local"` | `"local"` 또는 `"openai"` |

Waste threshold 설정은 [waste_types.md](./waste_types.md) 참조.
