# Episodic DB 아키텍처 개요

## 한 문장 요약

Claude Code 에이전트가 작업할 때 **모든 도구 호출을 기록**하고, 세션 종료 후 결정론적 분석으로 **낭비 에피소드를 식별**하여 검색 가능한 메모리를 구축하는 시스템.

---

## 시스템 위치

```
┌────────────────────────────────────────────────────────────────────┐
│  Claude Code (에이전트)                                             │
│  - Read, Write, Edit, Bash, Grep 등의 도구 호출                     │
└───────┬──────────────────────────────┬─────────────────────────────┘
        │ Hook (stdin JSON)            │ API Traffic (HTTP)
        ▼                              ▼
┌───────────────────┐      ┌────────────────────────────┐
│ capture/           │      │ proxy/                      │
│ hook_handler.py    │      │ server.py / bedrock.py      │
│ (PreToolUse,       │      │ (API 중계 + token 캡처)      │
│  PostToolUse,      │      │                             │
│  SessionEnd)       │      │                             │
└────────┬──────────┘      └──────────┬─────────────────┘
         │                             │
         ▼                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  store/ — SQLite (WAL mode)                                         │
│  sessions, tool_calls, results, resources, edges_touches, episodes  │
│  proxy_calls                                                        │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ SessionEnd 트리거
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  label/ — 결정론 라벨링 (LLM 없음)                                    │
│  contribution.py → waste_signals.py → outcome.py                    │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  episode/ — 에피소드 조립 (LLM 없음)                                  │
│  assembler.py → signature.py + metrics.py + waste_classifier.py     │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  embedding/ — 벡터 검색                                              │
│  serializer.py → embedder.py → indexer.py                           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  query/ — 검색 인터페이스                                             │
│  facet_search.py (SQL 기반) + vector_search.py (cosine similarity)  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 데이터 흐름 4단계

| 단계 | 시점 | 모듈 | 설명 |
|------|------|------|------|
| ① 캡처 | 실시간 | `capture/`, `proxy/` | 도구 호출과 API 응답을 즉시 기록 |
| ② 저장 | 실시간 | `store/` | 노드/엣지 형태로 SQLite에 저장, 큰 출력은 blob |
| ③ 라벨링 | 세션 종료 후 | `label/` | contribution, waste signal, outcome 산출 |
| ④ 에피소드 조립 | 세션 종료 후 | `episode/`, `embedding/` | 구간 분할 → 시그니처 추출 → 임베딩 |

**핵심 원칙:** ①~④의 모든 단계는 LLM 없이 결정론적으로 동작한다. 해시 비교, 그래프 순회, threshold 분류만 사용.

---

## 모듈 구조

```
src/episodic_db/
├── __init__.py
├── cli.py              # CLI 진입점 (click 기반)
├── config.py           # 설정 dataclass
├── capture/            # ① 데이터 캡처
│   ├── hook_handler.py     # Claude Code hook 이벤트 처리
│   ├── settings_factory.py # .claude/settings.json 생성
│   └── env_capture.py      # 실행 환경 스냅샷
├── proxy/              # ① API 중계 + token 캡처
│   ├── server.py           # Anthropic API 프록시
│   ├── bedrock.py          # AWS Bedrock 프록시
│   ├── token_bridge.py     # proxy_calls → tool_calls token 매핑
│   ├── sse_parser.py       # SSE 스트림 재구성
│   ├── port_utils.py       # 포트 할당
│   └── pricing.py          # 모델별 비용 계산
├── store/              # ② 저장소
│   ├── db.py               # SQLite 연결 + 마이그레이션
│   ├── schema.py           # DDL 정의
│   ├── nodes.py            # CRUD (Session, ToolCall, Episode 등)
│   ├── edges.py            # 엣지 CRUD (TOUCHES, DUPLICATE_OF)
│   └── blob_store.py       # 대형 출력 gzip 저장
├── label/              # ③ 세션 후 라벨링
│   ├── pipeline.py         # 라벨링 오케스트레이터
│   ├── contribution.py     # patch reachability (기여 판정)
│   ├── waste_signals.py    # 중복/반복 감지
│   └── outcome.py          # 세션 결말 분류
├── episode/            # ④ 에피소드 조립
│   ├── assembler.py        # 구간 분할 + 조립 오케스트레이터
│   ├── signature.py        # facet 추출 (경로, grep, 에러 등)
│   ├── metrics.py          # 비용/분석 메트릭 계산
│   └── waste_classifier.py # threshold 기반 waste_type 판정
├── embedding/          # 벡터 검색
│   ├── serializer.py       # 시그니처 → 텍스트 직렬화
│   ├── embedder.py         # Local(sentence-transformers) / OpenAI
│   └── indexer.py          # brute-force cosine similarity 검색
├── query/              # 검색 인터페이스
│   ├── facet_search.py     # SQL WHERE 기반 facet 검색
│   └── vector_search.py    # 벡터 유사도 + facet 필터 결합
└── bench/              # 벤치마크 실행
    ├── base.py             # Benchmark 추상 인터페이스
    ├── runner.py           # 벤치마크 러너 (proxy + execute + report)
    ├── executor.py         # Claude Code 서브프로세스 실행
    └── external/
        └── swebench/       # SWE-bench 벤치마크 로더
```

---

## 핵심 설계 원칙

1. **LLM 없음** — Episodic DB 내부의 모든 연산은 결정론 (해시 비교, 그래프 순회, threshold)
2. **중복 보존** — 같은 파일을 3번 읽으면 노드 3개. "3번 읽었다"는 사실 자체가 낭비 신호
3. **Facet 검색** — `task_type` 같은 추측 라벨 대신, 관측된 구조적 값(경로, grep 쿼리, 에러)으로 검색
4. **크로스플랫폼** — SQLite + numpy만 사용, 네이티브 확장 의존 없음
5. **플러그인 방식** — Claude Code의 hook 시스템과 API proxy를 통해 비침습적으로 동작

---

## 외부 의존성

| 패키지 | 용도 |
|--------|------|
| `aiohttp` | API 프록시 서버 |
| `click` | CLI 인터페이스 |
| `numpy` | 벡터 연산 (cosine similarity) |
| `sentence-transformers` | 로컬 임베딩 모델 |
| `certifi` | SSL 인증서 |
| `aiofiles` | 비동기 파일 I/O |

선택적:
| 패키지 | 용도 |
|--------|------|
| `openai` | OpenAI embedding API 사용 시 |
| `datasets` | SWE-bench 벤치마크 실행 시 |
| `botocore` | Bedrock 프록시 모드 시 |

---

## Semantic DB와의 관계

Episodic DB는 **사건 기록**만 담당한다. 자연어 요약, 의미 일반화, skill.md 생성 등은 별도의 Semantic DB (lt-advisor)가 수행한다.

```
Episodic DB (이 프로젝트)     Semantic DB (별도)
──────────────────────────    ─────────────────────
사건 로그, facet 기반 검색      자연어 요약, 의미 일반화
결정론 (LLM 없음)              LLM 기반 추상화
즉시 검색 가능 (SQL)            지식 그래프, skill.md
```

Episodic DB의 에피소드 데이터는 Semantic DB의 입력 재료가 되며, Semantic DB가 생성한 skill.md가 다음 에이전트 실행에 조언으로 제공된다.
