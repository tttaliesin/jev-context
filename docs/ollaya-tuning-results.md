# Ollaya 2차 튜닝·모델 비교 결과

2026-09-28. [계획](ollaya-tuning-plan.md) 작성 → [자체 검토·수정](ollaya-tuning-review.md) → 입력·규칙 고정 → 개발 비교 → 선택 고정 → 새 최종 진단 순으로 실행했다.

## 무엇을 알아냈나

**설정 조정과 작은 모델 교체만으로 현재 SemIf를 대체할 수준에는 이르지 못했다.** 새 한국어 60개에서 Laya는 기본·선택 설정 모두 25개, decider 0.8B는 36개, 현재 SemIf는 56개를 맞혔다. decider는 Laya보다 근거 관계와 도구 적합성을 잘 구분했지만, 문서 관련성을 자주 보류했다. 빠른 응답의 장점은 확인했다.

이 결과는 ‘Ollaya는 튜닝할 수 없다’는 뜻이 아니다. Ollaya는 실행기이며 이번에는 입력·질문·모델 선택을 바꿨다. **가중치 추가 학습은 하지 않았다.** 정답 사례를 학습시켜 개선할 경로와 필요한 데이터·GPU·배포 작업은 [추가 학습 준비 검토](ollaya-finetuning-readiness.md)에 정리했다.

제품은 SemIf shadow 구성을 유지한다. SemIf에도 중요 오류가 1개 있었으므로 이를 자동 판단에 충분히 안전한 구성으로 표현하지 않는다. 제품 어댑터·상시 실행 모델·새 연결 레이어를 추가하지 않았다.

## 평가 범위와 선택 과정

이전 60개는 모두 개발 자료로 재사용했다. 최종 진단은 별도로 작성한 60개로, 관련성·근거 관계·도구 적합성 각 20개다. 정답과 근거, 중요 사례 표시를 추론 전에 고정했고 모델 입력에는 넣지 않았다. 세 입력 파일의 SHA-256은 [frozen.json](../evaluations/ollaya-tuning/frozen.json)에 있다.

모두 **에이전트가 작성한 개발 진단**이다. 사람 검토를 거친 독립 평가나 실제 코딩 생산성 검증은 아니다. 두 자료는 같은 프로젝트의 판단 유형을 다루므로 의미 수준의 독립성에도 한계가 있다. 1차와 최종 사례가 달라 점수 증감으로 직접 비교하지 않는다.

| 개발 구성 | 정답 / 60 | 중요 오류 | 사례 p95 |
|---|---:|---:|---:|
| Laya: 기존 질문 + JSON | 27 | 14 | 204 ms |
| Laya: 한국어 질문 + JSON | 29 | 10 | 185 ms |
| Laya: 한국어 질문 + 항목별 텍스트 | 24 | 10 | 171 ms |
| **Laya: 짧은 영어 질문 + 항목별 텍스트** | **22** | **8** | **137 ms** |
| Laya: 영어 질문 + 중립 선택 ID | 17 | 11 | 136 ms |
| Laya: 예/아니오/불명 하위 질문 + 답 합성 | 21 | 10 | 476 ms |
| **decider 0.8B: 한국어 질문 + 항목별 텍스트** | **33** | **7** | **713 ms** |
| decider 0.8B: 영어 질문 + 항목별 텍스트 | 40 | 9 | 873 ms |

굵은 행이 사전 규칙으로 선택된 구성이다. **중요 오류 최소 → 전체 정답 최대 → p95 최소** 순서이므로 가장 높은 정답률의 구성과 다르다. Laya의 29/60, decider의 40/60을 숨기거나 최종 검증을 마친 점수처럼 제시하지 않는다. 최종 결과를 보고 선택을 바꾸지 않았다. [개발 결과와 선택 원문](../evaluations/ollaya-tuning/run-20260928/select.json)

두 선택 후보 모두 수락 20개 이상·수락 정답률 95% 이상·중요 오수락 0개를 만족하는 개발 임계값을 찾지 못했다. 따라서 `threshold: 1.01`로 **전부 보류**를 고정했다. 이는 성공적인 자동화가 아니라, 이번 점수만으로 답을 받아들이기 어렵다는 결과다. 아래 정답률은 보류 필터 적용 전 분류 결과다.

## 새로운 최종 진단

| 구성 | 정답 / 60 | 중요 오류 | 중요 오수락 | p50 | p95 |
|---|---:|---:|---:|---:|---:|
| Laya 기본 | 25 (41.7%) | 14 | 12 | 156 ms | 270 ms |
| Laya 선택 설정 | 25 (41.7%) | 11 | 7 | 123 ms | 152 ms |
| decider 0.8B 선택 설정 | 36 (60.0%) | 3 | 3 | 627 ms | 749 ms |
| 현재 SemIf | 56 (93.3%) | 1 | 1 | 894 ms | 983 ms |

‘중요 오류’는 사전에 중요하다고 표시한 사례에서 정답과 다른 모든 분류이며 보류도 포함한다. ‘중요 오수락’은 그중 `insufficient_evidence`로 보류하지 않고 잘못 답한 수다. 표의 오수락은 추가 임계값을 적용하지 않은 값이다. 선택 후보에 고정 임계값을 적용하면 수락·오수락 모두 0개이며 처리율도 0%다.

| 목적별 정답 / 20 | Laya 기본 | Laya 선택 | decider 선택 | SemIf |
|---|---:|---:|---:|---:|
| 문서 관련성 | 7 | 6 | 5 | 19 |
| 근거와 주장의 관계 | 8 | 8 | 15 | 19 |
| 도구의 작업 적합성 | 10 | 11 | 16 | 18 |

decider는 관련성이 있는 10개 중 9개를 보류하고 1개를 무관하다고 분류했다. 이번 설정이 관련성 용도로 유용하다고 볼 수 없다. 근거 관계·도구 적합성은 상대적으로 나았지만, 이 최종 표를 보고 목적별로 다른 모델을 고르는 재튜닝은 하지 않았다.

대표 사례는 다음과 같다.

- **Laya**: 다른 PC에서 응답받은 로그를 ‘외부 접근이 불가능하다’는 주장의 지지로 잘못 분류했다. 현재 작업 외 기록 접근이 금지됐는데 범위를 제한할 수 없는 검색 도구를 적합하다고 판단했다.
- **decider**: ‘검사 시작’, ‘개발 브랜치 수정’, ‘수정 이전 테스트’를 현재 성공·운영 적용·수정 후 정상의 부분 지지로 처리했다. 해당 시점·대상에 관한 근거가 부족하다는 구분이 남았다.
- **SemIf**: timeout 로그만 있는 상황에서 ‘캐시 손상이 원인’이라는 주장을 근거 부족 대신 반박으로 분류했다. 증거가 없다는 것과 반대 증거가 있다는 것을 혼동한 사례다.

기준은 SemIf 이상 정답·중요 오류 0·p95 2초 이하였다. 선택한 두 Ollaya 구성은 속도 조건만 충족했다. 모든 개발 480개·최종 240개 사례 실행에서 오류·시간 초과·미실행은 0개였다. 분해 구성은 여러 하위 호출을 한 사례로 집계했으며 가짜 최종 분류 확률을 만들지 않았다. 전체 confusion, 원래 확률, 하위 답, NLL은 [원시 결과](../evaluations/ollaya-tuning/run-20260928/index.json)에 있다.

## 자원·버전과 종료 확인

동일 PC(Intel Core Ultra 7 258V, RAM 32 GB)에서 Ollaya v0.7.3 CPU와 현재 SemIf OpenVINO Qwen3.5-4B INT8 Intel GPU 구성을 비교했다. 같은 장치·정밀도의 순수 런타임 비교가 아니라 실제 사용 가능한 구성 비교다. 한 번에 모델 하나만 로드했다.

| 최종 진단 준비·자원 | Laya | decider 0.8B | SemIf |
|---|---:|---:|---:|
| 준비 시간 | 2.74초 | 8.03초 | 43.12초 |
| 로드 직후 프로세스 트리 working set | 1.38 GiB | 3.07 GiB | 6.78 GiB |

준비 시간은 OS 파일 캐시를 비운 cold boot가 아니다. SemIf는 기존 OpenVINO 캐시를 썼다. working set은 콘솔과 자식 프로세스를 포함한 한 시점의 합계이며 peak·공유 페이지를 제외한 비용·GPU 전용 메모리는 아니다. 각 구성은 한 번씩 실행했으므로 작은 지연 차이를 일반화하지 않는다.

`decider:2b`는 준비 시 가용 RAM 10.72 GiB로 계획의 12 GiB 조건에 못 미쳐 다운로드·추론하지 않았다. 품질 실패가 아닌 자원 조건 제외다. 4B decider는 계획에서 제외했다. [공식 모델 안내](https://ollaya.dev/library/decider)의 CPU 메모리 주의를 참고했으며 큰 모델의 한국어 성능을 이번 결과로 추정하지 않는다.

모델 manifest·모든 blob의 실제 hash와 길이를 추론 전에 확인했다. [준비 기록](../evaluations/ollaya-tuning/run-20260928/prepare.json)이 이번 실행의 모델 lock이다. Laya 기본 온도는 1.0, decider의 choice 온도는 배포본 1.03이며 이번에 다시 학습·보정하지 않았다. [질문 토큰 감사](../evaluations/ollaya-tuning/question-budget-audit.json)에서 23개 개별 질문 구성의 head·선택지 잘림이 없음을 확인했고, `/v1/systemone`의 state 잘림 거절 계약을 사용했다.

모델별 `/api/ps`가 비었음을 확인한 뒤 다음 모델을 실행했다. SemIf `close()` 뒤 worker가 없었고, 종료 시 소유 PID와 실행 경로를 대조해 실험 Ollaya daemon도 종료했다. [종료 기록](../evaluations/ollaya-tuning/run-20260928/cleanup.json). 기존 제품 프로필·DB·모델은 보존했다.

## 적용한 파일과 재현

[실행 도구](../scripts/evaluate_ollaya_tuning.py), [회귀 테스트](../tests/test_ollaya_tuning.py), [고정 입력과 후보](../evaluations/ollaya-tuning/frozen.json), [선택 설정](../evaluations/ollaya-tuning/selected-recipes.json), [원시 단계별 결과와 hash](../evaluations/ollaya-tuning/run-20260928/index.json)를 저장했다. 한영 README에서 이 결과와 학습 준비 문서로 연결한다. 모델 가중치와 실행 바이너리는 Git에서 제외된 `.local/ollaya-evaluation/`에 남겼다.

[1차 재현 안내](ollaya-evaluation-results.md#재현)의 검증된 portable v0.7.3, 격리 포트·CPU·모델 경로를 사용한다. 아래 `$taskDaemonId`에는 **직접 시작한 격리 daemon의 PID**를 넣는다. 다른 Ollaya 서비스의 PID를 넣지 않는다. 새 출력 디렉터리에서 순서대로 실행한다.

```powershell
$taskOutput='.local/ollaya-tuning/reproduction-2'
# $taskDaemonId = 직접 시작한 격리 daemon의 PID
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya_tuning prepare --output $taskOutput --server-pid $taskDaemonId
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya_tuning development --model laya:multilingual --output $taskOutput --server-pid $taskDaemonId
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya_tuning development --model decider:0.8b --output $taskOutput --server-pid $taskDaemonId
# prepare에서 decider:2b가 ready라면 같은 development 명령을 그 모델로 추가한다.
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya_tuning select --output $taskOutput --server-pid $taskDaemonId
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya_tuning validation --model laya:multilingual --output $taskOutput --server-pid $taskDaemonId
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya_tuning validation --model decider:0.8b --output $taskOutput --server-pid $taskDaemonId
# select에서 decider:2b가 selected라면 같은 validation 명령을 그 모델로 추가한다.
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya_tuning semif --output $taskOutput --server-pid $taskDaemonId
```

각 단계의 종료 코드와 `prepare`의 ready/excluded를 확인하고 다음 단계로 진행한다. 종료·중단한 출력은 덮어쓰지 않는다. `prepare`는 그 실행에서 받은 모델을 hash로 고정하므로 **이번 보고서와 재현 실행의 prepare identity를 비교해야 같은 가중치 비교라고 할 수 있다**. 태그가 갱신되었거나 2B의 자원 조건이 달라지면 새 실험으로 구분한다.

`selected-recipes.json`의 설정은 `evaluate_case(client, model, configuration, case)`로 실행한다. 이 함수는 필요한 state만 전달하고 필드 변환·하위 답 합성을 수행한다. Modelfile만으로 이 동작을 재현할 수 없다. 선택 설정은 재현용이며 자동 수락 가능한 제품 프리셋이 아니다.

## 검사 기록

Ruff lint·format과 전체 pytest **310 passed / 2 skipped**가 통과했다. 추가 테스트 12개는 필드 보존, 제약 우선 합성, 중요 오류를 우선한 후보 선택, 실패·미실행 분모, 하위 질문의 공통 시간 제한, 자료 분리와 선택지 매핑을 검사한다. 제품 정확도를 이 단위 테스트로 증명한 것은 아니다.

별도 검산에서 원시 단계 파일 8개의 hash, 720개 사례 행의 집계, 개발 hash와 선택·최종 실행 순서, 보류 집계, 고정 입력 hash, 문서 상대 링크를 확인했다. Git에는 고정 입력의 LF와 원시 결과의 원래 bytes를 보존하는 속성을 추가했다.

```powershell
$env:PYTEST_ADDOPTS='--basetemp=.t/ot2full1 -p no:cacheprovider'
.venv/Scripts/python.exe -X utf8 scripts/check.py
```

재검사 시 새 임시 경로를 사용한다. 2026-09-28 사용자 요청으로 원격 검사 워크플로를 제거했다. 이후 재검사는 [개발 안내](development.md)의 로컬 명령을 사용한다.
