# OpenJev 작업별 Modal 연결

실제 경로는 Codex MCP → 로컬 문맥 검색 → 인증된 로컬 연결 → Modal 비공개 Queue → OpenJev GPU 판단 → 원문과 관찰 결과 반환
한국어 원문을 그대로 전달하고 판단 지시·선택지 설명은 영어 사용
번역본으로 원문을 대체하는 단계는 미사용

기존 [GPU 내부 진단](openjev-modal-evaluation.md)에서 확장한 실제 Windows 왕복·MCP 검증
운영 모드는 `shadow`, 자동 제외·발췌 축약·도구 선택 활성화는 미승격
프로젝트 문서의 선택된 발췌·질문·목표·제약을 해당 사용자의 Modal 작업으로 전송하는 명시적 원격 구성
아래는 첫 통합 연결 당시 기록이며 최신 지시문·반복 평가·현재 대화 CLI 실행은 [후속 판단 개선 기록](judgment-revision.md) 참조

## 2026-09-22 실제 결과

최종 profile fingerprint `f1a2783f441341b1f9cac5a203cc782ef4a6b57d10ccced2ddc2e696793d9ea9` 기준
측정 범위는 Windows 클라이언트·로컬 연결·Modal Queue·토큰 검사·GPU 추론 전체이며 준비 시간은 제외

| 항목 | 결과 |
| --- | --- |
| 한국어 진단 | 22/25, 중앙값 984ms, p95 1000ms |
| 영어 진단 | 22/25, 중앙값 984ms, p95 1000ms |
| 8개 독립 근거 묶음 | 전체 750ms, 8개 모두 observed |
| 실제 MCP 문맥 구성 | 관찰 모드 969ms, 모델 제외 기준 94ms |
| 실제 MCP 후보 추천 | 734ms, 필수 후보 유지 |
| 실제 MCP 읽기 전달 준비 | 1203ms, observed·not_dispatched |
| 원문 보호 | 같은 source·revision·content hash의 근거 7개 유지 |
| 입력 한도 초과 | 원문 축약 없이 input_incomplete |
| 첫 실행의 250ms 제한 실험 | 약 281ms 후 deadline_exceeded, GPU 종료 후 근거 7개 조회 가능 |
| 최종 정상 중지 | session-stop 후 Modal 컨테이너 0개 확인 |

직전 실행에서는 같은 한영 자료 각각 21/25로 일부 답의 반복 변동 확인
최종 실행의 한국어 오답은 예외 문장을 partial로 분류한 사례, 미해결 충돌을 partial로 분류한 사례, 설명 없는 도구를 fit으로 분류한 사례
영어에서는 예외 분류 대신 실행 결과 미수집을 partial로 분류한 사례가 오답
따라서 영어 번역의 우위나 자동 선별 안정성은 확인되지 않은 결론
partial·contradicts 결과는 보호 대상으로 올리고 shadow에서 모델의 명시적 제외·축약은 적용하지 않는 동작
전체 응답 예산에 따른 선택 자료 제외는 별도로 발생할 수 있으며 [후속 예산 검사](judgment-revision.md#현재-대화에서-호출)에 실제 영향 기록

단일 구조화 문맥 결과 크기는 기준 14,341바이트·관찰 모드 14,555바이트
전체 MCP 중복 포장을 포함한 수치나 Codex 입력 토큰 수가 아니며, 현재 결과로 토큰 절감 효과를 주장하지 않는 범위
전체 회귀 검사 100 passed·1 skipped, skipped는 Windows 심볼릭 링크 생성 권한에 따른 기존 검사

이번 두 세션의 조회 시점 앱 사용액은 합계 약 $0.54, 이전 단독 GPU 진단 비용과 별도
최종 청구서·저장 비용·향후 사용액을 포함하지 않는 측정값
실행 앱은 [첫 통합 검사](https://modal.com/apps/tttaliesin/main/ap-VuoMUtr7stVmdT40MtWeJS)와 [최종 검사](https://modal.com/apps/tttaliesin/main/ap-0U27WY92Syx2pYiJXowBAS)

로컬 원시 결과는 `.local/evaluations/openjev-session.json`, `openjev-session-final.json`, `openjev-session-shutdown.json`, `openjev-session-billing.json`
원격 모델 판단 성공은 실제 MCP 클라이언트 호출로 확인했으며 현재 Codex Desktop 대화에서의 도구 노출은 미확인

## 구현 경계

- 프로젝트 ID·work ID·profile fingerprint가 모두 일치하는 세션에만 요청 허용
- 최대 8개 근거를 한 번에 전송하고 각 근거의 원문·질문·판단 ID는 독립 유지
- 모델 준비는 별도 세션 명령에서 실행, MCP 시작이나 일반 검색에서 GPU 자동 생성 금지
- 요청 전체 판단 예산 2초, 같은 평가 ID 자동 재시도 금지
- 제한 시간 초과 시 판단 보류와 GPU 세션 종료, 검색한 원문 근거는 유지
- 기본 최대 실행 시간 600초·유휴 시간 90초, 허용 상한 각각 1100초·180초
- 명시적 중지·준비 중 취소·유휴 종료·최대 시간 종료와 원격 함수의 1200초 상한 적용
- 공개 HTTP 엔드포인트·영구 배포 없음, 로컬 연결은 임의 토큰으로 인증

세션 파일은 `.local/state/runtime/modal-session.secret.json`, 상태·로그는 같은 폴더의 별도 파일
인증 토큰·Modal 자격 증명 파일은 문맥 수집 대상에서 제외
MCP의 `openWorldHint`는 원격 판단을 사용할 수 있는 문맥·후보 추천·읽기 전달 도구에 표시

## 토큰 검사

기존 별도 tokenizer 계산과 추론 usage 사이의 1토큰 차이는 vLLM `/tokenize` 경로로 해결
실제 OpenJev 요청의 메시지와 template 옵션을 같은 서버 renderer에 전달하고 추론 결과의 `prompt_tokens`와 매회 비교
기본 입력 한도 4096토큰과 서버 canvas 여유를 추론 전에 검사하고 초과 시 `input_incomplete` 반환
원문·지시·선택지를 자르는 우회 동작 없음

`verified_read_tokens`는 재판독을 포함한 실제 확인 내역, `processed_input_tokens`는 그 합계
OpenJev의 자체 `input_tokens`는 자동 재판독을 제외할 수 있으므로 두 수치를 구분
이 수치는 Codex가 읽는 토큰이나 Modal 청구 토큰 수를 의미하지 않는 범위

근거는 고정된 [vLLM tokenize 계약](https://github.com/razorback16/vllm/blob/baa833874881ba62cef99e0c5b716fb136c4a009/vllm/entrypoints/serve/tokenize/protocol.py)과 실제 추론별 일치 검사

## 실행

현재 Windows 소스 작업 폴더의 `.venv`, `.local/modal-venv`, `.local/modal.toml`과 준비된 모델 캐시 사용
이미지·모델·FlashInfer 캐시는 [기존 Modal 준비 기록](openjev-modal-evaluation.md)의 고정 revision 재사용
실행 스크립트는 저장소에서 사용하는 도구이며 독립 wheel만 설치한 환경의 GPU 준비는 미제공

```powershell
.\.local\modal-venv\Scripts\python.exe scripts\configure_modal_profile.py
```

생성된 `.local/openjev-modal-profile.json`의 절대 경로를 프로젝트 설정 `[engine].profile_file`에 지정하고 `state = "shadow"` 유지
실행 코드가 바뀌면 profile을 다시 생성하고 기존 평가 fingerprint와 구분

`work_open`으로 작업을 생성하거나 복원한 뒤 응답의 실제 ID 사용

```powershell
$workId = "work_open에서 반환된 work_id"
.\.venv\Scripts\python.exe -m jev_context session-start --config .local\project.toml --work-id $workId
.\.venv\Scripts\python.exe -m jev_context session-status --config .local\project.toml
```

`starting` 또는 `preparing`에서 `ready`로 바뀐 뒤 MCP `context_prepare` 호출
준비 시간은 별도 기록이며 첫 문맥 요청의 2초 예산에서 제외
준비 중이나 종료 후에도 저장·검색 사용 가능, 모델 판단은 unavailable 또는 abstained로 명시
다른 work ID를 처리하려면 현재 세션을 중지한 후 해당 작업으로 새 세션 시작

```powershell
.\.venv\Scripts\python.exe -m jev_context session-stop --config .local\project.toml
```

세션 중지 응답은 `stopping`, 실제 자원 종료 확인은 Modal 컨테이너 목록과 작업 상태 조회
MCP 재연결은 GPU를 새로 생성하지 않으며 살아 있는 같은 작업 세션에 재접속

## 재현 가능한 검증

실제 Modal 세션이 ready인 동안 다음 명령 실행
이 검사에서는 고정된 진단 자료와 허용된 프로젝트 문서 3개만 사용

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\evaluate_modal_session.py --config .local\project.toml --work-id $workId --output .local\evaluations\openjev-session.json
```

[진단 자료](../models/openjev-challenge.json)는 부정·예외·범위 변경·상충·오래된 기록·정보 부족·삽입 지시·긴 문서 끝의 제한을 포함한 한영 25쌍
에이전트가 작성한 개발 자료이며 사람이 검토한 heldout 자료가 아닌 구분
영어판은 대응 문장 비교이며 번역 모델의 정확도·지연을 측정한 결과가 아닌 범위

검사는 실제 MCP 클라이언트로 저장 작업 복원·문서 등록·원문 보존·모델 관찰·후보 추천·읽기 전달을 실행
Desktop 현재 대화의 도구 노출과 호스트 작업 정답률·토큰 절감·전체 비용 개선은 별도 검증 대상
기존 목적별 승격 조건인 human-reviewed heldout·중요 회귀 0건·품질 및 효율 통과·일치 fingerprint 유지
