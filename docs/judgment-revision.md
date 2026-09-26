# 불확실성 판단 개선과 현재 대화 호출 경로

기존 [Modal 실제 문맥 검사](openjev-session.md)에서 확인한 정보 부족·상충·모호한 도구 설명 오판을 줄이는 후속 변경
한국어 원문과 영어 판단 지시의 조합 유지, 번역 단계 추가 없이 선택지와 판단 경계 명확화
새 template revision은 `jev-context-v2-2`, 이전 revision의 평가로 자동 선별을 활성화하지 않는 조건
이후 [현재 대화의 직접 MCP 연결과 후속 비교](native-mcp-validation.md)에서 DB 계정 문제 복구·실제 도구 호출·미반영 v3 후보 평가 진행

## 변경한 판단 경계

- 요청 접수·작업 시작만으로 성공을 주장하지 않고 `insufficient_evidence` 선택
- 동등하게 신뢰할 수 있는 관측의 미해결 충돌은 임의로 한쪽을 확정하지 않는 판정
- `partial`은 복합 주장 중 확인된 부분이 있고 나머지는 미확인인 경우로 한정
- 명시적 반례·예외가 보편 주장을 깨거나 복합 주장 일부를 반박하면 `contradicts` 선택
- 시점이 다른 과거 성공을 현재 성공으로 추정하지 않고 명시된 우선순위·충돌 해소만 사용
- 도구 이름·가용 여부·홍보 문구에서 기능을 추정하지 않고 구체적 기능 설명이 없으면 판단 보류
- 지원하지 않는 기능·명시된 비가용 상태·범위를 벗어난 필수 부작용은 `unfit` 선택

질문과 선택지 정의는 [judgment.py](../src/jev_context/judgment.py), 이전 지시문은 [동결한 v1 질문](../models/question-templates-v1.json)에 보존
모델의 분류 결과와 승인·실행·완료 증명은 기존과 같이 별개

## 비교 방식

같은 GPU 세션에서 v1과 v2를 쌍으로 실행하고 회차마다 순서를 교대하는 비교
기존 [개발 문항 50개](../models/openjev-challenge.json)와 새 [검증 문항 44개](../models/judgment-validation.json)를 각각 3회 반복
94개 문항·2개 지시문·3회로 총 564개 판단, 배치당 최대 8개 입력
평가 시작 전에 두 질문 묶음과 데이터 hash·반복 수를 `.plan.json`에 고정

새 문항은 다른 대상·표현과 실제로 알려진 기능·해소된 충돌 같은 대조 사례 포함
에이전트가 작성한 validation 자료이며 독립적으로 사람이 검토한 heldout 평가가 아닌 구분
반복 결과 3개와 대응 한영 문항은 상관된 자료이므로 독립 표본 수로 확대 해석하지 않는 기준
변동 문항 수와 잘못된 fit·supports를 정답 수와 함께 보고

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\evaluate_judgment_revision.py --config .local\project.toml --work-id $workId --output .local\evaluations\judgment-comparison.json --wait-ready 650
```

위 명령은 기존 명시적 작업 세션이 준비되기를 기다리는 평가이며 GPU 생성·재시도·승격을 수행하지 않는 경로
동일 출력 파일 재사용 금지, 중단된 실행의 평가 계획과 원시 결과도 보존

## 2026-09-22 실행 결과

profile fingerprint `f4546e853d2ac51763268cfe169d621193809ba77c4e83395f9ea67d902409c3` 기준
아래 정답 수는 각 문항을 3회 모두 맞힌 문항 수이며 반복 횟수로 표본 수를 늘리지 않은 집계

| 자료 | 언어 | 이전 지시문 | 새 지시문 |
| --- | --- | --- | --- |
| 기존 개발 문항 | 한국어 | 22/25 | 25/25 |
| 기존 개발 문항 | 영어 | 21/25 | 25/25 |
| 새 검증 문항 | 한국어 | 17/22 | 20/22 |
| 새 검증 문항 | 영어 | 17/22 | 19/22 |
| 전체 | 한영 합계 | 77/94 | 89/94 |

기존 50문항은 43개에서 50개, 새 44문항은 34개에서 39개로 개선
총 564회 판단에서 기술적 판단 보류 0건, 이번 단일 세션 안의 반복 답변 변동 0건
배치 전체 Windows·Modal 왕복 중앙값 735ms, p95 797ms, 최대 969ms
단일 질문의 추론 시간이나 GPU 준비 시간을 의미하지 않는 수치
새 지시문에서 잘못된 `supports`는 없으나 비가용 도구를 `fit`으로 분류한 한영 2문항은 반복마다 동일하게 발생
이번 비교에서도 영어 번역의 우위는 확인되지 않은 결과

남은 오답 5개

| 문항 | 예상 | 관측 | 남은 문제 |
| --- | --- | --- | --- |
| validation-3-ko/en | insufficient_evidence | contradicts | 복구 대기 상태를 성공 주장에 대한 반박으로 확정 |
| validation-6-en | supports | insufficient_evidence | 관측이 충돌한다는 주장 자체를 판단하지 못함 |
| validation-20-ko/en | unfit | fit | 명시된 available=false를 무시 |

실제 후보 추천은 비가용 항목을 모델 호출 전에 제외하며 필수 도구가 비가용이면 `insufficient` 반환
모델이 항상 fit을 반환하는 검사 엔진으로도 해당 도구의 모델 호출·선택이 0건인 회귀 검사 추가
원시 분류 오류는 평가에서 그대로 오답으로 유지
사람이 검토한 heldout 자료·실제 호스트 작업 품질·전체 비용과 토큰 효율은 미검증이므로 shadow 유지

## 현재 대화에서 호출

현재 Codex 프로젝트 설정의 MCP 항목은 enabled 상태이나 이 대화에 제공된 직접 MCP 도구는 없는 상태
도구 목록을 강제로 바꾸지 않고 같은 Service·계약 검증·모델 준비 함수를 공유하는 CLI 진입점 추가
현재 대화에서 CLI를 통해 저장 작업 복원과 revision을 확인한 변경 기록 실행
이 결과는 CLI를 통한 실제 사용 증거이며 Desktop MCP 직접 노출 성공을 의미하지 않는 범위

입력 schema 조회

```powershell
.\.venv\Scripts\python.exe -m jev_context schema --config .local\project.toml --tool context_prepare
```

UTF-8 요청 파일에 `contract_version`, 새 `request_id`, 실제 `work_id`, `query`와 필요한 옵션 지정
변경 요청에는 같은 논리적 재시도에서 유지하는 `mutation_id`와 필요한 revision도 포함

```powershell
.\.venv\Scripts\python.exe -m jev_context call --config .local\project.toml --tool context_prepare --input .local\requests\context.json --prepare-engine
```

`--input` 생략 시 표준 입력 JSON 사용, 파일·표준 입력 모두 524288바이트 상한과 중복 키 검사 적용
`--prepare-engine`은 Modal 세션을 생성하지 않고 이미 준비된 동일 프로젝트·작업 세션에 연결
GPU 종료 후에도 같은 호출 경로에서 저장·검색 사용 가능
표준 출력은 기존 도구 계약의 JSON envelope, 오류·충돌 종료 코드 1, JSON 입력 오류 종료 코드 2

현재 대화에서 실제 CLI를 실행한 결과

- 저장된 work ID 복원과 expected revision을 확인한 scope 변경 성공
- 준비된 Modal 세션에서 실제 context_prepare의 8개 판단 observed, 프로세스 시작을 포함한 호출 1359ms
- GPU 종료 후 같은 work ID 복원·검색 성공, 모델 판단 abstained와 원문 근거 7개 반환
- 새 CLI 경로를 설치용 Skill에 반영, Desktop의 직접 MCP 도구 노출은 미확인

32KiB 전체 응답 예산에서 모델 제외 기준은 근거 7개, shadow 관찰은 6개 반환
판단 정보와 가능한 반대 근거의 required_evidence 승격·정렬이 같은 응답 예산을 사용해 선택 자료 1개 추가 제외
필수 근거와 제약은 보존하고 제외된 선택 자료는 source_read로 고정 revision 원문 재조회 성공
따라서 shadow의 명시적 모델 제외·축약 비활성화와 예산에 따른 선택 자료 제외를 구분
이 결과를 토큰 절감이나 품질 손실 없는 압축으로 해석하지 않는 기준

초기 live probe는 네 번의 실제 호출 뒤 모든 선택 근거가 같아야 한다는 단정에서 실패
검사 기준을 필수 근거·제약 보존과 예산 제외 원문의 조회 가능성으로 수정
수정 후 live 재실행은 GPU 유휴 종료로 준비 조건을 충족하지 못한 기록도 보존
live 결과의 후속 검증은 저장한 실제 응답 분석과 새 source_read 호출이며 수정된 live probe 전체 통과를 의미하지 않는 구분
종료 상태의 수정된 probe는 전체 통과

## 준비 지연 처리

GPU 배정 대기가 모델 로딩보다 먼저 발생하므로 로컬 준비 대기에서 두 시간을 함께 고려
준비 대기 최대 600초와 사용자가 지정한 세션 전체 TTL 중 먼저 도달하는 제한 적용
준비 중 취소와 준비 시간 초과 원인은 `requested_during_preparation`·`preparation_deadline`로 상태 기록
일반 판단 요청의 2초 예산과 전체 세션 TTL·원격 함수 1200초 상한은 별도 유지

첫 시도는 GPU 배정 대기 약 207초 뒤 로딩을 시작해 종전 360초 준비 제한으로 종료, 평가 응답 0건
제한을 위 범위로 수정한 두 번째 시도에서 비교 평가 완료
평가 뒤 유휴 제한으로 자동 종료했고 Modal 컨테이너 0개 확인
두 시도의 조회 시점 앱 사용액 합계 약 $0.58, 최종 청구서·저장 비용·이전 평가 사용액과 별도
실행 앱은 [준비 제한으로 종료한 시도](https://modal.com/apps/tttaliesin/main/ap-cCnL7JcNEYFkUpBMn73pml)와 [비교 평가 완료 시도](https://modal.com/apps/tttaliesin/main/ap-ZFxMweOZhxq3fmj6owQlHi)

## 재현 기록

`scripts/check.py`로 코드 형식·정적 검사와 전체 회귀 검사 실행
CLI 입력·schema·변경 기록과 비가용 후보 차단을 포함한 107 passed·1 skipped
skipped는 기존 Windows 심볼릭 링크 생성 권한 검사

로컬 원시 결과

- `.local/evaluations/judgment-v2-comparison-run2.plan.json`: 추론 전 고정한 비교 계획
- `.local/evaluations/judgment-v2-comparison-run2.json`: 564회 원시 판단과 반복 집계
- `.local/evaluations/judgment-startup-timeout.json`: 첫 준비 시간 초과 상태
- `.local/evaluations/cli-host-ready.json`: 실제 live 호출과 초기 probe의 중간 결과
- `.local/evaluations/cli-host-ready-budget-aware.json`: 유휴 종료 뒤 재검사 시도
- `.local/evaluations/cli-host-ready-analysis.json`: 저장된 live 응답 분석과 원문 재조회
- `.local/evaluations/cli-host-stopped.json`: GPU 종료 후 CLI 전체 검사
- `.local/evaluations/judgment-v2-billing.json`: 이번 두 앱의 사용액 조회

평가 실행기는 [evaluate_judgment_revision.py](../scripts/evaluate_judgment_revision.py), 실제 CLI 검사는 [probe_cli_integration.py](../scripts/probe_cli_integration.py)
