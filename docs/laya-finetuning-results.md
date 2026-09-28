# 실제 가중치 학습·Ollaya 반입 예비 실행

2026-09-28. [실행 계획과 사전 자체 검토](laya-finetuning-plan.md)에 따른 기록. **기술 예비 실행은 성공, 실제 정답 데이터에 의한 본 학습·독립 성능 검증은 미완료**다.

## 여기서 말하는 ‘정답’

모델이 배울 것은 상황에 맞는 판단이다. ‘상황 + 질문 + 올바른 판단’을 한 쌍으로 모아 학습한다.

| 상황과 질문 | 정답 초안 | 이유 |
|---|---|---|
| 테스트 시작 로그만 있는데 ‘테스트 통과’ 주장을 뒷받침하는가? | 근거 부족 | 완료 결과를 아직 관측하지 않았음 |
| 파일 삭제 금지 조건인데 도구가 실행 즉시 파일을 지우는가? | 부적합 | 사용자의 제약을 위반함 |
| DB 잠금 오류를 찾는데 문서가 화면 잠금 기능에 관한 내용인가? | 관련 없음 | ‘잠금’이라는 단어만 같고 필요한 정보가 다름 |

이 표는 에이전트가 작성한 설명용 초안이다. 사람이 확인한 정답으로 표시하지 않았다. 에이전트가 예시와 판정 초안을 만들 수 있지만, 같은 작성자가 만든 문제·정답만으로 실사용 성능을 확정하면 오류나 편향을 놓칠 수 있다. 그래서 이전 계획에는 사람 검토와 별도 시험 자료가 포함돼 있었다.

후속으로 [판정 기준과 검토 사례](laya-label-review.md)를 작성했다. 기본 30개(실제 결과 재구성 9개·문서 기반 가정 7개·구성 사례 14개)의 보완 의견 11개를 반영하고, 조건을 바꾼 구성 비교 사례 14개를 더해 총 44개다. 원래 30개의 라벨은 유지했다. 모든 판정은 에이전트 초안이고 사람 검토 대기다. 한 제작 작업의 자료로 묶어 분할하지 않으며, 기존 230개 실제 기록 기반 후보에 합산하거나 본 학습·독립 시험에 사용하지 않는다.

## 이번에 실제 수행한 것

| 단계 | 실행 결과 |
|---|---|
| 실제 프로젝트 기록 조사 | 현재 DB를 읽기 전용으로 확인. 문맥 묶음 69개, 과거 판단 19개 |
| 학습 후보 준비 | 출처 원문과 작업 목표를 묶은 검토 후보 230개. 정답·중요 여부는 비워 둠 |
| 검토·분할 도구 | 로컬 HTML 검토 화면과 JSONL 계약, 출처·작업·동일 원문을 묶는 분리 검사 구현 |
| 기술 예비 학습 | 공개 개발 60개로 판단 부분 100 step 학습. 31개 텐서의 실제 bytes 변경 확인 |
| 저장·재로드 | 별도 checkpoint에 저장 후 새 Python 프로세스에서 로드. 12개 입력의 선택 유지 |
| Ollaya 반입·검증 | 격리 모델 `jev-laya:pilot`로 로드. 12개 입력의 선택·표시 확률이 Python과 일치 |
| 본 학습·보정·새 최종 평가 | **미실행**. 사람 정답과 독립 자료가 아직 없음. 예비 실행으로 대체하지 않음 |

학습한 것은 약 **1,477만 개 파라미터**인 decision head·type embedding·scorer다. 전체 약 3억 2,191만 개 중 encoder와 act head는 고정했다. CPU float32, batch 1, AdamW 1e-5, cross entropy, seed 20260928로 진행했다. 공식 전체 RLCD 학습을 재현한 것은 아니다.

설정 호환성을 수정한 실행은 준비·학습·저장·검사를 포함해 **69.70초**, 학습 step 중앙값은 **0.460초**였다. 이는 기존 자료 60개를 반복한 작은 CPU 예비 실행이다. GPU 학습 시간이나 전체 모델 학습 비용으로 환산하지 않는다. 학습 자료를 다시 풀어서 얻는 정답률은 성능 증거로 제시하지 않았다.

저장 시 원래 safetensors의 dtype과 offset을 유지했다. 학습 중 float32 결과와 저장 후 재로드 결과는 12개 선택이 같고 최대 확률 차이가 0.0001이었다. 이후 **저장된 checkpoint를 기준**으로 Ollaya를 대조했다. 두 실행의 반올림된 출력 확률 최대 차이는 0.0이었다. 임의 입력 전체나 반올림 전 bit 단위 동등성을 보장한다는 뜻은 아니다.

## 첫 실패와 수정

첫 학습은 완료됐지만 Python과 Ollaya의 선택은 12개 중 7개만 같았고 최대 확률 차이는 **0.5912**였다. 이 실행은 반입 검증 실패로 보존했다.

모델 파일은 Transformers 5 형식의 `rope_parameters.sliding_attention.rope_theta=160000`을 포함한다. 설치된 Transformers 4.56.2는 기존 `local_rope_theta` 필드를 사용하며, 해당 필드가 없으면 기본값 10000을 사용한다. 같은 가중치라도 위치 정보를 계산하는 설정이 달라져 다른 모델 계산이 된 것이다. 설치된 버전의 코드와 Ollaya graph의 모델 설정을 대조했다. [Transformers 4.56.2 설정](https://github.com/huggingface/transformers/blob/v4.56.2/src/transformers/models/modernbert/configuration_modernbert.py)

원래 모델은 보존하고 별도 참조 설정에 `global_rope_theta=160000`, `local_rope_theta=160000`을 명시했다. 지원하는 기본 rotary 형식·attention 층 배열인지 검사하고 충돌하면 거절하도록 했다. 원래 가중치부터 다시 100 step 학습한 뒤 재로드·반입 검증을 통과했다. 첫 실행을 덮어쓰거나 결과를 숨기지 않았다.

같은 문제가 제품의 선택형 Laya worker에서 조용히 재발하지 않도록, checkpoint 값과 로드된 legacy rotary 값이 다르면 준비 단계에서 거절하는 검사도 추가했다. 현재 SemIf 설정은 변경하지 않았다. 과거 Python Laya 결과의 해석에는 이 호환성 문제를 고려해야 한다. 앞선 Ollaya·SemIf 비교는 이 Python 참조 경로를 쓰지 않아 해당 결과를 수정하지 않았다.

## 학습한 모델을 어떻게 넣었나

Ollaya v0.7.3의 동일 구조 fp32 ONNX graph를 재사용하는 제한된 패키징 경로를 구현했다. 새 모델 구조를 임의 변환하는 범용 기능은 아니다.

- 외부 가중치를 참조하는 **168개 tensor**의 이름·shape·dtype·offset·길이를 기존 safetensors header와 대조했다.
- 새 가중치 hash로 외부 참조를 바꿨다. graph에 들어 있던 `scorer.3.weight`의 전치 상수 1개도 원래 값이 일치하는지 확인한 뒤 새 값으로 바꿨다. 이 작업을 생략하면 일부 가중치만 교체되는 문제가 생긴다.
- CPU fp32 graph만 별도 저장소에 패키징했다. tokenizer·decision·기본 calibration·라이선스는 보존했다. 추가 학습 후 확률 보정을 완료했다고 주장하지 않는다.
- ONNX 검사 후 전용 포트 11438에서 실행하고 PyTorch 재로드 결과와 대조했다. 검증 후 모델 해제와 소유 daemon 종료를 확인했다.

내부 manifest 계약에 의존하는 실험용 경로다. Ollaya 버전·graph·모델 구조가 달라지면 다시 검증해야 한다. 기존 Modelfile만으로 임의 가중치 반입이 해결된다는 의미가 아니다.

## 데이터가 아직 준비되지 않은 이유

과거 판단 19개에는 원래 모델 입력 전체가 남아 있지 않아 동일 요청을 재구성할 수 없었다. 모델의 이전 예측을 정답으로 복사하지 않았다. 대신 원문 출처와 당시 작업 목표를 연결한 **새 검토 후보**를 만들었다. 이는 실제 모델 요청 230개를 복구했다는 뜻이 아니다.

후보 230개는 관련성 목적만 포함하고, 출처·작업이 서로 연결돼 **하나의 그룹**이 된다. 이를 무작위로 나누면 같은 문서·작업이 학습과 시험에 함께 들어간다. 따라서 계획의 600개 검토 후보, 세 목적, 독립적인 학습·개발·보정·최종 자료 조건을 충족하지 못했다. 준비 검사는 `not_ready`로 거절했다.

검토 화면은 프로젝트 로컬의 `.local/laya-finetuning/data/review.html`에 있다. 사용자가 직접 정답·중요 여부·검토자를 입력하고 완료한 항목만 `human_reviewed`로 내보낸다. 모델이 이 상태를 자동으로 채우지 않는다. 개인 작업 원문이 포함된 후보 파일은 원격 Git에 올리지 않았다.

이후 사용자는 실제 업무 기록으로 진행하도록 명시했다. 합성 자료로 부족량을 채우지 않으며 아래 후속 조사와 수집 경로 보완을 진행했다.

## 후속: 실제 기록 확보와 근거 대조

2026-09-28, `6dea809` 이후 실행. [보완 계획과 사전 자체 검토](laya-finetuning-plan.md#2026-09-28-실제-업무-자료부터-비교-평가까지)에 따라 현재 프로젝트 DB를 읽기 전용 트랜잭션으로 조사했다. 결론은 **개선 미확인**이다. 독립 정답 자료가 준비되지 않아 기준 성능 측정·본 학습·학습 후 비교는 실행하지 않았다. 이전 예비 학습을 다시 돌려 이 단계를 대체하지 않았다.

| 조사 항목 | 실제 결과 |
|---|---|
| 프로젝트 작업 / 이벤트 / 문맥 묶음 | 23 / 306 / 77개. 이벤트 수는 이번 기록 시점에 따라 늘어날 수 있음 |
| 과거 모델 판단 | 19개, 모두 원래 state·질문 전체가 없어 원요청 복구 0개 |
| 저장된 실행 증거 | 81개. 주장·명령·결과 요약·대상 revision을 보존 |
| 새 검토 후보 | 위 81개에서 근거 관계 질문을 재구성. 실제 모델 요청이나 사람이 확인한 정답 81개라는 뜻이 아님 |
| 현재 연결 그룹 | 15개. 작업·출처·같은 원문을 연결한 그룹 수이며, 15개 모두 독립 시험에 적합하다는 뜻은 아님 |
| 목적별 후보 | 근거 관계 81개, 관련성·도구 적합성 0개 |
| 의미 검토 초안 | 81개 모두 이유 작성: 지지 49·일부 지지 31·보류 1. 저장된 보고와 주장의 관계에 대한 에이전트 초안 |
| 원시 파일 대조 | 위 81개 중 6개에 대해 파일 10개 대조. 나머지 75개는 원시 파일을 독립 대조하지 않았다고 표시 |
| 사람 검토 정답 | 0개. 자동 사실 확인·에이전트 초안과 구분 |

모든 원기록의 복사본과 hash를 후보에 연결했다. 모델의 과거 선택을 정답으로 복사하지 않았다. 원시 결과가 남은 사례부터 대조한 후 81개 전체의 의미 관계 초안을 작성했고, **개발 중 내용을 살펴본 81개와 같은 그룹은 최종 시험에 재사용하지 않도록 표시**했다. 기존 44개 설명용 사례와 기존 230개 목표/원문 후보에 합산하지 않는다. 지지·일부 지지에 편중돼 있고 반박·무관 사례가 없으므로 이 자료만으로 전체 라벨 성능을 평가할 수도 없다.

| 사례의 실제 기록 | 확인한 사실과 판정 초안 |
|---|---|
| 초기 형식 검사 실패 (`evidence-0f51a9…`) | `engines.py`의 형식 실패 로그 존재. 새 기준 등록까지는 결과에 없어서 일부 지지 |
| 긴 경로 검사 실패 (`evidence-eed5b8…`) | 실패 2·통과 197·건너뜀 1 일치. 경로 길이 조사와 수정 후 개별 검사는 이 로그만으로 확인 불가: 일부 지지 |
| 후속 전체 검사 (`evidence-e30df5…`) | 과거 로그의 199개 통과 확인. 당시 검사 통과 주장을 지지하며 현재 코드의 재검증은 아님 |
| UI 17개 검사 (`evidence-9ffa95…`) | 같은 파일 경로에 현재는 2026-09-27의 58개 검사 결과가 있음. 원래 17개 결과를 검증할 수 없어 보류. 과거 보고가 거짓이라고 단정하지 않음 |
| 100 step 예비 학습 (`evidence-a9bc79…`) | 고정 index hash와 원시 파일에서 31개 텐서 변경·12/12 반입 일치 확인: 기술 예비 실행 주장 지지 |
| 첫 반입 실패와 수정 원인 (`evidence-8fa782…`) | 첫 7/12 일치·확률 오차 0.5912와 검사 실패 확인. 후보 요약은 rotary 원인 전체를 직접 설명하지 않아 일부 지지 |

사람 판단이 필요한 모호한 항목은 UI 17개 기록 한 건을 우선 표시했다. 필요한 것은 **당시 원본 결과 파일 또는 그 hash와 연결된 사본**이다. 기억으로 정답을 선택하는 것으로 대체하지 않는다. 다른 80개도 에이전트 초안이며 사람이 승인한 정답으로 표시하지 않았다. 원시 파일 75개 미대조는 별도 확인 과제다. 이 자료를 검토해도 관련성·도구 적합성과 새 독립 시험 자료 부족은 해소되지 않으므로 사용자에게 81개를 일괄 검토하도록 요구하지 않는다.

### 수집 경로와 준비 검사 수정

- 새 모델 판단은 실제 요청 state·질문·프로필·시점·결과를 기존 packet에 보존한다. 모델에 전달하기 전에 복사해 엔진의 내부 변경으로 원입력이 바뀌지 않게 한다. 시간 부족으로 전달하지 않은 요청은 따로 표시한다.
- 도구 적합성도 기존 packet 저장소에 저장한다. 일반 응답에는 상세 요청을 포함하지 않으며 `work_inspect(view=judgments)` 또는 로컬 추출에서 확인한다. 읽기 전용 설치에는 저장하지 않는다. 선택에서 제외한 원문도 출처 참조를 유지해 삭제·접근 정책을 적용한다.
- `prepare_laya_training_data.py audit`는 보존된 실제 요청과 저장 보고의 재구성을 구분해 추출한다. DB 원문은 변경하지 않는다. 검토 화면은 전체 입력과 근거를 보여주며 질문을 수정하지 못하게 하고, 사람 검토에는 이유를 요구한다.
- 준비 검사는 설명용 자료, 원입력/원기록 hash 변경, 사람 정답·근거 누락, 노출된 그룹의 최종 시험 재사용을 거절한다. source ID와 경로를 함께 연결하고 원본/비교 관계도 같은 그룹으로 묶는다. 최종 시험의 목적별 30개 하한도 검사한다.

실제 81개로 준비 검사를 실행한 결과 `not_ready`다. 검토 전 그룹 배정의 분포는 학습 74·개발 4·시험 3·보정 0개이며 모두 근거 관계다. **이 배정은 준비 상태 진단일 뿐 확정 분할이 아니며 frozen 파일은 생성하지 않았다.** 사람 정답, 다른 두 목적, 독립 보정·시험 자료가 부족하다. 현재 모델과 제품 설정은 유지했다. 새 저장 동작은 수정된 코드로 실행한 프로세스부터 적용되며, 이미 실행 중인 Desktop MCP는 재시작 전까지 예전 코드를 사용한다.

원문과 결과는 로컬 `.local/laya-finetuning/actual-20260928/`의 `review-candidates.jsonl`, `review.html`, `grounding-review.json`, `readiness/prepare.json`에 보관한다. 원격 저장소에는 개인 원문을 올리지 않는다.

후속 코드 검사는 Ruff lint/format과 전체 pytest **326 passed / 2 skipped**를 통과했다. 원요청 복사, 미전달 요청 구분, 제거된 근거의 참조 보존, 도구 판단 저장, 원본/비교 및 source ID 연결, 미검토/변경 입력/시험 노출 거절을 확인했다. 처음 전체 검사에서는 실제 요청이 없는 모의 호출까지 저장하려다가 기존 평가 fixture 1개가 실패했다(323 passed / 2 skipped). 저장할 요청이 있을 때만 기록하도록 고친 뒤 위 전체 검사를 통과했다. 모의 채점 기준이나 과거 동결 결과는 수정하지 않았다.

실제 Edge의 로컬 검토 화면에서도 원입력 읽기 전용·전체 조건 표시·이유 필수·입력 보존·변경된 입력의 가져오기 거절을 확인했다. 화면 검사는 임시 브라우저 상태에서만 수행했고 실제 후보에 사람 정답을 저장하지 않았다. 원격 CI와 실제 모델 실행은 하지 않았다.

```powershell
# 기존 결과와 다른 새 출력 경로 사용
.venv/Scripts/python.exe -X utf8 scripts/prepare_laya_training_data.py audit --db .local/state/projects/project-89095613680449da8ab8c438dfcf826a/state.sqlite --output .local/laya-finetuning/actual-next
.venv/Scripts/python.exe -X utf8 scripts/prepare_laya_training_data.py prepare --reviewed .local/laya-finetuning/actual-next/review-candidates.jsonl --output .local/laya-finetuning/actual-next/readiness
```

남은 입력은 **실제 질문·후보·당시 제약·결과가 함께 기록된 관련성/도구 적합성 사례와, 기존 자료와 출처가 분리된 세 목적의 검토 자료**다. 저장 기능을 위해 새 요청을 억지로 생성하지 않았고, 제품 수명·권한 검사의 모의 사례를 실제 업무 자료로 세지 않았다. 충분한 실제 기록과 정답 근거가 갖춰지면 문서에 정한 기준 측정 → 별도 후보 학습 → 같은 독립 시험 비교 순서로 진행한다.

## 2026-09-29 연결·저장 검증과 자료 재검토

결론은 **개선 미확인**이다. 새 코드의 실제 stdio 저장 경로는 통과했지만 **현재 Codex에 연결된 구 서버는 재시작이 필요하다.** 두 결과를 합쳐 연결 완료로 보고하지 않는다.

### 현재 연결과 별도 서버의 차이

| 확인 대상 | 실제 관측 |
|---|---|
| 현재 Codex 도구 | runtime `4910f59c…`, 시작 `2026-09-28T02:02:25Z`(한국 11:02), `restart_required` |
| 현재 프로세스 | Codex app-server PID 573864 → venv 실행기 583180 → 실제 Python MCP 602552. 9월 29일 최종 OS 조회에서도 남아 있음 |
| 실행 코드 / 디스크 | `12573682…` / `3d568a5a…`. 시작 시 코드 해시가 다르므로 일부 지연 import가 새 코드여도 갱신 완료로 볼 수 없음 |
| 현재 연결의 실제 요청 | 관련성·근거 관계를 포함한 `context_prepare`, 도구 적합성 `capability_recommend`를 직접 호출. 둘 다 `engine_busy`로 보류. 문맥 packet은 원요청이 없고 도구 판단은 packet ID가 null |
| 별도 SDK stdio 서버 | 동일 제품 설정·DB·로컬 모델로 실행. build와 disk가 모두 `3d568a5a…`, `matches_disk` |
| 실제 모델 판단 | 관련성·근거 관계·도구 적합성 모두 `observed`. 품질 평가나 사람 정답 일치 검사는 아님 |
| 저장·복원 | 두 성공 packet의 원입력·질문·프로필·시점·결과와 DB/`work_inspect` 일치. 첫 서버 종료 후 새 runtime `d588b3d1…`에서 같은 packet 전체가 동일함을 확인 |
| 종료 | 실제 모델 하나만 실행. 최종 OS 조회에 모델 worker·broker가 없고 구 MCP 두 Python 프로세스만 남음 |

구 서버는 이전 코드 기준 프로필 fingerprint `9d573676…`를 유지하고 새 서버는 `3dc54b2c…`다. fingerprint에는 판단/실행 코드 hash도 포함되며 모델 가중치를 바꿨다는 뜻은 아니다. 다른 fingerprint의 broker가 점유한 동안 구 연결은 `engine_busy`로 거절되어 두 모델이 동시에 실행되지 않았다. 새 서버에서 모델 준비 총 49.688초를 관측했지만 이는 이번 저장 경로 검증의 준비 시간이고 기준 성능 비교 수치가 아니다.

현재 Codex 연결의 재시작 후 세 목적 재검증은 **미완료**다. 현재 호스트의 MCP 연결을 다시 여는 도구가 제공되지 않아 임의로 프로세스를 죽이지 않았다. [공식 MCP 안내](https://developers.openai.com/codex/mcp)의 MCP 서버 재시작 기능을 사용할 수 있으면 `jev_context`만 재시작한다. 해당 기능이 없으면 **Jev 관리 앱이 아니라 Codex 앱을 완전히 종료 후 다시 열어야 한다.** 이후 현재 대화의 `workspace_status`에서 새 instance와 `matches_disk`를 확인한 뒤 동일한 저장 검증을 수행한다. 별도 `codex app-server`를 실행하는 것은 현재 호스트의 재시작이 아니다.

### 검증 자료 제외와 실제 업무 자료

검증 작업 `work-0efa3a462b044fb4a0c51d33476fde53`의 scope에는 `data_usage:verification_only`를 저장했다. 새 모델 준비 시도의 packet 1개, 성공 packet 2개, 구 연결의 보류 packet 1개, 총 **4개 packet/4개 evaluation 묶음**을 실제 자료 감사에서 제외했다. 질문 수와 evaluation 묶음 수는 다르다. 구 연결의 도구 적합성은 저장되지 않았으며 로컬 수신 기록에만 남겼다.

기존 추출기에 검증 작업·packet 제외를 추가했고, 복사된 검증 state에도 표시가 있으면 준비 검사가 거절한다. `--previous-review`는 같은 ID의 입력·원기록·출처가 일치할 때만 검토·개발 노출 상태를 이어받는다. 감사에서 내보낸 후보는 기본적으로 개발 자료로 표시해 최종 시험에 자동 유입되지 않는다. raw 결과의 경로/hash를 공유하는 사례도 같은 분할 그룹으로 연결한다.

앱을 열어 두는 것만으로 정답 자료가 쌓이지 않는다. `desktop/renderer/renderer.js`의 주기 조회는 상태/작업 목록을 읽고 `desktop_bridge.py`는 `workspace_status`와 모델 상태를 조회한다. 온보딩의 연결 검사도 `workspace_status`다. 이 경로에는 `context_prepare`나 `capability_recommend` 모델 판단이 없다. 실제 Codex 작업에서 해당 도구를 호출해야 판단 시도가 저장되고, `work_record(evidence_reported)`를 호출해야 실행 보고가 생긴다. 셸 실행 결과나 대화 전체가 자동 수집되는 기능은 없다. 상태 조회 메타데이터·모델 준비 로그·실제 판단·사람 정답은 각각 다르다.

| 자료 상태 | 9월 29일 결과 |
|---|---|
| 실제 업무에서 보존된 원래 모델 요청 | 0개. 과거 19개는 원입력 누락, 새 검증 요청은 제외 |
| 저장된 보고에서 재구성한 후보 | 82개. 기존 81개와 전날 수행한 실제 감사의 완료 보고 1개. 새 업무 요청 82개가 아님 |
| 목적 / 연결 그룹 | 근거 관계 82, 관련성 0, 도구 적합성 0 / 15그룹 |
| 에이전트 초안 | 지지 50, 일부 지지 31, 보류 1. 사람 검토 0 |
| 원시 근거 대조 | 13사례·19파일. 이전 6사례·10파일에서 확대. 69사례는 원시 결과 독립 대조 미실행 |
| 최종 시험 적격 | 0개. 기존 44개 설명 사례와 개발 노출 자료를 제외 |
| 준비 검사 | `not_ready`, 262개 조건 위반, frozen 파일 없음 |

추가 대조에서는 당시 실행 결과와 저장된 target hash가 같은 모델 수명·캐시 재기동·revision 충돌·과거 native 검증 파일을 확인했다. 문맥 복원 원본 hash, 구/신 MCP의 예산 응답 차이, 전날 실제 검사 로그도 대조했다. 과거 native 성공은 **오늘 연결의 성공을 증명하지 않는다.** 원시 파일에서 더 많은 사실을 확인해도 원래 후보에 없는 내용을 몰래 넣어 판정 초안을 바꾸지 않았다.

보류 사례는 여전히 과거 UI 17개 검사다. 현재 같은 경로의 파일은 나중의 58개 검사이므로 추천은 **보류 유지**다. 필요한 것은 당시 17개 실행의 원본 결과나 그 hash가 맞는 사본이며, 기억으로 정답을 고르는 질문이 아니다. 지금 사용자에게 모호한 의미 판정을 추가로 묻지 않는다. 다른 초안도 사람 검토 완료로 표시하지 않는다.

분할 진단은 train 71개/12그룹, development 8개/2그룹, test 3개/1그룹, calibration 0이며 모두 미검토 근거 관계다. 이는 준비 상태 진단일 뿐 확정 분할이 아니다. 기존 하한 600개, 시험 목적별 30개, 세 목적의 네 분할, 사람 정답 조건을 충족하지 않는다. 반박·무관 라벨 초안도 없고 독립 시험 적격이 0이므로 표본 수를 채워도 현재 자료만으로 전체 성능을 주장할 수 없다. 기준 성능(SemIf / 미학습 Laya), 본 학습, 후보 비교, 정확도·중요 오류·추론 시간/메모리·차이의 불확실성 측정은 실행하지 않았다. 기존 모델·제품 설정·자동 승격 상태를 유지했다.

### 재현과 로컬 검사

원시 증거는 `.local/laya-finetuning/verification-20260929/`의 `stdio-storage.json`, `native-storage.json`, `final-processes.json`에 있다. SDK 성공 packet은 `packet-59ccb7935a0941f882dcc27e2d41474b`, `packet-134671fa373944129e24cdc046cf731c`다. 재시작 전후 전체 packet hash는 각각 `ffc0e1c8…`, `4149dba2…`로 동일하다. 현재 native 실패 packet은 `packet-be3fe547df9d4d34aeb52fa901d1a5a3`다. 이 기록은 검증용이며 독립 품질 평가에 포함하지 않는다.

후보·초안·자동 확인 사실·원시 파일 hash는 `.local/laya-finetuning/actual-20260929/`의 `review-candidates.jsonl`, `grounding-review.json`, `review.html`, `readiness/prepare.json`에 보존했다. 원격에는 개인 원문을 올리지 않는다.

```powershell
# 추가 모델이 실행 중이지 않을 때. 새 출력 파일 사용, 검증 전용 내부 작업을 생성함.
.venv/Scripts/python.exe -X utf8 -m scripts.validate_model_lifecycle --config .local/project.toml --verify-request-storage --output .local/laya-finetuning/verification-next/stdio-storage.json
# 이전 검토/노출 상태를 보존하며 현재 DB를 읽기 전용 감사
.venv/Scripts/python.exe -X utf8 -m scripts.prepare_laya_training_data audit --db .local/state/projects/project-89095613680449da8ab8c438dfcf826a/state.sqlite --previous-review .local/laya-finetuning/actual-20260929/review-candidates.jsonl --output .local/laya-finetuning/actual-next
.venv/Scripts/python.exe -X utf8 -m scripts.prepare_laya_training_data prepare --reviewed .local/laya-finetuning/actual-next/review-candidates.jsonl --output .local/laya-finetuning/actual-next/readiness
```

전체 로컬 회귀는 **329 passed / 2 skipped**. 이후 raw 출처 그룹 연결 및 분포 보고 보완을 포함한 최종 관련 검사는 **20 passed**, 전체 Ruff lint와 95파일 format 검사를 통과했다. 첫 제한 환경 pytest는 Temp 권한/임시 부모 폴더 문제로 fixture 4개가 실행되지 않아, 프로젝트 내부 새 임시 경로로 수정한 뒤 실행했다. 검사 실패를 제품 성공으로 세지 않았다. 원격 CI·유료 자원·제품 기본 모델 교체는 사용하지 않았다.

## 재현 도구와 결과

- [자료 조사·검토·분리](../scripts/prepare_laya_training_data.py), [검토 화면 템플릿](../scripts/laya_training_review.html)
- [실제 예비 학습·재로드](../scripts/train_laya_pilot.py), [동일 구조 패키징](../scripts/package_laya_pilot.py), [런타임 동등성 검사](../scripts/verify_laya_pilot.py)
- [원시 결과·실패 보존·파일 hash](../evaluations/laya-finetuning/run-20260928/index.json), [참조 환경 버전](../evaluations/laya-finetuning/run-20260928/environment.json)

학습 환경은 기존 격리 Laya 환경을 사용했다. ONNX 패키징은 별도 환경에 ONNX 1.23.0을 설치했다. 제품 Python 의존성이나 기존 모델 환경을 수정하지 않았다. 가중치·검토 원문·실행 바이너리는 `.local/`에만 있다.

```powershell
# 항상 새 출력 경로를 사용한다. 기존 완료/실패 출력을 덮어쓰지 않는다.
.local/laya-venv/Scripts/python.exe -X utf8 scripts/train_laya_pilot.py train --output .local/laya-finetuning/repro
.local/laya-venv/Scripts/python.exe -X utf8 scripts/train_laya_pilot.py reload --output .local/laya-finetuning/repro
.local/laya-package-venv/Scripts/python.exe -X utf8 scripts/package_laya_pilot.py --pilot .local/laya-finetuning/repro --output .local/laya-finetuning/repro-store
# Ollaya 0.7.3을 해당 저장소·CPU·포트 11438에서 직접 시작한 뒤 소유 PID를 전달한다.
.venv/Scripts/python.exe -X utf8 -m scripts.verify_laya_pilot --pilot .local/laya-finetuning/repro --server-pid $taskDaemonId
```

자료 준비는 `prepare_laya_training_data.py inventory --db <현재 프로젝트 DB> --output <로컬 경로>`로 검토 후보를 만들고, 검토 후 `prepare --reviewed <JSONL> --output <새 경로>`로 검사한다. 준비되지 않은 자료는 종료 코드 1과 이유를 남기며 frozen 자료를 생성하지 않는다.

## 검사 기록

첫 전체 검사는 Laya 모의 worker에 실제 encoder 설정 파일이 없어서 1개 실패했다(319 passed / 2 skipped). fixture에 설정 파일과 해당 설정을 읽은 모델 객체를 넣어 실제 준비 계약을 반영했고, 관련 11개 검사가 통과했다. 최종 Ruff lint·format과 전체 pytest는 **320 passed / 2 skipped**였다.

추가 검사에는 출처 그룹의 전이적 연결, 동일 원문 중복, 모델 예측을 정답으로 사용하지 않는 계약, 공개 진단의 재사용 차단, 읽기 전용 기록 추출, HTML 스크립트 주입 방지, rotary 설정 변환과 runtime 거절이 포함된다. 생성한 검토 화면의 JavaScript 문법과 원시 결과 hash·checkpoint 연결·상대 문서 링크도 확인했다. 브라우저에서 사람의 실제 검토 작업까지 수행한 것은 아니다.
