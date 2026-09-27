# 구현 0.2.0과 검증 범위

아래는 초기 0.2.0 구현 기록이다. 이후 Desktop 연결 시연은 [호스트 기능표](host-capabilities.md), 현재 로컬 엔진과 프로젝트 이동 복구 결과는 [2026-09-26 검증](recovery-validation.md)을 따른다.

2026-09-27의 [문맥 전달 개선](context-efficiency-results.md)은 현재 상태·이력 분리와 예산 누락 표시를 추가한다. 저장 가능한 판단 상세는 크기와 무관하게 별도 조회하며, read-only에서는 조회할 수 없는 packet 참조를 만들지 않고 판단 상세를 본문에 유지한다. 계약 입력과 DB schema는 변경하지 않는다.

[통합 설계 2.0](design/unified-design.md)을 구현하는 두 번째 개발 버전
실제 로컬 추론과 MCP 호출 연결, 자동 선별 승격 기준은 미충족 상태
후속 [영어 번역 입력 비교](translation-evaluation.md)에서도 현재 모델의 자동 선별 품질 미달 확인
실행 명령·통과 수·실제 모델 지연은 [0.2.0 검증 기록](verification-v2.md)에서 확인
후속 [OpenJev Modal 작업 세션](openjev-session.md)에서 원격 판단·실제 MCP 문맥·토큰 사전 검사·GPU 종료 검증 추가

## 실제 사용 경로

`work_open` → `source_sync` → `context_prepare` → `capability_recommend` → 호스트 작업 → 검증 기록 → 다음 작업 복원
`context_prepare`에서 근거별 관련성·표현 길이와 선택적 주장 관계를 로컬 모델에 질의
필수 제약·알려진 충돌·실패·반대 근거는 선별에서 제외하고 반드시 보존
원문 위치·revision·hash 유지, 원문과 모델 판단을 별도 데이터로 반환

검증된 active profile에서는 무관한 선택 자료 제외와 짧은·긴 원문 발췌 선택
Laya와 후속 OpenJev Modal 구성 모두 shadow 상태로 실제 판단을 반환하되 원문 선택에는 미반영
모델 입력·지시·선택지의 토큰 잘림은 판단 보류로 처리
예산은 MCP의 text/structuredContent 중복과 메타데이터까지 포함하며 JSON-RPC 외부 프레임만 제외
큰 판단 상세는 packet ID를 통해 `work_inspect(view=judgments)`에서 페이지별 조회

## 버전과 데이터

기존 `contracts.json`의 1.0 계약 유지, `contracts_v2.py`에서 명시적 2.0 계약 구성
프로젝트 설정의 `contract_version = "2.0"`과 모든 MCP 요청의 `contract_version: "2.0"` 필요
2.0에는 기존 8개 도구와 `capability_recommend`, `handoff_prepare` 제공

기존 DB 전환 전 사용 중인 이전 서버 종료 후 설정에 계약 버전 추가

```powershell
.\.venv\Scripts\python.exe -m jev_context migrate --config .local\project.toml
```

SQLite backup API로 `state.v1-backup.sqlite` 생성 후 schema 2로 변경
이전 schema를 사용하는 프로세스는 변경된 정책 hash 또는 schema 검사로 거절
백업에는 전환 전 원문이 남으며 이후 `data_forget`의 삭제 범위에 포함되지 않는 별도 복구 자료
복원은 서버 종료 후 백업을 별도 데이터 위치에 복사하고 1.0 설정으로 연결
진행 중인 WAL 파일을 수동으로 덮어쓰는 복원 방식 제외

기존 출처와 작업의 소유 관계를 추정해서 새로 부여하지 않는 마이그레이션
과거 이벤트·revision·mutation 기록 유지

## 모델 준비와 실행

확인 장비: Windows, 메모리 약 32GB, Intel Arc 140V, NVIDIA GPU 없음
선택한 시험 모델: `convaiinnovations/laya`의 multilingual checkpoint
체크포인트 revision `1c5edc17a7acd8701df6fc341c0d179f1c62c982`, Laya 소스 revision `573e5b62696ba441230cd6be71d593331b5d23af`
모델·설정·tokenizer 합계 약 678MB, 별도 CPU 실행 환경에 PyTorch와 SDK 설치

```powershell
.local\tools\uv.exe --cache-dir .local/uv-cache venv .local/laya-venv --python .venv/Scripts/python.exe
.local\tools\uv.exe --cache-dir .local/uv-cache pip sync models/laya-requirements.txt --python .local/laya-venv/Scripts/python.exe --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
.\.venv\Scripts\python.exe scripts/prepare_laya.py
```

위 명령만 네트워크에서 준비 파일을 취득하며 서버 실행 중에는 offline 모드 사용
준비 스크립트에서 새 `.local/laya-profile.json` 생성, 이미 존재하는 profile은 유지
파일 크기·가중치 SHA-256·실행 전 manifest 검증
모델 profile뿐 아니라 adapter와 질문 코드 hash도 fingerprint에 포함해 변경 시 기존 평가 무효화
같은 lock root에서 모델 worker 하나만 상주

프로젝트 설정의 엔진 항목

```toml
[engine]
state = "shadow"
profile_file = "C:/absolute/project/.local/laya-profile.json"
```

```powershell
.\.venv\Scripts\python.exe -m jev_context serve --config .local\project.toml --prepare-engine
```

`codex-config`와 설치 스크립트는 위 엔진 사용 인자와 MCP 시작 제한 120초를 생성
2026-09-26 재설계부터 로컬 모델의 `--prepare-engine`은 공유 실행기 연결만 활성화하며, 실제 판단 요청 전에는 모델을 준비하지 않는다.
Windows 독립 실행기 시작·종료와 기본 120초 유휴 해제 정책은 [모델 수명 설계](model-lifecycle-design.md)를 따른다. MCP 시작 제한과 모델 준비 제한은 별개다.
모델 준비 실패 시 검색 서비스를 유지하며 엔진 미준비 상태 반환
유료·원격 추론으로 자동 대체하지 않는 동작

## 평가와 활성화

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/evaluate_model.py --profile .local/laya-profile.json --cases models/korean-diagnostic.json --output .local/evaluations/laya-ko-development.json
.\.venv\Scripts\python.exe -X utf8 scripts/smoke_mcp_v2.py --profile .local/laya-profile.json --output .local/evaluations/mcp-v2-smoke.json
```

첫 명령은 실제 추론 결과·입력 dataset hash·profile fingerprint·지연 시간 기록
30문항은 개발자가 작성한 진단용 예시이며 사람이 검토한 30개 실제 작업 또는 heldout 평가가 아닌 자료
진단 결과(2026-09-23 재실행): 관련성 6/12, 주장 관계 8/12, 도구 적합성 3/6, 총 17/30
이전 profile fingerprint의 2026-09-22 결과 16/30(도구 적합성 2/6)은 `.local/evaluations/laya-ko-development.2026-09-22.json`에 보관
도구 적합성 차이는 unfit·insufficient_evidence 사이를 오간 4문항의 변동으로 개선 근거가 아닌 수준
관련성은 모든 문항을 relevant로 선택해 무관 문항 6개 전부 오답
반대 근거 오답 2개로 자동 선별 활성화 기준 미달
이 결과를 근거로 품질 향상·토큰 절감·제품 완료를 주장하지 않는 판정

평가 스크립트는 활성화를 수행하지 않으며 개발용 보고서의 quality/efficiency gate는 실패 상태로 기록
active 모드는 로컬 설치 설정에 평가 파일과 SHA-256을 명시한 경우에만 목적·언어별 gate 확인
동일 profile, human-reviewed heldout, 목적별 30건 이상, 중요 회귀 0, 비기권 판단, 사전 품질·전체 효율 기준 통과와 threshold 필요
모델 확신도는 정답률이 아닌 `1 − 정규화된 Shannon entropy`이며 목적별 평가 없이는 자동 사용 불가

## 완료와 전달

`criterion_registered`에서 필수 기준·대상 revision 등록
`criterion_result`의 passed에는 성공 종료 코드·대상 hash가 있는 동일 revision의 evidence ID 필요
미실행·실패·불명·적용 제외 사유를 구분하고, 누락 기준·열린 문제·다른 revision이면 완료 불충족
`reported_complete`는 보고된 자료의 충족 상태이며 `host_verified`로 승격하지 않는 동작
출처 삭제나 후속 변경 시 기존 완료 충족 상태 무효화

`capability_recommend`는 60초 이내 관찰된 현재 목록·version을 검사하고 필수 후보 보존
호스트 제공 목록은 agent-reported, 실행 직전 호스트가 가용 여부·hash를 재확인하는 계약
`handoff_prepare`는 목표·프로젝트·작업 revision·원문 revision·범위·역할·회차를 포함한 중복 키 생성
읽기 조사 packet만 준비하며 worker 실행·Codex 작업 생성은 수행하지 않는 범위
쓰기 전달은 호스트의 격리·파일 소유권을 확인하는 어댑터가 없어 unsupported 반환

## 남은 제품 검증

- 현재 Desktop 대화에서 MCP 도구 노출과 Skill을 통한 실제 사용자 작업 흐름 확인
- 한국어 판단 품질을 충족하는 모델 또는 학습·질문 구성 검증
- 사람 검토를 거친 별도 heldout 과제와 E0/E1/E2/E3 전체 작업 효율 비교
- 선택형 hooks 어댑터와 쓰기 worker 격리 연동

실제 OpenJev 추론은 이 장비에서 검증하지 않은 항목
MCP stdio 통합 성공을 Desktop 연결 성공으로 확대하지 않는 기준
