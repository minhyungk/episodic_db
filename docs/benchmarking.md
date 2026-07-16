# 벤치마크 시스템

Episodic DB의 캡처/분석 기능을 SWE-bench 태스크로 검증하는 시스템. Claude Code를 서브프로세스로 실행하고 hook + proxy로 전체 세션을 캡처한다.

---

## 구조

```
bench/
├── base.py         # Benchmark 추상 인터페이스
├── runner.py       # CLI 진입점 + 오케스트레이터
├── executor.py     # Claude Code 서브프로세스 관리
└── external/
    └── swebench/   # SWE-bench 벤치마크 로더
        ├── __init__.py
        ├── benchmark.py  # SWEBenchmark 구현
        └── loader.py     # HuggingFace datasets에서 로드
```

---

## 사용법

```bash
# 기본 실행 (Direct API)
episodic-bench --port 8080 --log-dir logs/ --limit 5

# Bedrock 모드
episodic-bench --port 8080 --bedrock --model us.anthropic.claude-sonnet-4-5-20250514-v1:0

# 특정 인스턴스만
episodic-bench --swebench-ids "django__django-11039,astropy__astropy-12907"

# 커스텀 DB 경로
episodic-bench --db /tmp/bench.db --log-dir /tmp/bench_logs/
```

### CLI 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--port` | 8080 | Proxy 포트 |
| `--bedrock` | - | Bedrock 프록시 모드 |
| `--model` | None | Claude 모델 오버라이드 |
| `--log-dir` | `logs/` | 로그 출력 디렉토리 |
| `--db` | None | SQLite DB 경로 (기본: ~/.episodic_db/episodic.db) |
| `--limit` | None | 실행할 벤치마크 수 제한 |
| `--swebench-ids` | None | 콤마 구분 SWE-bench instance ID |

---

## 실행 흐름

```
1. Proxy 서버 시작
2. SWE-bench 데이터셋에서 벤치마크 목록 로드
3. 각 벤치마크에 대해:
   a. 고유 session_id 생성 (benchmark_name + random_8hex)
   b. Proxy에 session_id 설정 (POST /control/set-session)
   c. Workspace 디렉토리 생성 + 벤치마크 setup
   d. .claude/settings.json 생성 (hooks 등록)
   e. Claude Code 서브프로세스 실행
   f. stdout/stderr 저장
   g. 벤치마크 score (있으면)
   h. 캡처 결과 리포트
4. 최종 요약 출력
5. bench_report.json 저장
```

---

## Benchmark 인터페이스

```python
class Benchmark(ABC):
    @property
    def name(self) -> str: ...           # 벤치마크 식별자
    def get_prompt(self) -> str: ...     # Claude Code에 전달할 프롬프트
    def get_claude_args(self) -> list[str]: ...  # 추가 CLI 인수
    def get_working_directory(self) -> Path | None: ...  # 작업 디렉토리
    def setup(self, workspace_base: Path): ...  # 환경 준비 (git clone 등)
    def score(self, session_dir: Path) -> float | None: ...  # 결과 채점
    def cleanup(self): ...               # 정리
```

---

## BenchmarkExecutor 상세

### Claude Code 실행 명령

```bash
claude --print --no-session-persistence --dangerously-skip-permissions \
       --output-format text --settings /path/.claude/settings.json \
       [--model model_id] \
       -p "벤치마크 프롬프트"
```

| 플래그 | 이유 |
|--------|------|
| `--print` | 비대화형 모드 (stdout 출력) |
| `--no-session-persistence` | 세션 상태 저장하지 않음 |
| `--dangerously-skip-permissions` | 벤치마크 자동화를 위해 권한 확인 스킵 |
| `--output-format text` | JSON 대신 텍스트 출력 |
| `--settings` | 생성한 settings.json 사용 |

### 환경변수

```bash
EPISODIC_DB_ACTIVE=1                    # Hook 활성화
EPISODIC_DB_PATH=/path/to/bench.db      # DB 경로
EPISODIC_DB_SESSION_ID=django__..._abc  # 세션 ID 오버라이드
EPISODIC_DB_DEBUG_LOG=/logs/hook_debug.log

# Direct 모드
ANTHROPIC_BASE_URL=http://127.0.0.1:8080
ANTHROPIC_API_KEY=sk-...                # .env에서 로드 가능

# Bedrock 모드
ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8080
CLAUDE_CODE_USE_BEDROCK=1
```

---

## SWE-bench 벤치마크

### 데이터 소스

HuggingFace `datasets` 라이브러리로 SWE-bench 데이터셋을 로드.

### 벤치마크 인스턴스 구조

각 SWE-bench 인스턴스:
- `instance_id`: 예) `django__django-11039`
- `repo`: 예) `django/django`
- `base_commit`: 시작 커밋
- `problem_statement`: 문제 설명 (Claude Code 프롬프트로 사용)
- `patch`: 정답 패치 (채점용)
- `test_patch`: 검증 테스트

---

## 출력물

### 디렉토리 구조

```
logs/
├── session_{benchmark_name}_{id}/
│   ├── stdout.txt          # Claude Code stdout
│   ├── stderr.txt          # Claude Code stderr (있으면)
│   └── workspace/          # 작업 디렉토리 스냅샷
├── hook_debug.log          # Hook 디버그 로그
└── bench_report.json       # 벤치마크 결과 요약
```

### bench_report.json

```json
{
  "results": [
    {
      "session_id": "django__django-11039_a1b2c3d4",
      "benchmark": "swebench-django__django-11039",
      "tool_calls": 45,
      "episodes": 3,
      "metadata": {
        "exit_code": 0,
        "stdout_len": 12340,
        "stderr_len": 0
      }
    }
  ],
  "db_path": "/Users/user/.episodic_db/episodic.db"
}
```

---

## 결과 분석

벤치마크 실행 후:

```bash
# 특정 세션의 에피소드 확인
episodic-db inspect django__django-11039_a1b2c3d4

# 반복 루프 에피소드 찾기
episodic-db query --waste-type repeated-loop

# 비용 높은 낭비 찾기
episodic-db query --waste-type expensive-failure
```

---

## 커스텀 벤치마크 추가

`Benchmark` 클래스를 상속하여 새 벤치마크를 추가:

```python
from episodic_db.bench.base import Benchmark

class MyBenchmark(Benchmark):
    @property
    def name(self) -> str:
        return "my-custom-task"

    def get_prompt(self) -> str:
        return "Fix the bug in auth/session.py where tokens expire incorrectly"

    def setup(self, workspace_base):
        # git clone, 환경 준비 등
        pass

    def get_working_directory(self):
        return self._work_dir
```

`runner.py`의 벤치마크 discover 로직에 등록하면 된다.
