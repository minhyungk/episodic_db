# Episodic DB

Claude Code agent의 활동을 기록하고, 세션 종료 후 결정론적으로 분석하여 **낭비 에피소드**를 식별하는 메모리 시스템.

## 개요

```
Agent 작업 → Hook 캡처 → SQLite 저장 → 라벨링(patch 역추적) → Episode 조립 → Embedding
```

- **Episodic DB**는 "언제 뭘 했나"의 사건 기록 (결정론, LLM 없음)
- 세션 종료 후 낭비 구간을 Episode로 묶고, facet 기반 검색 + 벡터 유사도 검색 제공
- Claude Code plugin으로 동작 (hooks + API proxy)

## 설치

```bash
pip install -e .
```

**요구사항:** Python 3.11+, macOS / Windows / Linux

## 사용법

### 시작

```bash
# Proxy 시작 + Hook 등록
episodic-db start --port 8080 --project-dir /path/to/project

# Bedrock 모드
episodic-db start --port 8080 --bedrock --project-dir /path/to/project
```

시작하면 `.claude/settings.json`에 훅이 등록되고, API proxy가 실행됩니다.
Claude Code를 해당 프로젝트에서 실행하면 자동으로 캡처됩니다.

### 상태 확인

```bash
episodic-db status
```

### 세션 검사

```bash
episodic-db inspect <session_id>
```

### Episode 검색

```bash
# Facet 기반 검색
episodic-db query --path-prefix auth/ --waste-type futile-exploration

# 벡터 유사도 검색 (OPENAI_API_KEY 필요)
episodic-db query --similar "auth token expiry bug" --limit 5
```

### Embedding 생성

```bash
# 특정 세션
episodic-db embed --session <session_id>

# 전체 미생성 episode
episodic-db embed --all
```

### 중지

```bash
episodic-db stop
```

## 아키텍처

```
┌─────────────────────────────────────────────────┐
│  Claude Code Session                            │
│                                                 │
│  Hook Events ──► capture/hook_handler.py        │
│  API Traffic ──► proxy/server.py                │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  store/ — SQLite (WAL mode)                     │
│  Sessions, ToolCalls, Resources, Results, Edges │
└───────────────────────┬─────────────────────────┘
                        │ SessionEnd trigger
                        ▼
┌─────────────────────────────────────────────────┐
│  label/ — 결정론 라벨링                          │
│  • contribution.py: patch 역추적                 │
│  • waste_signals.py: 중복/반복 감지              │
│  • outcome.py: converged/abandoned/looped        │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  episode/ — Episode 조립                         │
│  • assembler.py: NEXT 체인 segmentation          │
│  • signature.py: facet 추출                      │
│  • waste_classifier.py: threshold 분류           │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  embedding/ — 벡터 검색                          │
│  • serializer.py: facet → text                   │
│  • embedder.py: OpenAI API (pluggable)           │
│  • indexer.py: numpy cosine similarity           │
└─────────────────────────────────────────────────┘
```

## 핵심 설계 원칙

1. **LLM 없음** — Episodic 내부의 모든 연산은 결정론 (그래프 순회, 해시 비교, threshold 분류)
2. **중복 보존** — 같은 파일을 3번 읽으면 노드 3개. "3번 읽었다"는 사실 자체가 낭비 신호
3. **Facet 검색** — task_type 같은 추측 라벨 대신, 관측된 구조적 값(경로, grep 쿼리, 에러)으로 검색
4. **크로스플랫폼** — SQLite + numpy만 사용, 네이티브 확장 의존 없음

## 낭비 유형 (waste_type)

| 유형 | 설명 |
|------|------|
| `futile-exploration` | 오래 탐색했지만 새 정보 없음 |
| `read-heavy` | 읽기 비율이 60%+ |
| `context-snowball` | context 크기가 연속 증가 |
| `repeated-loop` | 동일 명령 반복 실행 |
| `expensive-failure` | 같은 실패 반복 |

## 개발

```bash
# 테스트 실행
python -m pytest tests/ -v

# 패키지 재설치
pip install -e .
```
