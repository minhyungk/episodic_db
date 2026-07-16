# 캡처 시스템 상세

Episodic DB의 데이터 수집 계층. 두 채널로 데이터를 수집한다:
1. **Hook Handler** — Claude Code의 hook 이벤트를 stdin으로 수신
2. **API Proxy** — LLM API 트래픽을 중계하며 token 사용량 캡처

---

## Hook Handler (`capture/hook_handler.py`)

Claude Code는 특정 시점에 등록된 command를 subprocess로 실행하고, stdin에 JSON payload를 전달한다.

### 이벤트 종류

| Hook Event | Timeout | 발동 시점 | 처리 내용 |
|-----------|---------|-----------|-----------|
| `SessionStart` | 5초 | Claude Code 세션 시작 | 세션 생성 + 환경 스냅샷 |
| `PreToolUse` | 5초 | 도구 실행 직전 | tool_call 삽입 + resource/edge 생성 |
| `PostToolUse` | 5초 | 도구 실행 직후 | result 저장 (inline or blob) |
| `SessionEnd` | 30초 | 세션 종료 | 라벨링 + 에피소드 조립 파이프라인 |
| `Stop` | 5초 | 강제 종료 | 세션 정상 종료 처리 |

### Hook 활성화 조건

환경변수 `EPISODIC_DB_ACTIVE`가 설정되어 있어야 hook_handler가 실행된다. 미설정 시 즉시 `sys.exit(0)`.

```bash
EPISODIC_DB_ACTIVE=1        # hook 활성화
EPISODIC_DB_PATH=/path.db   # DB 파일 경로
EPISODIC_DB_SESSION_ID=...  # (벤치마크 모드) 세션 ID 오버라이드
```

### PreToolUse 처리 상세

```python
def handle_pre_tool_use(payload, db, config):
    # 1. seq 번호 부여 (세션 내 순서)
    seq = MAX(seq) + 1 FROM tool_calls WHERE session_id = ?

    # 2. input_hash 생성 (중복 감지용)
    input_hash = SHA256(json.dumps(tool_input, sort_keys=True))[:16]

    # 3. normalized_input 생성 (사람이 읽을 수 있는 요약)
    #    Read/Write/Edit → "Edit /path/to/file.py"
    #    Bash → "Bash: git status..."
    #    Grep → "Grep: pattern"

    # 4. tool_call 레코드 삽입

    # 5. resource + edge 생성
    #    Write/Edit → mode = "WROTE"
    #    그 외 → mode = "READ"
    #    resource_id = "path:/full/path/to/file.py"
```

### PostToolUse 처리 상세

```python
def handle_post_tool_use(payload, db, config):
    # 1. 결과 텍스트 추출 (tool_response / tool_result / tool_output)

    # 2. 크기 판단
    if len(output) > 4000 chars:
        → blob_store에 gzip 저장, digest_handle 반환
    else:
        → inline_content로 DB에 직접 저장

    # 3. result_hash = SHA256(output)[:16]

    # 4. results 테이블에 삽입
```

### SessionEnd 처리

SessionEnd는 30초 timeout을 가지며, 전체 라벨링 + 에피소드 조립 파이프라인을 실행한다. 상세는 [episode_pipeline.md](./episode_pipeline.md) 참조.

---

## API Proxy

Claude Code의 API 호출을 중계하면서 토큰 사용량, 모델명, tool_use_id를 캡처한다.

### Direct 모드 (`proxy/server.py`)

Anthropic API(`https://api.anthropic.com`)로의 요청을 중계.

```
Claude Code → http://127.0.0.1:8080/v1/messages → Anthropic API
                      ↓ (중계하면서 캡처)
                 proxy_calls 테이블에 기록
```

**스트리밍 처리:** SSE 이벤트를 클라이언트에 즉시 전달(passthrough)하면서, 동시에 누적하여 응답 완료 후 파싱.

### Bedrock 모드 (`proxy/bedrock.py`)

AWS Bedrock Runtime으로의 요청을 SigV4 재서명하여 중계.

```
Claude Code → http://127.0.0.1:8080/model/{id}/invoke-with-response-stream → Bedrock
                      ↓ (SigV4 재서명 + 캡처)
                 proxy_calls 테이블에 기록
```

**AWS Event Stream 디코딩:** Bedrock는 SSE가 아닌 바이너리 event-stream 프로토콜을 사용. 청크를 base64 디코딩하여 JSON 이벤트로 재구성.

### SSE Parser (`proxy/sse_parser.py`)

스트리밍 응답의 이벤트를 `ReconstructedResponse`로 합성:

```python
@dataclass
class ReconstructedResponse:
    model: str          # 사용된 모델
    message_id: str     # 메시지 ID
    stop_reason: str    # 종료 이유
    usage: dict         # {input_tokens, output_tokens, cache_*}
    content_blocks: list  # text/tool_use 블록들
    assistant_text: str   # 텍스트 블록 합산
```

### Token Bridge (`proxy/token_bridge.py`)

**실시간:** proxy_call 하나를 기록 (`log_proxy_call`)

**세션 종료 시 reconciliation:** proxy_calls의 token 데이터를 tool_calls에 분배
- `proxy_calls.tool_use_ids` → 해당 API turn에서 생성된 tool_use_id 목록
- token/cost를 tool_use_id 개수로 나누어 분배
- 세션 전체 합계를 sessions 테이블에 업데이트

### Pricing (`proxy/pricing.py`)

모델별 가격표:

| 모델 | Input ($/1M) | Output ($/1M) | Cache Write ($/1M) | Cache Read ($/1M) |
|------|-------------|--------------|-------------------|------------------|
| Opus 4.6~4.8 | $5.00 | $25.00 | $6.25 | $0.50 |
| Sonnet 4.5~4.6 | $3.00 | $15.00 | $3.75 | $0.30 |
| Haiku 4.5 | $1.00 | $5.00 | $1.25 | $0.10 |

모델명 매칭은 prefix 기반. 매칭 실패 시 Sonnet 4.6 가격 사용.

---

## Settings Factory (`capture/settings_factory.py`)

`episodic-db start` 시 Claude Code의 `.claude/settings.json`을 생성:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python -m episodic_db.capture.hook_handler", "timeout": 5}]}],
    "PreToolUse": [{"hooks": [{"type": "command", "command": "python -m episodic_db.capture.hook_handler", "timeout": 5}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "python -m episodic_db.capture.hook_handler", "timeout": 5}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "python -m episodic_db.capture.hook_handler", "timeout": 30}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "python -m episodic_db.capture.hook_handler", "timeout": 5}]}]
  }
}
```

---

## 환경 캡처 (`capture/env_capture.py`)

SessionStart 시 실행 환경의 full snapshot을 수집:

| 항목 | 수집 방법 |
|------|-----------|
| OS + version | `platform.system()` + `platform.release()` |
| Shell | `$SHELL` 환경변수 |
| Runtimes | `node --version`, `python3 --version`, `cargo --version` 등 |
| Project markers | `package.json`, `pyproject.toml`, `Cargo.toml` 등 존재 여부 |
| Git 정보 | branch, HEAD (short), dirty 여부 |
| CWD | 현재 작업 디렉토리 |

**보안:** 환경변수의 값 중 KEY/TOKEN/SECRET/PASSWORD 패턴이 있거나 엔트로피가 높은 값은 `***MASKED***`로 치환.

---

## Blob Store (`store/blob_store.py`)

4000자를 초과하는 도구 출력(pytest 로그, 큰 파일 내용 등)은 content-addressed blob으로 저장:

- 저장 위치: `~/.episodic_db/blobs/{hash[:2]}/{hash}.gz`
- 형식: gzip 압축된 UTF-8 텍스트
- 주소 지정: SHA256 해시 (content-addressed → 동일 내용은 한 번만 저장)
- DB에는 `digest_handle`(해시값)만 기록

```python
blob_store.store(content) → "a1b2c3d4..."  # SHA256 hash
blob_store.retrieve("a1b2c3d4...") → original content
```
