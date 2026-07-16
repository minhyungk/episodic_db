# Episodic DB Documentation

## 문서 목차

### 개요
- [architecture.md](./architecture.md) — 시스템 아키텍처, 모듈 구조, 설계 원칙
- [getting_started.md](./getting_started.md) — 설치, 실행, CLI 사용법

### 내부 동작
- [capture_system.md](./capture_system.md) — Hook Handler + API Proxy 상세 (데이터 수집)
- [data_model.md](./data_model.md) — SQLite 테이블, 인덱스, 엔티티 관계
- [labeling_algorithm.md](./labeling_algorithm.md) — Token reconciliation, contribution marking, waste signals, outcome
- [episode_assembly.md](./episode_assembly.md) — Segmentation, signature, metrics, waste classification, embedding
- [episode_pipeline.md](./episode_pipeline.md) — 파이프라인 전체 흐름 요약 (기존)
- [query_system.md](./query_system.md) — Facet 검색 + 벡터 유사도 검색

### 참조
- [schema.md](./schema.md) — episodes 테이블 필드 상세 (기존)
- [waste_types.md](./waste_types.md) — 낭비 유형 분류 규칙 (기존)
- [benchmarking.md](./benchmarking.md) — SWE-bench 벤치마크 시스템

### 개발
- [development_guide.md](./development_guide.md) — 테스트, 확장 포인트, DB 마이그레이션, 디버깅

### 미래 구현 안
- [multiagent_dependency_tracking.md](./multiagent_dependency_tracking.md) — 멀티에이전트 dependency 추적 + 사고 backtracking (시뮬레이션 검증 완료)

---

## 읽기 순서 추천

**처음 접하는 경우:**
1. `architecture.md` — 전체 구조 파악
2. `getting_started.md` — 실행해 보기
3. `data_model.md` — 데이터 구조 이해
4. `capture_system.md` → `labeling_algorithm.md` → `episode_assembly.md` — 파이프라인 순서대로

**특정 부분을 수정해야 할 때:**
- Hook/Proxy 수정 → `capture_system.md`
- 라벨링 로직 수정 → `labeling_algorithm.md`
- 에피소드 분할/시그니처 수정 → `episode_assembly.md`
- 검색 기능 수정 → `query_system.md`
- 새 필드 추가 → `data_model.md` + `development_guide.md`
- waste_type 추가 → `waste_types.md` + `development_guide.md`
