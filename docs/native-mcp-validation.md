# 현재 대화의 MCP 연결과 후속 검증

2026-09-22 현재 Codex 대화에서 `jev_context` 도구를 직접 호출해 저장 작업 복원·검색·기록·고정 revision 원문 조회 확인
기존 [CLI 검증](judgment-revision.md) 이후 처음 확인한 Desktop 도구 호출 경로
GPU 준비·판단 품질·호스트 작업 효율은 각각 별도 측정 대상으로 구분
후속 [독립 코드 수정 6회 비교](coding-benchmark-results.md)에서 실제 수정 성공률·호스트 누적 토큰·실행 시간 측정 추가

## DB 권한 오류와 복구

원인은 CLI의 `CodexSandboxOnline` 계정과 MCP 프로세스의 로그인 계정 `tttal` 사이의 DB 운영 계정 불일치
기존 DB 폴더는 CLI 계정 소유이며 `protect_directory`는 현재 실행 사용자와 SYSTEM만 허용하도록 구성
MCP 도구 목록에는 10개 함수가 보이지만 상태 조회와 작업 복원에서 `storage_failed: Cannot protect local database directory` 반환

프로세스 실행 계정과 해당 DB 폴더의 소유자·DACL을 확인한 뒤 프로젝트 DB 디렉터리만 MCP 로그인 계정으로 이전
이전 후 보호된 DACL은 로그인 사용자와 SYSTEM만 허용, 일반 사용자 그룹 추가 없음
DB·잠금·백업 파일을 포함한 해당 디렉터리 내 11개 객체의 소유자와 ACL 확인
DB 내용 초기화·삭제·다른 프로젝트 권한 변경 없이 기존 작업 ID와 revision 복원 성공

이 구성에서는 프로젝트 DB를 여는 CLI도 MCP와 같은 OS 계정에서 실행
샌드박스에서 DB를 새로 만들거나 기존 ACL을 덮어쓰지 않고 현재 노출된 MCP 도구 사용
GPU 세션 시작은 DB의 work ID 확인이 필요하므로 같은 로그인 계정 사용
일반 MCP 조회·검색은 GPU를 생성하지 않는 기존 동작 유지

권한 변경 전후 기록은 `.local/evaluations/mcp-storage-account-repair.json`
서버의 `host_integration.desktop_current_session: not_observed`는 서버가 호스트를 스스로 관측하지 않았다는 고정 상태값
현재 대화의 직접 호출 증거는 서버의 이 상태값과 별도로 기록

## 직접 호출 확인

- `workspace_status`: 기존 프로젝트 ID와 작업 목록 반환
- `work_open`: 기존 작업의 revision 6 복원
- `context_prepare`: GPU 미실행 상태에서 원문 근거와 제약 반환, 판단은 abstained
- `work_record`: 사용자의 계획 진행 요청을 scope 변경으로 기록, revision 7 반환
- `work_open`: 변경된 목표와 revision 7 재조회
- `source_read`: 지정한 source ID·revision의 원문 조회
- `source_sync`: 허용된 구현 파일 7개 등록, 모두 성공
- `workspace_status`: 같은 대화에서 GPU 준비 후 shadow, 종료 후 unavailable 확인
- `context_prepare`: 준비된 GPU와 연결한 실제 모델 관찰 성공
- `work_inspect`: 작은 응답 예산으로 분리된 실제 판단 결과 재조회

## 비교와 해석 기준

운영 지시문 v2-2와 별도 v3 후보의 질문·선택지 파일을 분리하고 운영 지시문은 비교 중 유지
이전 94문항과 새 한영 16쌍·32문항을 각각 3회 반복하는 총 756회 비교
추론 전 질문 파일·자료 hash·반복 횟수를 계획 파일에 고정
중복 case ID나 선택지 계약을 바꾸는 후보는 평가 실행기에서 거절

별도 실제 프로젝트 문맥 검사는 저장소 코드 조사 6개 질문과 8KiB·32KiB 응답 예산 사용
동일 source ID·revision·작업 revision에서 모델 off와 observe 응답을 비교
직접 MCP 호출 지연·전체 응답 바이트·근거 수·미리 정한 코드 문자열 포함 여부·제약 보존을 기록
문자열 포함률은 필요한 코드 근거를 가져왔는지에 대한 제한된 진단이며 코딩 작업 성공률과 별개
현재 호스트가 제공하지 않는 실제 Codex 입력 토큰과 독립 코딩 과제 성공률은 null로 기록
UTF-8 바이트나 다른 모델의 tokenizer 값을 Codex 토큰 수로 대체하지 않는 기준

## 판단 지시문 비교 결과

운영 v2-2와 v3 후보를 같은 GPU에서 비교한 결과이며 정답 수는 각 문항을 3회 모두 맞힌 문항 수

| 자료 | 운영 v2-2 | v3 후보 |
| --- | --- | --- |
| 기존 개발 50문항 | 50/50 | 49/50 |
| 이전 validation 44문항 | 40/44 | 42/44 |
| 새 validation 32문항 | 29/32 | 30/32 |
| 전체 | 119/126 | 121/126 |

총점은 개선됐지만 `validation-11-en`에서 관리자는 추가 인증이 필요하다는 근거를 추가 인증이 불필요하다는 주장의 supports로 3회 모두 오판
기존 예외 문항 `challenge-3-en`에도 회귀 발생
후보의 비가용 도구 false fit은 0건으로 감소했으나 설명이 모호한 도구를 unfit과 insufficient_evidence 사이에서 반복 변경
운영 지시문에서도 직전 실행과 이번 실행 사이의 답변 변동과 이번 반복 중 변동 확인
따라서 v3 후보는 운영에 반영하지 않고 기존 v2-2·shadow 유지

756회 요청 모두 observed, 배치 전체 왕복 중앙값 750ms·p95 860ms
후보 파일의 질문과 기록된 정답은 결과를 본 뒤 변경하지 않은 상태
명령형 정책 문장과 실제 완료 주장처럼 정답 해석이 모호할 수 있는 사례는 독립 검토 대상으로 유지
자료 자체의 정답 정확성도 검토 전제이며 정답 수만으로 모델의 일반 정확도를 확정하지 않는 범위

## 실제 MCP 문맥 진단 결과

각 조건은 코드 조사 질문 6개, 총 context_prepare 24회와 필요한 source_read 12회
재조회 대상 source ID는 평가 계획에 미리 지정한 값이며 호스트가 스스로 필요한 파일을 찾아내는 능력과 별개
다음 시간은 직접 MCP 호출과 필요한 원문 재조회의 합계 중앙값, 응답량은 둘을 합한 평균 UTF-8 바이트

| 응답 예산 | 판단 모드 | 최초 근거 충족 | 원문 재조회 | 재조회 후 충족 | 전체 시간 | 전체 응답량 |
| --- | --- | --- | --- | --- | --- | --- |
| 8KiB | off | 0/6 | 6회 | 6/6 | 3948ms | 24930바이트 |
| 8KiB | observe | 0/6 | 6회 | 6/6 | 4748ms | 23535바이트 |
| 32KiB | off | 6/6 | 0회 | 6/6 | 3038ms | 27295바이트 |
| 32KiB | observe | 6/6 | 0회 | 6/6 | 4304ms | 27488바이트 |

8KiB observe의 두 요청은 필수 근거를 조용히 빼는 대신 명시적 insufficient 반환
작은 응답량을 같은 품질의 압축이나 절감 효과로 해석하지 않는 기준
정상 문맥을 반환한 22개 요청 모두 작업 제약 유지
8KiB의 분리된 판단은 packet ID로 다시 조회해 observed 확인
32KiB에서는 이번 6개 질문의 필요한 코드 근거를 원문 재조회 없이 확보

같은 예산 안에서는 off·observe 순서를 교대했으나 8KiB 전체 뒤에 32KiB를 실행한 단일 세션
캐시·실행 순서·호스트 부하를 통제한 반복 성능 실험이 아니므로 예산 증가가 지연 감소의 원인이라고 단정하지 않는 범위
shadow는 모델 제외·축약을 활성화하지 않고 관찰 비용을 더하는 모드이므로 이 수치로 active 선별의 효율을 추정하지 않는 기준
운영 시 코드 조사에 32KiB를 지정하고 반환 근거의 충분성을 확인하는 방법은 이번 진단으로 확인

## 검증과 실행 기록

회귀 검사 110 passed·1 skipped, 코드 정적 검사·형식 검사 통과
skipped는 기존 Windows 심볼릭 링크 생성 권한 검사
추가 검사는 중복 평가 문항·선택지 계약 변경 거절과 반복 표본 집계 확인
운영 서버 코드·운영 모델 질문·profile fingerprint는 이번 평가 중 유지

[Modal 평가 앱](https://modal.com/apps/tttaliesin/main/ap-rm946E8NCkxevJbS3yQZAx)의 조회 시점 사용액 약 $0.46
평가 종료 후 명시적 session-stop 실행, Modal 컨테이너 0개와 현재 대화 MCP의 unavailable 상태 확인
이 사용액은 이번 앱의 GPU·CPU·메모리 비용이며 최종 청구서·저장 비용·이전 실행 비용과 별도

로컬 실행 기록

- `.local/evaluations/native-mcp-calls.json`: 현재 대화의 직접 MCP 호출
- `.local/evaluations/mcp-storage-account-repair.json`: DB 계정 이전과 ACL 확인
- `.local/evaluations/judgment-v3-comparison.plan.json`: 추론 전 동결한 비교 계획
- `.local/evaluations/judgment-v3-comparison.json`: 756회 원시 판단
- `.local/evaluations/native-context-benchmark.final-plan.json`: 문맥·원문 재조회 비교 계획
- `.local/evaluations/native-context-benchmark.json`: 24회 문맥 조회 집계와 개별 원시 기록 경로
- `.local/evaluations/native-budget-insufficient-trace.json`: 작은 예산에서 분리된 판단 재조회
- `.local/evaluations/native-mcp-billing.json`: 이번 앱의 사용액

질문 파일은 [운영 v2](../models/question-templates-v2.json)와 [미반영 v3 후보](../models/question-templates-v3-candidate.json), 새 자료는 [validation 32문항](../models/judgment-validation-v3.json)

## 자동 선별 승격 조건

기존 목적별 조건인 일치 profile fingerprint·사람이 검토한 heldout 자료·언어와 목적별 30개 이상·중요 회귀 0건·품질과 효율 통과 유지
모델 오류를 availability 검사 같은 코드 방어가 막더라도 원시 모델 정확도에서는 오답으로 보존
에이전트가 작성하고 반복해서 확인한 자료는 개발·validation 자료로 유지
같은 자료의 정답을 사후 검토했다는 이유로 독립 heldout 자료로 재분류하지 않는 기준

현재 대화의 연결 확인과 진단 평가만으로 자동 선별 활성화 조건을 충족했다고 보고하지 않는 범위
독립 검토자는 운영 후보를 고정한 뒤 미사용 자료의 정답·반례·모호성을 검토하고 검토자·날짜·자료 hash 기록
실제 작업 평가는 같은 출발 상태와 완료 검사를 가진 과제를 별도 실행해 성공률·전체 지연·호스트 토큰·모델 비용 비교
