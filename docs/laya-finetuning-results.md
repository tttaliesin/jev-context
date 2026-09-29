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

결론은 **개선 미확인**이다. 아래는 Codex 재실행 전의 조사다. 당시 새 stdio 저장 경로는 통과했지만 구 Codex 서버에는 재시작이 필요했다. 이후 사용자가 재실행했고, [현재 연결의 직접 검증](#codex-재실행-후-최종-확인)도 통과했다. 두 시점의 증거를 구분한다.

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

이 조사 시점에는 현재 Codex 연결의 재시작 후 세 목적 재검증이 **미완료**였다. 현재 호스트의 MCP 연결을 다시 여는 도구가 제공되지 않아 임의로 프로세스를 죽이지 않았다. [공식 MCP 안내](https://developers.openai.com/codex/mcp)의 MCP 서버 재시작 기능을 사용하거나, 해당 기능이 없으면 **Jev 관리 앱이 아니라 Codex 앱을 완전히 종료 후 다시 열도록** 안내했다. 별도 `codex app-server` 실행을 현재 호스트의 재시작으로 간주하지 않았다. 아래에서 실제 재실행 후 결과를 확인했다.

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

### Codex 재실행 후 최종 확인

2026-09-29 사용자 “codex 재실행 했다” 이후, 현재 대화에 노출된 Jev MCP 도구로 직접 확인했다. **현재 연결의 코드 갱신·세 목적 요청 저장·기존 기록 복원이 모두 통과했다.** 추가 재시작은 필요하지 않다.

- 현재 runtime은 `runtime-d0f8fd80dbb84dbaa99cd81e4f22e52f`, 시작 `2026-09-29T00:31:14.972985Z`(한국 09:31:14)이며 build와 disk가 모두 `3d568a5a…`, `code_state=matches_disk`다. 이전 runtime과 프로필 불일치가 해소됐다.
- 현재 Codex의 `context_prepare(required)`에서 관련성·근거 관계가 `observed`, `capability_recommend`에서 도구 적합성이 `observed`였다. 후자의 응답 `partial`은 일부 도구 목록과 shadow 정책에 따른 상태이며 저장 실패가 아니다. 원입력·질문·프로필·시점·결과·state hash를 DB와 `work_inspect`에 대조했다.
- 성공 packet은 `packet-42d937e7545247908b3d9275ed35ddd6`, `packet-66fe5c0983384b3cb7a19864ea66b640`다. 전체 packet hash는 각각 `8b49af93…`, `0164e028…`다. 새 읽기용 stdio 프로세스에서도 같은 기록을 읽었다. **요청 생성은 현재 Codex 연결에서 했으며 별도 SDK 생성으로 대신하지 않았다.** 재실행 전에 저장한 두 성공 packet도 현재 Codex에서 조회한 결과와 기존 hash가 일치했다.
- 준비 시도 1개와 성공 2개를 포함해 검증용 packet 총 7개를 학습 후보에서 제외했다. 실제 보고 후보는 여전히 82개이며 이번 확인으로 실제 업무 표본이나 사람 정답이 늘지는 않았다. 원시 수신·DB 대조·별도 프로세스 복원 결과는 `.local/laya-finetuning/verification-20260929/postrestart-native.json`에 있다.
- 마지막 현재 Codex 상태 조회에서도 `matches_disk`를 유지했고 유휴 해제 뒤 worker·broker가 모두 null이었다. 모델을 동시에 추가 실행하거나 제품 설정을 변경하지 않았다.

이 검증에서 모델은 관련성에 `irrelevant`, 근거 관계에 `insufficient_evidence`, 도구 적합성에 `fit`을 선택했다. 질문과 직접 관련된 수명 검증 원문에 `irrelevant`를 선택한 점은 판단 품질의 한계를 보여주는 관찰이다. 검증 성공은 요청 전달·저장 성공을 뜻하며 세 판단이 모두 정답이라는 뜻은 아니다. 이를 독립 성능평가 표본으로 사용하지 않는다. 모든 목적의 promotion은 false로 유지하며 본 학습·정확도 비교는 기존 데이터 조건 미충족으로 미실행, **개선 미확인**이다. 이번에는 제품 코드를 수정하지 않아 전체 회귀를 반복하지 않고 실제 연결·DB·복원 대조와 문서 검사를 수행했다.

### 기준 답안 검토 주체 변경과 첫 확정 묶음

2026-09-29 사용자가 Jev를 평가하는 기준 답안은 Codex가 검증하라고 지정했다. **기존 계획의 모든 라벨에 사람 검토를 요구하던 조건을 변경했다.** 아래 과거 조사에서 사람 정답 0개를 학습 차단 조건으로 설명한 부분은 당시 기준이며, 현재는 원문·실행 근거를 검토한 `agent_verified` 답안도 준비 검사에서 인정한다. 사람이 작성한 정답이나 사람 독립 평가로 표현하지 않는다.

준비 검사는 검토자·시점·이유 외에 검토한 state/원기록 hash, 확정 라벨·중요 여부, 원시 근거 파일 경로/hash, Jev 출력에서 정답을 복사하지 않았다는 검토 기록을 확인한다. 기존 에이전트 초안 상태만 바꾸거나, 검토 뒤 입력/라벨을 바꾸거나, 근거 없이 승인한 자료는 거절한다. 이 검사는 기록의 일관성을 확인하며 의미 판단의 정확성을 자동 증명하지는 않는다.

82개 중 고정 target hash의 원시 결과를 다시 대조한 첫 4개를 Codex 기준 답안으로 확정했다. 다른 초안은 일괄 승인하지 않았다.

| 실제 사례 | 확정 판정과 근거 |
|---|---|
| 모델 공유·유휴 해제 (`evidence-a5c699…`) | **supports**. 후보가 공유·연결 종료 후 판단 유지·유휴 해제를 모두 보고하며 원시 결과의 observed 3회와 최종 worker 부재가 일치 |
| 캐시 기동·두 회차 재기동 (`evidence-5400e3…`) | **supports**. 두 연결 검증과 서로 다른 worker 재기동·해제, target hash 일치 확인 |
| revision 충돌 및 수정 주장 (`evidence-707f97…`) | **partial**. 후보와 원시 실패에서 충돌 발생은 확인하지만 병행 저장이라는 원인과 특정 코드 수정까지 입증하지 않음. 전체 완료로 오판하는 오류를 중요하게 지정 |
| 과거 native 복원·판단·종료 (`evidence-c05e0f…`) | **partial**. 후보에 판단·기동·종료 성공은 있으나 재시작·작업 복원 성공은 명시되지 않음. 명령에 work_open이 있다는 것만으로 성공을 보충하지 않음 |

자료는 `.local/laya-finetuning/agent-review-20260929/`에 이전 82개 파일과 별도로 보존했다. `review-candidates.jsonl`에는 확정 4개·대기 78개, `review-results.json`에는 기준 답안·이유·근거 hash가 있다. 검토 화면은 사람 검토와 에이전트 검증을 별도로 표시한다. 모든 원입력과 기존 개발 노출 표시를 유지하며, 4개 모두 최종 시험에서는 제외한다. 실제 작업 보고의 재구성 사례라는 출처도 바꾸지 않았다.

준비 검사에서 4개의 검토 조건은 인정됐다. 전체는 **not_ready**이며 250개 조건 위반, frozen 파일 없음이다. 부족한 것은 이제 사람 검토 여부 자체가 아니라 나머지 검토, 600개 하한, 관련성·도구 적합성 자료, 독립 분할·시험 자료다. 제품 승격 계약에 사람 평가인 것처럼 값을 넣거나 기본 모델을 교체하지 않았다. 학습·성능 비교는 미실행, 개선 미확인이다.

기준 답안과 제품 구현을 같은 Codex가 검토했다는 한계는 유지한다. 새 최종 시험은 개발 중 본 사례와 출처를 분리하고, 고정된 기준으로 대상 모델의 예측을 보기 전에 답안을 확정해야 한다. 사용자에게 모든 사례의 라벨 작성을 요구하지 않으며, 사용자 의도나 접근할 수 없는 사실이 있어야 판단 가능한 항목만 구체적으로 질문한다.

관련 로컬 검사 28개, 전체 Ruff lint/95파일 format, 생성된 검토 화면 JavaScript 문법, 문서 링크 159개를 확인했다. 검토 뒤 입력·라벨·원기록 변경, Jev 예측 복사, 근거 누락, 초안·중요 여부 미완료의 거절과 정상 Codex 검토 허용을 검사했다. 기존 후보 파일 hash는 보존했고 사람 정답 0개·시험 적격 0개·frozen 없음도 확인했다. 이번 변경에서 모델 실행·원격 CI·유료 자원을 사용하지 않았다.

## 2026-09-29 승인 계획 실행: 검토 상태와 시험 분리

기존 82개의 남은 78개를 검토해 **확정 38개(기존 4개 포함), 보류 44개**로 정리했다. 원시 대조 기록이 있던 8개는 재검토했고, 나머지는 프로젝트 안의 실행 결과 디렉터리 31개를 검색했다. 없는 원본을 만들거나 초안을 일괄 승인하지 않았다. 보류는 원본 미확보, 다른 실행으로 덮인 파일, 일부 원시 결과만으로 종합 주장을 확정하지 못한 경우다. 당시 원본이 없는 것과 입력의 답이 `insufficient_evidence`인 것은 구분했다. 일부 보류는 원시 파일이 남아 있어도 전체 대응 검토가 끝나지 않은 항목이며, 자료 자체가 존재하지 않는다고 주장하지 않는다.

첫 산출물은 로컬 `.local/laya-finetuning/execution-20260929/review-results.json`과 `review-candidates.jsonl`/`review.html`이다. 사례별 입력·원기록 hash, 판정·중요 여부·이유·시각·원시 근거 또는 보류 이유가 있다. 첫 결과 JSON SHA256은 `2c3d61a0b656b12b4e214b4318ad3ccd4d1ba1f61f254446a58955987eedf379`이다. 사람 검토는 0개이며 기존 82개는 모두 최종 시험 제외다. 확정 라벨은 supports 26/partial 12다. UI 17개 과거 결과는 같은 경로에 남은 58개 결과로 검증하지 않았다.

이번 실제 작업에서 계획의 제약 검색, 기존 자료의 시험 제외 주장 확인, 원시 로그 검사 도구 선택을 MCP로 요청했다. 입력을 나중에 만들어 붙이지 않고 DB의 실제 요청과 고정 원문 revision을 대조했다. 새 **26개 요청 사례**는 relevance 16/evidence_relation 8/capability_fit 2다. 모두 같은 프로젝트의 계획·도구 선택에서 나온 개발 자료이며 새로운 독립 출처로 세지 않는다. Jev 결과는 전부 `abstained / engine_preparing`였고, 문맥 호출 두 번은 필수 문맥 71,373 bytes가 최대 65,536 bytes보다 커서 `insufficient`였다. 요청 저장 성공을 추론 성공이나 품질 측정으로 표현하지 않는다. 필수 제약을 빼서 성공처럼 만들지도 않았다. 마지막 현재 MCP는 코드 일치, idle, worker/broker 없음이며 재시작 요청은 필요 없다.

| 목적 | 확보 | Codex 답안 확정 | 최종 시험 | 부족한 라벨/출처 |
|---|---:|---:|---:|---|
| 관련성 | 16 | 16 | 0 | relevant만 있음. irrelevant/insufficient_evidence 및 독립 작업 필요 |
| 근거 관계 | 90 | 46 | 0 | supports 27/partial 17/insufficient_evidence 2. contradicts/unrelated 없음 |
| 도구 적합성 | 2 | 2 | 0 | fit/unfit 각1. insufficient_evidence 및 독립 도구/작업 필요 |
| 합계 | 108 | 64 | 0 | 기존 하한600 대비 확정536개 부족. 세 목적 시험 각30개 모두 부족 |

연결 그룹은 15개이며 관련성·도구 적합성 새 사례는 각각 하나의 기존 그룹에 속한다. `latest/reviewed.jsonl` SHA256은 `5e54e284d6d648207f750b7d15c40c78b4cc9d7648f0ddfa9e050465eb646172`다. 검증 전용 packet 7개·실행 보고 2개는 계속 제외했고 원요청이 없는 과거 판단19개를 복원하지 않았다. 앱을 켜두는 상태 조회는 판단 요청 자료를 만들지 않는다. `context_prepare`의 실제 판단 요청과 `capability_recommend` 호출에서만 이 유형의 원요청이 저장되며, 엔진 준비 중 보류 결과도 그대로 남는다.

### 적용한 준비 기능과 자체 검토

- `reserve`는 **아직 packet/실행 보고가 없는 실제 작업**과 사용하지 않은 출처의 ID/revision/hash 또는 도구 ID/version/정확한 후보 hash를 예약한다. 시각·기존 감사 hash·예약 hash를 기록하며 기존 파일은 덮어쓰지 않는다. 기존 개발 자료를 시험으로 바꾸지 않는다.
- `audit --reservations --reservation-history --previous-review`는 예약 후의 정확한 원요청만 시험 후보로 표시한다. 같은 작업·출처·원문·파생 사례·원시 파일·도구 ID를 전이적으로 묶고 개발 자료와 연결된 예약은 무효화한다. 모르는 출처 관계는 시험 자격으로 추정하지 않는다.
- `blind`와 예약 자료의 HTML은 허용 필드만 내보낸다. 입력·출처·근거 경로/hash는 보이지만 원기록의 Jev 답안/점수·기존 초안/라벨은 출력하지 않는다. 원래 요청/결과는 DB 및 로컬 원기록에 남는다. `--review-decisions` 병합은 동일 입력·원기록 hash·출처에만 적용한다.
- `prepare`는 네 분할과 기존600/시험 목적별30을 유지한다. 시험 라벨별 최소2개 출처 그룹을 독립 반복의 최소 검사로 추가했다. 이는 충분한 표본이나 통계 검정력을 보장하는 숫자가 아니다. 준비 통과 시 명시적 질문 템플릿과 자료·분할·파일 hash를 함께 고정하고, 읽기 함수는 변경된 hash나 분할 혼입을 거절한다.

예약 시각과 검토 주체는 로컬 기록이며 암호학적 독립 봉인이나 사람 독립 평가가 아니다. 동일 Codex의 구현·답안 검토 편향은 남는다. 실제 새 시험 작업/출처가 없으므로 시험 예약을 만들어 수량을 채우지 않았다. 예약·차단 경로는 격리 fixture로 검사했으며 그것도 업무 자료에 넣지 않았다.

현재 준비 검사는 **not_ready, 154개 위반, frozen 파일 없음**이다. 44개 보류·라벨 분포·독립 분할·시험 부족이 남았다. 승인 계획 3단계의 실제 자료 학습 CLI 연결, 제품 SemIf/미학습 Laya 기준 측정, 후보 학습, 보정·최종 비교·bootstrap 분석은 준비 조건 때문에 **미실행**이다. 현재 학습/평가 CLI는 여전히 공개 기술 예비 실행 경로이며 실제 자료 학습 준비 완료로 표시하지 않는다. 이 경계를 숨기려고 도구만 더 늘리거나 공개 자료를 새 최종 시험으로 쓰지 않았다. 결론은 **개선 미확인**, 기본 모델과 설정은 유지했다.

사용자가 지금 라벨을 붙이거나 Codex를 재시작할 필요는 없다. 후속 최소 입력은 개발에 노출되지 않은 실제 작업과 그 원문·후보·조건이다. 그런 작업이 생기면 내용 검토/모델 호출 전에 예약하며, 같은 출처의 사례를 늘리는 것만으로 독립 평가 부족이 해소되지는 않는다. 44개 보류의 개별 원본 요구는 첫 결과 JSON에 보존돼 있다.

### 재현 명령

마지막 자체 검토에서 다른 source ID로 복제된 동일 원문도 예약하면 안 된다는 경로를 보완했다. DB의 과거 source revision hash를 대조해 거절하며, 보완 후 관련 **38개 검사 통과**, Ruff lint/format 통과를 확인했다. 위 전체 347개 결과는 이 마지막 작은 보완 직전 결과이고 이후에는 관련 검사를 다시 수행했다. Markdown 로컬 링크358개도 확인했다.

검증: 전체 Ruff lint/95파일 format 및 **347 passed, 2 skipped (103.39초)**. 정상 에이전트 검토와 기존 검토 보존, 예약 전 기록·개발 출처·검증 작업 거절, 파생/동일 도구 그룹 충돌, 예약 시점/hash/후보 변경 거절, 예측 비노출, 검토 입력 변경 거절, 고정 자료 hash·분할 선택·덮어쓰기 차단을 검사했다. 격리 테스트는 실제 학습 자료에 넣지 않았다. 원격 CI·유료 자원·후보 모델 학습은 실행하지 않았다.

```powershell
# 아래 실제 감사는 DB를 읽기 전용으로 열고, 검토와 개발 노출 표시를 이어받는다.
.venv/Scripts/python.exe -X utf8 scripts/prepare_laya_training_data.py audit --db .local/state/projects/project-89095613680449da8ab8c438dfcf826a/state.sqlite --previous-review .local/laya-finetuning/execution-20260929/latest/reviewed.jsonl --output .local/laya-finetuning/next-audit
.venv/Scripts/python.exe -X utf8 scripts/prepare_laya_training_data.py prepare --reviewed .local/laya-finetuning/execution-20260929/latest/reviewed.jsonl --output .local/laya-finetuning/next-readiness
# 두 번째 명령은 현재 자료에서 exit1/not_ready가 정상적인 차단 결과다.
```

향후 예약 사양은 JSON 배열 `[ {"id":"…", "work_id":"실제 새 작업 ID", "source_ids":["새 출처 ID"]} ]`이다. 도구 적합성은 `capability_sources`에 `id/version/sha256`(실제 inventory 후보 객체의 canonical JSON SHA256)을 명시한다. `reserve --db <DB> --reservation-spec <사양.json> --reservation-history <예약 당시 감사.jsonl> --output <새 경로>`로 생성한 예약 파일과 같은 감사 파일을 후속 `audit`에 전달한다. `blind --reviewed <전체 원기록.jsonl> --output <검토 경로>`의 `blind-review.jsonl`을 검토하고 `prepare --reviewed <전체 원기록.jsonl> --review-decisions <검토 결과.jsonl> --reservations <예약.json> --questions <목적별 질문.json> --output <새 경로>`로 병합·검사한다. 기존 원문/모델/설정은 보존하며 개인 원문은 Git에 올리지 않는다.

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
