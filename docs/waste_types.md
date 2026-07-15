# Waste Types

에피소드의 비효율 유형 분류. `waste_classifier.py`에서 threshold 기반으로 판정.

## 분류 체계

| waste_type | 의미 | 실제 사례 |
|------------|------|-----------|
| `productive` | 비효율 없음 (정상) | 코드 읽고 바로 수정 |
| `read-heavy` | 읽기 비중 과다 | 15콜 중 12콜이 Read/Bash, Edit은 마지막 1개 |
| `repeated-loop` | 동일 작업 반복 | pip install → 실패 → 다시 install → 실패... |
| `expensive-failure` | 비용 큰 실패 | 환경 설정 삽질로 토큰만 소모 |
| `futile-exploration` | 결과 없는 탐색 | 계속 grep/read만 하고 edit 없이 종료 |

## 판정 로직 (우선순위 순)

### 1. repeated-loop

```
IF max_repeat(input_hash) >= 3:
    → repeated-loop (severe)

IF max_repeat(input_hash) >= 2 AND num_calls >= 20:
    → repeated-loop (warn)
```

동일한 normalized_input(hash)이 3번 이상 반복되면 즉시 판정.
2번 반복 + 에피소드 길이 20 이상이면 warn 수준으로 판정.

**전형적 패턴:** pytest 실행 실패 → 환경 수정 → 같은 pytest 재실행 → 또 실패

### 2. expensive-failure

```
IF error_count(is_wasteful calls) >= 3:
    → expensive-failure
```

에러를 뱉은 call이 3개 이상이면 판정. tool call의 `is_wasteful` flag 기반.

**전형적 패턴:** ModuleNotFoundError → pip install → 또 다른 에러 → 또 install

### 3. read-heavy

```
IF num_calls >= 10 AND read_output_token_ratio >= 0.70:
    → read-heavy (severe)

IF num_calls >= 10 AND read_output_token_ratio >= 0.60:
    → read-heavy (warn)
```

에피소드가 10콜 이상이면서 Read/Grep/Bash의 output token이 전체의 60~70% 이상이면 판정.

**전형적 패턴:** 파일을 하나씩 계속 읽으며 코드 파악만 하다가 마지막에 겨우 Edit 하나

### 4. futile-exploration

```
IF num_calls >= 25 AND new_information_rate < 0.3 AND no Edit/Write:
    → futile-exploration

IF no_new_info_calls >= 8 AND no Edit/Write:
    → futile-exploration

IF futility_score > 0.6:
    → futile-exploration
```

오래 탐색했지만 새로운 정보도 없고 수정도 없이 끝난 경우.

**전형적 패턴:** 20개 파일 읽었지만 전부 이미 본 내용, 결국 아무것도 안 하고 abandon

## Thresholds (기본값)

```python
WasteThresholds(
    read_heavy_min_calls=10,
    read_heavy_warn_ratio=0.60,
    read_heavy_severe_ratio=0.70,
    repeated_loop_window=20,
    repeated_loop_warn_count=2,
    repeated_loop_severe_count=3,
    expensive_failure_warn=2,
    expensive_failure_severe=3,
    futile_exploration_min_calls=25,
    futile_exploration_no_new_info=8,
)
```

`Config.thresholds`에서 커스터마이즈 가능.

## Metrics → Waste 관계

| Metric | 높으면 | 관련 waste_type |
|--------|--------|-----------------|
| `read_output_token_ratio` | 읽기만 함 | read-heavy |
| `new_information_rate` (낮으면) | 중복 정보 | futile-exploration |
| `repeated_read_rate` | 같은 파일 재읽기 | futile-exploration |
| `futility_score` | 복합 무용도 | futile-exploration |

## Embedding에서의 활용

waste_type은 `embedding_text`의 첫 번째 facet으로 직렬화됨:

```
read-heavy | converged | python | flask/blueprints/ | ...
```

벡터 검색 시 유사한 상황의 waste 에피소드가 상위에 올라오면 **반면교사**로 활용:
- "이 접근은 삽질이었다" → 다른 방법 시도 유도
- "환경 설정 실패가 반복됐다" → 환경 확인 먼저 하도록 안내
