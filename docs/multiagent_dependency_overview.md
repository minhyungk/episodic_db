# Multi-Agent Dependency Tracking — 검증 보고

## 한 줄 요약

AI 에이전트 여러 명이 동시에 일할 때, 하나가 사고 나면 **"왜?"를 에이전트 경계를 넘어 자동 역추적**할 수 있는지 시뮬레이션으로 검증했고, **가능하다.**

---

## 검증한 시나리오

### 시나리오 1: 함수 시그니처 충돌

메인 에이전트가 인증 버그를 고치려고 3명을 동시에 띄운 상황.

**배경:**

```
메인 에이전트: "인증 토큰 만료 버그 수정해"
  ├── 에이전트 A: "session.py의 check_expiry 함수를 고쳐"
  ├── 에이전트 B: "middleware.py의 검증 로직을 업데이트해"
  └── 에이전트 C: "다 되면 auth 테스트 돌려"
```

**벌어진 일 (시간순):**

```
t=0s  에이전트 A: session.py를 읽음
t=0s  에이전트 B: session.py를 읽음 ← 이 시점에 A의 수정은 아직 안 됨
t=2s  에이전트 A: session.py를 수정 — check_expiry(token, strict=True) 로 파라미터 추가
t=3s  에이전트 B: middleware.py를 작성 — check_expiry(token) 으로 호출 (옛날 시그니처)
t=5s  에이전트 C: pytest 실행 → 💥 TypeError: check_expiry() got unexpected argument 'strict'
```

에이전트 C 입장에서는 그냥 "테스트 실패"만 보인다. 본인이 잘못한 건 아무것도 없다.

**역추적 결과:**

```
★ 에이전트 C: pytest tests/test_auth.py → 실패
  │
  ├─ 이 테스트가 import하는 auth/session.py를 누가 마지막으로 수정했나?
  │   └─ 에이전트 A가 Edit (시그니처 변경)
  │
  └─ 이 테스트가 import하는 auth/middleware.py를 누가 마지막으로 수정했나?
      └─ 에이전트 B가 Edit (옛날 시그니처 기준으로 작성)
          │
          └─ 에이전트 B가 session.py를 읽은 시점 — 에이전트 A의 수정 이전 (stale!)

결론: A가 시그니처를 바꿨는데 B가 바뀌기 전 버전을 보고 코드를 짬.
     → A와 B의 작업을 동기화해야 했음.
```

---

### 시나리오 2: 3단계 도미노 (Cascade Failure)

설정 파일 하나가 깨지면서 연쇄적으로 3개 에이전트가 실패하는 상황.

**배경:**

```
메인 에이전트: "서버 리팩토링 + 테스트"
  ├── 에이전트 D: "config.py를 정리해"
  ├── 에이전트 E: "개발 서버 띄워"
  └── 에이전트 F: "API 통합 테스트 돌려"
```

**벌어진 일:**

```
t=0s  에이전트 D: config.py 리팩토링 중 PORT 변수를 실수로 삭제
t=3s  에이전트 E: python src/server.py 실행
      → "NameError: name 'PORT' is not defined" — 서버 안 뜸
t=5s  에이전트 F: curl http://localhost:8000/api/health
      → "Connection refused" — 테스트 전면 실패
```

에이전트 F 입장에서는 "서버가 안 떠있네?" 밖에 모른다.
에이전트 E 입장에서는 "config에서 에러 나네?" 밖에 모른다.
진짜 원인은 에이전트 D인데, D는 이미 작업을 "완료"했다고 생각하고 있다.

**역추적 결과:**

```
★ 에이전트 F: curl localhost:8000 → Connection refused
  │
  └─ 에이전트 F는 서버가 떠있어야 하는데, 서버를 시작한 에이전트는?
      └─ 에이전트 E: python src/server.py → NameError로 실패
          │
          └─ 에이전트 E가 읽은 config.py를 마지막으로 수정한 에이전트는?
              └─ 에이전트 D: Edit src/config.py (PORT 변수 삭제)

결론: D의 config 수정 실수 → E 서버 실패 → F 테스트 실패.
     ROOT CAUSE = 에이전트 D의 config.py 수정.
```

---

## 시뮬레이션 방법

### 뭘 만들었는지

현재 Episodic DB가 이미 저장하는 데이터:
- 어떤 에이전트가 어떤 파일을 **읽었는지** (edges_touches, mode=READ)
- 어떤 에이전트가 어떤 파일을 **수정했는지** (edges_touches, mode=WROTE)
- 각 작업의 **시간**, **결과**, **에러 여부**

여기에 3개만 추가:
- **agents**: 에이전트 목록 (누가 누구를 만들었는지)
- **agent_dependencies**: 에이전트 간 관계 (A가 쓴 파일을 B가 읽음, 등)
- **incidents**: 사고 발생 지점 표시

### 어떻게 검증했는지

`experiments/multiagent_dependency_sim.py` 스크립트로:

1. **임시 DB 생성** — 실제 Episodic DB와 동일한 테이블 + 확장 테이블 3개
2. **시나리오 데이터 삽입** — 위 두 시나리오의 에이전트, tool call, 파일 접근, 에러를 실제 DB에 기록
3. **Backtracking 알고리즘 실행** — 사고 지점에서 시작해서 역방향으로 원인 추적
4. **결과 확인** — root cause까지 정확히 도달하는지 검증

### 실행 결과 (실제 출력)

```
$ python experiments/multiagent_dependency_sim.py

======================================================================
Multi-Agent Dependency Backtracking Simulation
======================================================================

──────────────────────────────────────────────────────────────────────
SCENARIO 1: File Conflict → Test Failure
──────────────────────────────────────────────────────────────────────

Backtrack 결과:
★ [agent-c] Bash: Bash: pytest tests/test_auth.py
  at 2026-07-16T22:31:45
  → [file_handoff] → path:auth/session.py (file_handoff)
    at 2026-07-16T22:31:45
  → [file_handoff] → path:auth/middleware.py (file_handoff)
    at 2026-07-16T22:31:45
  → [agent-a] Edit: Edit auth/session.py        ← ROOT CAUSE 도달
    at 2026-07-16T22:31:45
  → [agent-b] Edit: Edit auth/middleware.py      ← 연관 원인 도달
    at 2026-07-16T22:31:45

──────────────────────────────────────────────────────────────────────
SCENARIO 2: Cascade Failure (3-hop dependency chain)
──────────────────────────────────────────────────────────────────────

Backtrack 결과:
★ [agent-f] Bash: Bash: curl http://localhost:8000/api/health
  at 2026-07-16T22:31:45
  → [blocks] →  (blocks)
    at 2026-07-16T22:31:45
  → [agent-e] Bash: Bash: python src/server.py   ← 중간 원인
    at 2026-07-16T22:31:45
    → [file_handoff] → path:src/config.py (file_handoff)
      at 2026-07-16T22:31:45
    → [agent-d] Edit: Edit src/config.py         ← ROOT CAUSE 도달
      at 2026-07-16T22:31:45
```

두 시나리오 모두 **root cause 에이전트 + 원인 행동까지 자동 도달** 확인.

### 알고리즘이 하는 것 (비유)

형사가 사건 현장에서 출발해서 단서를 따라가는 것과 같다:

```
사건 현장 (에이전트 C 테스트 실패)
  → "이 테스트가 쓰는 파일들을 누가 마지막으로 건드렸지?"
    → session.py — 에이전트 A가 수정
    → middleware.py — 에이전트 B가 수정
      → "B가 작업할 때 참고한 정보는?"
        → session.py를 읽었는데, A가 수정하기 전 버전
          → "이게 원인이네" ← 추적 종료
```

이걸 사람이 하면 로그 3개를 왔다갔다 하면서 시간대를 맞춰봐야 하는데, 시스템이 파일 접근 기록을 다 갖고 있으니 자동으로 할 수 있다.

---

## 기존 시스템과의 관계

```
기존 Episodic DB (이미 있음)          추가 부분 (미래)
────────────────────────────          ──────────────────
✓ 에이전트별 모든 행동 기록            에이전트 간 관계 기록
✓ 어떤 파일을 읽고/썼는지 기록         → 이걸로 cross-agent 추적
✓ 에러 발생 여부 기록                 사고 시 자동 역추적
✓ 낭비 구간 식별                     원인 에이전트 식별
```

핵심: **기존 데이터("누가 어떤 파일을 언제 읽고 썼는지")가 이미 있으므로**, 에이전트 간 관계를 연결하는 레이어만 얹으면 된다. 데이터를 새로 쌓는 게 아님.

---

## 알려진 한계

| 한계 | 설명 |
|------|------|
| 실시간 경고 불가 | "지금 충돌 날 것 같다"는 예측 못 함. 사후 분석만 가능 |
| False positive | 같은 파일이라도 서로 다른 부분을 건드린 경우 실제로는 충돌 아닐 수 있음 |
| 순환 의존 | A→B→C→A 루프면 단일 원인이 없음 (관련 전체를 보여주는 것으로 대응) |

---

## 정리

| 질문 | 답 |
|------|-----|
| 구현 가능한가? | **예.** 시뮬레이션으로 검증 완료 |
| 기존 기능에 영향 있나? | **없음.** 별도 테이블, on/off 토글 |
| 실제로 원인 찾아지나? | **찾아짐.** 2개 시나리오에서 모두 root cause 도달 |
| 몇 단계까지 추적되나? | **3단계 이상.** cascade failure(D→E→F) 추적 확인 |
| 뭐가 더 필요한가? | 실제 Claude Code 멀티에이전트 세션에서의 실전 테스트 |
