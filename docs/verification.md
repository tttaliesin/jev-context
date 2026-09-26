# 첫 버전 검증 기록

2026-09-22 · Windows x64 · Python 3.12.14 · 제품 0.1.0
현재 프로젝트에서 새로 구현한 코드의 실행 결과이며 과거 `outputs/context-ledger` 코드·테스트는 재사용하지 않은 범위

## 실행한 검사

- Ruff lint·format 검사 통과
- pytest 자동 검사 71개 통과, symlink 생성 권한이 필요한 검사 1개 건너뜀
- Windows junction을 통한 프로젝트 경계 이탈 차단은 별도 실제 검사 통과
- 공식 MCP Python 클라이언트로 initialize·8개 도구 목록·각 도구 호출·프로세스 재시작 후 복원 통과
- 동일 mutation 재시도·다른 입력 충돌·동시 work revision 충돌·원자적 rollback 확인
- 수집 중 프로세스를 강제 종료한 뒤 기존 커밋을 유지하고 미완료 항목만 재개하는 복구 확인
- source 삭제 후 원문·FTS·chunk·관련 기록 인용·packet·replay의 재노출 차단 확인
- 별도 프로젝트의 같은 파일명과 다른 프로젝트 source ID를 통한 자료 혼입 차단 확인
- OpenJev 모의 HTTP 서버로 loopback 제한·프록시 무시·redirect 거절·분포·NaN·시간 초과 확인
- 실제 Laya worker 프로세스에 모의 SDK를 연결해 JSON 통신·OS 단일 적재 잠금·시간 초과 종료·잠금 재획득 확인
- sdist·wheel 생성, 잠금 의존성의 hash를 검사해 별도 가상환경에 offline 설치, 설치된 wheel의 한국어 demo 실행 통과
- 배포 파일에 `.local`·`.venv`·`.uv-cache`·SQLite 데이터가 포함되지 않는지 확인

실행 명령은 `python scripts/check.py`, `uv lock --check`, `uv build --no-build-isolation --offline`
동작 예제는 `python -m jev_context demo`
설치 검사는 소스 경로 바깥의 별도 가상환경에서 `jev_context.__file__`이 해당 환경의 site-packages를 가리키는지 확인

문서의 상대 링크·기본 구조 검사 수행
Skill 설치본과 원본의 SHA-256 일치 확인
Skill 전용 quick_validate.py 실행은 PyYAML 누락으로 실패, frontmatter·워크플로는 정적 검토

## 작은 합성 입력의 지연

`python scripts/measure.py`로 같은 서비스 프로세스에서 각 100회 측정
입력은 작업 하나·짧은 한국어 발췌 하나, 모델·Desktop 왕복·cold start 제외

| 동작 | 중앙값 | p95 |
| --- | --- | --- |
| work_open | 0.063ms | 0.105ms |
| context_prepare | 2.575ms | 2.876ms |

2026-09-22 측정값이며 큰 저장소·모델·Desktop 성능으로 일반화하지 않는 범위
RAM 512MiB 목표와 GPU·VRAM 여유는 이번 측정에서 미검증

## 설계 30개 사례와의 대응

실제 사용자 자료로 고정한 30개 평가 세트는 아직 없으므로 전체 실제 과제 평가 상태는 `not_evaluated`
아래는 합성 자동 검사가 다루는 구성요소의 대응이며 의미 판단 30개 통과 집계가 아님
모델 판단과 호스트의 Skill 선택 품질은 합성 계약 검사로 대체하지 않는 기준

| 사례 | 현재 자동 검사 또는 남은 검증 |
| --- | --- |
| P01 | 서버 재시작 후 design 범위·제약 복원 |
| P02 | A 대체·B 채택 결정 상태 복원 |
| P03 | mutation 조회·재시도와 프로세스 종료 복구 |
| P04 | 같은 제목의 내부 작업은 별도 ID, 호스트의 사용자 확인 행동은 미검증 |
| P05 | 명시적 scope_revised.goal 변경과 과거 event 보존 |
| P06 | 애매한 “그건 빼고”의 원문 보존, 호스트의 지시 대상 해석은 미검증 |
| P07 | 두 글자 한국어 부분 검색 |
| P08 | 한국어와 refreshToken() 혼합 검색 |
| P09 | 현재 파일 hash 갱신·과거 revision 읽기 |
| P10 | 긴 문서 마지막 행의 예외 검색 |
| P11 | 같은 파일명의 다른 프로젝트 격리 |
| P12 | 미등록 파일 자동 수집 금지·등록 자료 검색 범위 표시 |
| P13 | 변경 전후 원문과 historical 상태 구분 |
| P14 | 허용·금지 문서 동시 반환, 자동 승자 선정 없음 |
| P15 | 낮은 검색 순위의 알려진 충돌 근거 보존 |
| P16 | 필수 항목 예산 초과 insufficient·필요 크기 반환 |
| P17 | 지시 무시 문구를 원문 데이터로 반환하고 scope 유지 |
| P18 | 조건·예외의 원문 저장·복원, 호스트의 행동 선택은 미검증 |
| P19 | 정확한 원문 조회 제공, 요약과 원문의 의미 불일치 자동 판정은 미검증 |
| P20 | outcome_unknown 실행 보고와 완료 근거 불충분 유지 |
| P21 | 과거 revision의 실행 보고를 현재 검증 성공으로 승격하지 않음 |
| P22 | 금지·의무 없음의 서로 다른 원문 보존, 모델 의미 구분 품질은 미검증 |
| P23 | 번역 프로필 없음, not_evaluated |
| P24 | 이중 부정 원문 보존, 모델의 유보 판단은 미검증 |
| P25 | Skill에 명시 제약 보존 안내, 실제 호스트 스킬 선택은 미검증 |
| P26 | 키워드 기반 자동 스킬 실행 없음, 실제 후보 판단은 미검증 |
| P27 | 모델 질문의 insufficient_evidence 필수, 실제 스킬 적합성은 미검증 |
| P28 | 모델 입력 길이·질문·라벨 검증, 실제 큰 스킬 목록 품질은 미검증 |
| P29 | 한영 혼합 요청 원문 보존, 호스트의 리뷰 범위 해석은 미검증 |
| P30 | 모의 엔진 장애·시간 초과 후 유보, 유료 API fallback 없음 |

실행 가능한 대응은 [테스트 폴더](../tests/)에서 확인

## 설치와 남은 환경 의존 항목

현재 프로젝트의 `.codex/config.toml`과 `.agents/skills/jev-context/SKILL.md` 설치 완료
설치 명령은 기존의 다른 설정을 보존하고 동일 이름의 다른 내용은 덮어쓰지 않는 방식
현재 실행 환경에서 `codex mcp get jev_context --json`은 홈 경로를 찾지 못해 실패
이 실패는 Python MCP 서버의 통신 오류와 구분하며 Desktop 세션의 실제 발견·호출은 앱 재연결 후 확인 필요

OpenJev 실제 서버·packed tokenizer 사전 검사 연결, Laya 실제 가중치·전용 환경·한국어 의미 평가 미완료
모델 프로필 active 승격·실제 30과제 비교·자원 기준 검증·hooks·번역·백업 복원은 미완료 또는 후속 범위
symlink 검사는 현재 OS 권한 조건에서 미수행이며 junction 경계 검사 결과로 symlink의 모든 동작까지 보증하지 않는 기준
