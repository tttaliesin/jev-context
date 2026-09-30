# 실제 가중치 학습·Ollaya 반입 예비 실행

**최신 결과(2026-09-29):** 후속 진단에서 같은 학습 자료 15개를 300 step 반복했지만 정답은 4/15로 유지됐다. 평균 손실은 2.055→1.671로 감소했으나 진단 기준 미달이므로 A/B/C 후보 학습과 새 최종 시험은 실행하지 않았다. 앞선 시험은 원본/후보33/90, 제품SemIf55/90이었다. 결론은 **개선 미확인**, 제품 기본 모델 유지다. 진단 자료의 점수를 시험 정확도로 표현하지 않는다.

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


## 2026-09-29 승인된 합성 실험: 자료 준비와 원문 재검토

실제 자료 부족과 별개로 합성 자료를 학습·시험 양쪽에 쓰도록 사용자가 승인했다. 실제 600개 기준은 그대로이며 합성 자료를 그 수에 합산하지 않는다.

- 합성 자료: `evaluations/laya-finetuning/synthetic-20260929.json`에270개.30개 구체적 업무 상황을 먼저train10/development5/calibration5/test10으로 배정했다. 각 상황의 관련성3/근거관계3/도구적합성3개는 한 분할에만 존재한다. 분할별90/45/45/90개, 목적별 동일 수량과 라벨 균등, 시험 목적별10개 상황 묶음·30개 및 라벨별2개 이상 묶음 검사를 통과했다.
- 검토: 각 항목에 가상 입력·정답·근거 문장·판정 이유·Codex 검토·hash를 기록했다. Jev 예측을 정답에 쓰지 않았다. 같은 작성자의 규칙·문체 편향이 있으며, 사람 독립 평가나 실제 업무 향상을 뜻하지 않는다.
- 수정 후 고정 데이터 SHA256: `0ae4281dfe88ca1905c52eb25fe1d9f50ae9af0deee74be8304dab7d2fd7bde9`. 질문hash `fdc5984c609ffd1cd13078e7499bae8d42e5e8e75eaa4c62abcb9593c48b204a`. 분할hash `c9796fb9cdad1395fe821b9a62f84ae6d1615bb7a74a3acb51d1b6926ef33843`.
- 실제 보류44개 재검토: 아이콘 적용과 초기 packaged GUI 검증2개를 원시 파일에 대조해추가 확정. 실제108개 중66개 agent_verified/42개deferred/human0/test0. 나머지는 프로젝트 원본목록에서 필수 원문을 찾지 못한33개와 일부 파일은 있으나 종합 주장 대조가 불완전한9개다. 이를 정답 insufficient_evidence로 바꾸지 않았다. 원본108개와 이전검토 파일을 보존했다.
- 원시 대조의 범위: v3비교의756관측과 관리자 예외 오판3회, startup30개 선택일치/확률차0, 온보딩·언어왕복 보고 등을 확인했으나 당시 미적용 설정·전체회귀·원격반영까지 확인되지 않은 주장은 보류했다. 새 실행으로 과거 사실을 대체하지 않았다. 원격CI는 조회·실행하지 않았다.

개인 기록과 상세 보류 이유는 `.local/laya-finetuning/synthetic-20260929/actual-recheck.json`, 새 검토는 `actual-reviewed.jsonl`에 저장한다. 이후 `--previous-review`에는 이 새 파일을 사용한다. 준비 결과와 모델 실행은 같은 디렉터리에 별도 보존한다. 아래 모델 비교 결과가 추가되기 전까지 이 절은 자료 준비 완료만 뜻한다.


### 최종 점수 공개 전 발견한 입력 결함과 정정

첫 합성 파일의 도구 후보 ID에 `fit`/`unfit`/`insufficient_evidence`가 포함돼 있었다. 정답이 입력에 노출되는 결함이므로 최초 실행(가중치 `da71a292…`)은 성능 근거에서 제외했다. 시험 예측·점수는 열람하지 않았으며, 이 수정은 모델 오답을 보고 한 조정이 아니다. 각 상황의 세 도구 후보를 동일한 중립 ID `<family>-tool`로 바꾸고 상태/검토 hash를 갱신했다. 기존 상황 분할·질문·정답·근거는 보존했으며 새 입력hash로 원본 모델부터 전 과정을 재실행한다. 정답 단어가 후보 ID에 들어가면 준비 검사가 거절하는 회귀 검사를 추가했다. 최초 자료·모델·실행 보고는 삭제하지 않고 `invalidated-run.json`과 함께 로컬에 보존한다.

이 정정 뒤의 정식 실험 산출물은 `.local/laya-finetuning/synthetic-20260929/corrected/`에만 둔다. 이전 디렉터리의 시험 수치나 시간은 최종 결과와 섞지 않는다. 전체검사354통과/2제외 후 소비된 시험 재학습 차단·정답ID 유출 방지 등 관련45검사를 통과했다. 최종 코드 검사는 아래 결과에 기록한다.

### 재현 순서

저장소 루트의 PowerShell에서 실행한다. 모델·가상환경은 기존 로컬 설치를 사용하며 자동 다운로드나 유료 실행을 하지 않는다. 출력 디렉터리는 새 경로를 지정한다. 원자료 준비의 `--previous-review`는 로컬 실제자료 충돌 검사를 추가하며 공유 저장소에는 해당 개인자료를 넣지 않는다.

```powershell
$experiment='.local/laya-finetuning/synthetic-20260929/corrected'
.venv/Scripts/python.exe -X utf8 -m scripts.prepare_laya_training_data prepare --data-profile synthetic-experiment --reviewed evaluations/laya-finetuning/synthetic-20260929.json --previous-review .local/laya-finetuning/synthetic-20260929/actual-reviewed.jsonl --output "$experiment/frozen"
```

기존 Ollaya0.7.3 daemon을 루프백 전용 포트로 실행한다. `OLLAYA_DEVICE=cpu`, `OLLAYA_MAX_LOADED_MODELS=1`, `OMP_NUM_THREADS=4`와 원본 모델 저장소를 사용한다. OMP 설정은 기록한 환경값이며 ONNX 내부 스레드 수를 별도 실측했다고 주장하지 않는다. 학습의 torch4threads는 코드에서 고정한다. 원본 Laya와 후보의 실행기·환경·질문·입력은 동일하며 제품 SemIf는 기존 GPU 프로필을 그대로 사용한다.

```powershell
# SemIf test와 원본 Laya development/calibration/test를 순차 실행한다.
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya frozen --data-profile synthetic-experiment --dataset "$experiment/frozen/frozen.jsonl" --manifest "$experiment/frozen/prepare.json" --split test --backend semif --endpoint http://127.0.0.1:11439 --output "$experiment/semif"
# 원본 Laya는 backend ollaya, model laya:multilingual, 기존 model-store,
# 소유 daemon의 --server-pid를 전달하고 각 split을 별도로 실행한다.
.local/laya-venv/Scripts/python.exe -X utf8 -m scripts.train_laya_pilot train --data-profile synthetic-experiment --dataset "$experiment/frozen/frozen.jsonl" --manifest "$experiment/frozen/prepare.json" --split train --output "$experiment/pilot"
# 같은 인수로 train 대신 reload를 실행한다.
.local/laya-package-venv/Scripts/python.exe -X utf8 -m scripts.package_laya_pilot --pilot "$experiment/pilot" --output "$experiment/ollaya-store"
# 새 저장소를 사용하는 소유 daemon을 다른 루프백 포트에서 준비한다.
# scripts.verify_laya_pilot에 --pilot/--endpoint/--server-pid를 전달한다.
# 후보의 development/calibration을 frozen 명령으로 실행한다.
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya freeze-candidate --data-profile synthetic-experiment --dataset "$experiment/frozen/frozen.jsonl" --manifest "$experiment/frozen/prepare.json" --model-store "$experiment/ollaya-store" --checkpoint "$experiment/pilot/checkpoint/model.safetensors" --calibration-report "$experiment/candidate/calibration.json" --candidate-lock "$experiment/candidate-lock.json"
# 후보 test에는 --model jev-laya:pilot, --model-store, --candidate-lock을 전달한다.
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya compare --data-profile synthetic-experiment --dataset "$experiment/frozen/frozen.jsonl" --manifest "$experiment/frozen/prepare.json" --candidate-lock "$experiment/candidate-lock.json" --baseline-report "$experiment/baseline/test.json" --candidate-report "$experiment/candidate/test.json" --product-report "$experiment/semif/test.json" --output "$experiment"
```

`frozen` 시험 실행은 점수·선택을 표준출력에 내보내지 않는다. `compare`가 결과를 열기 전에 고정자료 옆 `test-consumed.json`을 만들며 그 자료로 새 후보를 학습하면 거절한다. 이 표시는 협업 절차 보호이며 파일 복사·변조까지 막는 보안 봉인은 아니다. 시험 결과로 후보를 수정하려면 새 상황 묶음과 새로운 고정 시험이 필요하다. 공개 예비 진단은 명시적으로 `--data-profile public-pilot`을 사용한다.


### 수정된 자료의 학습·최종 품질 결과

최종 후보는 원본에서 다시 시작한100step 학습이다. 83.18초, 최대step0.952초, 학습 가능한14,770,945개 파라미터 중31개 텐서가 변경됐으며 encoder는 그대로다. 후보 가중치SHA256은 `cc396e3c7e05ff522e3863bf9e4481daa0c80b1b28b3b5ce2bef7d2457bdd7e5`다. 재로드12/12 및 Ollaya12/12 선택 일치, 최대 확률 오차는 각각 약0.0001로 반입 기준0.005 이하다.

| 목적 | 제품 SemIf | 미학습 Laya | 학습 후보 | Laya 대비 정확도 차이95% 구간 |
|---|---:|---:|---:|---:|
| 관련성 | 21/30 | 11/30 | 11/30 | 0~0%p |
| 근거 관계 | 13/30 | 9/30 | 9/30 | 0~0%p |
| 도구 적합성 | 21/30 | 13/30 | 13/30 | 0~0%p |

합계는 제품55/90(61.1%), 미학습/후보 각각33/90(36.7%)다. 3회 반복에서 세 모델 모두 판정 변동이 없었고, 실행 실패와 미실행은0이다. 반복270개를 독립270문제로 계산하지 않았으며 정확도 분모는90개다. Laya 학습 전후에는 **개선한 문항0개, 퇴보한 문항0개**, 중요 오류44개로 동일했다. 목적별 중요 오류는 관련성11/근거관계21/도구적합성12개다. 이 실험의 critical은 사전 표시된 비긍정 라벨 문항의 오판을 보수적으로 센 것이며 제품 사고율이 아니다.

10개 상황 묶음을 재표집한10,000회 paired bootstrap 구간은 세 목적 모두0~0%p다. 동일한 판정을 반복한 고정 문제집의 구간이며 실제 업무 효과의 구간으로 확대하지 않는다. 보정 온도는 원본/후보 모두calibration에서3으로 선택됐고, 시험NLL은1.26718→1.26168, Brier는0.70324→0.70032였다. 확률은 조금 달라졌지만 정답 선택은 바뀌지 않았다. 이 작은 확률 지표 차이를 정확도 개선으로 주장하지 않는다.

주요 오류도 그대로다. 관련성에서 내용이 읽히지 않는10개를 모두 무관으로 판정했고, 도구 기능이 명시되지 않은10개를 모두 부적합으로 판정했다. 근거 관계에서는 근거 부족6개와 무관6개를 모두 supports로 판정했고, partial6개 중5개를 supports로 판정했다. 이는 보류·부분 근거 처리가 여전히 부족하다는 이번 문제집의 관찰이다. 시험 공개 후 이를 보고 학습이나 질문을 수정하지 않았다.

**결론: 개선 미확인.** 정확도 차이 구간의 하한>0 기준을 통과하지 못했다. 제품 기본 SemIf와 설정은 유지한다. 제한된 head 학습이 실행됐다는 사실과 유용한 판단 성능이 좋아졌다는 주장을 구분한다. 시험은소진 표시됐으며 새로운 후보를 조정하려면 새 시험이 필요하다.

### 실제 자료의 별도 개발 진단

확정66개를 같은 원본·후보로 각각 실행했다. 저장된 원요청26개는 당시 질문을 사용했고, 재구성한 실행 보고40개는 명시된 공통 판정 질문을 사용했다. 원입력을 변경하지 않았다.

| 목적 | 자료 수 | 정상 실행 | 정답(전체 자료 기준) | 입력 한도 초과 |
|---|---:|---:|---:|---:|
| 관련성 | 16 | 9 | 8 | 7 |
| 근거 관계 | 48 | 43 | 9 | 5 |
| 도구 적합성 | 2 | 2 | 1 | 0 |

원본과 후보의 결과는 위 표와 동일하다. 합계18/66이며12개입력은 `STATE_TRUNCATED` 거절을 실패로 보존했다. 성공한54개만 분모로 삼아 점수를 높이지 않았다. 이 자료는 개발에 노출돼 있고 관련성16개가 모두relevant이며 도구적합성은2개뿐이다. 독립 시험이나 실제 업무 개선 증거가 아니다. 개인 원문/예측은 로컬에만 보존한다.

### 메모리 수집 결함과 자원 재측정

첫 측정기의 WMI 조회는 제한된 환경에서 프로세스 목록을 받지 못하고도 종료코드0을 반환했다. 그때 기록된 working-set0은 **미측정**으로 무효 처리한다. Windows Toolhelp32와PSAPI로 소유 프로세스와 자식의 실제 working set을 읽도록 수정하고, 없는PID나0값은 실패로 처리한다. GPU 메모리를 측정한 것으로 표현하지 않는다.

품질 시험 결과·가중치·질문·보정은 고정하고, 동일 입력·순서·3회 반복으로 자원을 다시 측정한다. 이는 측정 오류를 바로잡는 반복이며 새로운 독립 품질 시험으로 세지 않는다. 이전품질 결과와 원시0값은 삭제하지 않고 미측정 이유를 기록했다. 유효 자원 결과는 `corrected/performance/`와 아래 표에 둔다.

| 모델 | 준비 시간 | 추론 p50 | 추론 p95 | 관측 최대 프로세스 메모리 |
|---|---:|---:|---:|---:|
| 제품 SemIf (GPU) | 28.33초 | 1055.8ms | 1220.0ms | 6.776GiB |
| 미학습 Laya (CPU) | 3.47초 | 292.5ms | 515.0ms | 1.400GiB |
| 후보 Laya (CPU) | 3.01초 | 294.0ms | 495.5ms | 1.399GiB |

모델별90문제×3회 순차 실행, 샘플 주기5초(조회 시간 추가), Windows Toolhelp32+PSAPI의 소유 프로세스 트리 working set이다. 실제 순간 최대치나 GPU 할당량을 뜻하지 않는다. 준비 시간에는 모델 무결성 검사·적재가 포함되며 추론 시간은 따로 측정했다. 후보의 전체p95 변화는-3.8%, 관측 메모리 변화는-0.0%다. 이전 품질 실행과 모든270개 판정이 모델별로 일치했다. 목적별p95·라벨별오류·개선/퇴보 목록은 로컬 `resource-comparison.json`과 `comparison.json`에 보존한다. 자원 측정 반복으로 시험을 새 독립 평가로 재분류하지 않았다.

실제 자료는66개 확정으로600개 하한까지534개 부족하다. 실제 독립 시험은 세 목적 각각30개씩 모두 부족하다. 관련성의 irrelevant/insufficient_evidence, 근거 관계의 contradicts/unrelated, 도구 적합성의 insufficient_evidence 확정 사례가 없다. 기존15개 연결 그룹은 개발에 노출돼 최종 시험으로 전환하지 않는다. 필요한 원문33개와 대조 미완료9개의 목록은 `actual-recheck.json`에 있으며, 사용자에게 일괄 라벨 작성을 요구하지 않는다. 이번 합성 실험에는 추가 사용자 입력이 필요하지 않았다.


### 최종 검증과 반영 범위

최종 코드에서 Ruff/형식 검사와 **357 passed, 2 skipped**(113.25초)를 확인했다. 변경 문서의 로컬 링크161개가 유효했고 합성270개에 개인 경로·실제 기록 식별값이 없음을 검사했다. 실제66개 원문·모델 가중치·실행 원시 결과는 로컬에 보존하며 Git에 추가하지 않는다. source/질문/hash/분할변경, 정답ID 유출, 시험 소진 후 학습, 시험 전 후보미고정, 다른모델 실행 중 해제, 존재하지않는PID를0메모리로 기록하는 경로를 회귀 검사한다.

실험 daemon11439/11440/11441의 모델 해제를 확인하고 소유PID를 종료했다. 현재 native Jev는 `matches_disk`, 제품엔진 `idle`, worker/broker 없음, 기존 profile fingerprint 유지, 모든 promotion=false다. 원격CI와 유료 자원은 사용하지 않았다. 검증한 준비·학습·평가·반입검사 코드와 합성자료, 기존 계획/결과/한영README를 기존 원격master에 반영한다. 이 결과는 합성 실험 완료이며 실제 독립 업무 평가 완료를 뜻하지 않는다.
## 2026-09-29 외부 사용법·학습 사례 조사

사용자의 외부 조사 요청에 따라 공식 문서·실제 학습 코드·재현 이슈를 확인하고 현재 구현과 대조했다. 아래는 **외부 보고와 코드 조사**이며 우리 환경에서 해당 방법을 재실행한 결과가 아니다. 기존 진단의 정답 4/15 불변, 손실 감소, encoder 불변, 실행 경로 일치는 그대로 유효하다. 원인은 아직 확정하지 못했지만 조사 없이 같은 제한 설정의 실패에서 멈추는 것은 불충분했다.

### ‘튜닝이 쉽다’의 서로 다른 의미

- [Ollaya Modelfile](https://ollaya.dev/docs/modelfile)은 질문·선택지, 확률 보정, 정밀도 등을 지정해 파생 모델을 구성한다. 질문 변경과 가중치 학습은 다른 작업이다. 하나의 양수 온도로 logits를 나누는 보정은 확률의 과신을 조절하지만 가장 높은 선택지를 바꾸지 않는다.
- [공식 Laya](https://github.com/NandhaKishorM/laya)는 업무별 추가 학습을 전제로 한 기본 모델의 한계를 직접 설명한다. 공개 벤치마크의 학습 후 점수를 우리 관련성·근거 관계·도구 적합성 성능으로 옮겨 해석할 수 없다.
- [Laya Studio](https://github.com/Hantlowt/laya-studio)는 동결된 표현에서 중심점·방향·변환 등을 만드는 별도 접근이다. 가중치 추가 학습과 다르며 현재 Ollaya에 설정 파일만 넣으면 동작하는지 검증되지 않았다. 이번에 설치하거나 외부 생성 API를 사용하지 않았다.

### 실제 학습 코드와의 차이

조사일은 2026-09-29다. 공식 코드 참조는 `9d955671415fc19f069b9cc998928075c1f255ec`, 커뮤니티 layaMOE 코드는 `866456198d29cca836c83e66f32fb556857c4292`로 고정했다. 읽은 원본과 SHA-256은 로컬 `.local/laya-finetuning/upstream-research-20260929/sources.json`에 남겼다. 다운로드한 코드는 실행하지 않았다.

| 항목 | 우리 실행 | 공식 학습 노트북 | 공개 CPU head 학습 사례 |
|---|---|---|---|
| 학습 대상 | encoder 고정, head/type embedding/scorer | encoder와 나머지 모듈 | encoder 고정, 복제한 head |
| 학습률 | 실제 실행 1e-5 | encoder 2.5e-5, head 1e-4 | head 6e-4 |
| 반복·자원 | CPU 4 threads, batch 1, 100/300 updates | 2 T4, 4 epochs, 누적 batch 64 | CPU, 6 epochs, 수천 학습 항목 |
| 목적 함수 | 정답 인덱스 cross entropy | soft cross entropy + RLCD | choice는 cross entropy |
| 추가 조건 | 고정 질문·선택지 순서 | cosine 학습률 감소 | encoder 특징 캐시, warmup/감소, 선택지 순서 섞기 |
| gradient clipping | 1 | 1 | 1 |

공식 수치는 [학습 노트북](https://github.com/NandhaKishorM/laya/blob/9d955671415fc19f069b9cc998928075c1f255ec/notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb)의 실제 코드에서 확인했다. 이는 현재 CPU 20분 예비 실행과 다른 범위다. 공식 RLCD를 쓰지 않았다는 사실만으로 우리의 cross entropy를 결함으로 단정할 수는 없다.

[layaMOE 작성자](https://github.com/vishalmysore/layaMOE)는 frozen head가 3e-5에서 거의 움직이지 않고 6e-4에서 학습됐다고 보고한다. [학습 코드](https://github.com/vishalmysore/layaMOE/blob/866456198d29cca836c83e66f32fb556857c4292/scripts/train_expert.py)에서도 6e-4 기본값을 확인했다. 다만 이 사례는 ModernBERT 기반 421M 모델이며 우리의 multilingual 322M과 다르다. 작성자가 시험을 보고 라우터를 한 번 수정했고 목적별 시험도 작다. 따라서 성공 보증이나 독립적인 품질 증거가 아닌 **head 학습률과 특징 캐시를 조사할 근거**로 사용한다. MoE 서비스나 라우터를 우리 프로젝트에 추가할 이유는 없다.

### 알려진 문제와 우리 증상에 적용되는 범위

| 원출처 | 공개 관찰 | 우리에게 주는 의미와 한계 |
|---|---|---|
| [선택지·검색 품질 이슈 #171](https://github.com/NandhaKishorM/laya/issues/171) | 제작자가 해당 검색 사례에서 개선이 없음을 인정하고 선택지 설명·순서 민감성을 설명 | 의미가 같은 선택지 순서 변경을 개발 진단에 포함할 근거. 해당 검색 작업의 실패율을 우리 작업에 적용하지 않음 |
| [다국어 한쪽 답 쏠림 #99](https://github.com/NandhaKishorM/laya/issues/99) | 긴 잡음 입력의 여러 선택지가 일부 답으로 쏠린다는 보고 | 우리와 증상은 유사하지만 입력 길이와 과제가 달라 동일 원인이라고 확정할 수 없음 |
| [부정문 실패 재현 #377](https://github.com/NandhaKishorM/laya/issues/377) | 0.3.20의 실제 다국어 모델에서 취소하지 말라는 일부 입력을 취소로 판정 | 금지·반례를 다루는 업무에서 중요한 재현 후보. 우리 설치 버전에서 아직 재실행하지 않음 |
| [공식 브라우저 특화 사례](https://github.com/NandhaKishorM/laya/blob/9d955671415fc19f069b9cc998928075c1f255ec/docs/finetune_browser_agent.md) | 실행 결과 기반 학습, 입력·선택지 표현과 잘림 문제를 함께 수정 | 실제 실행 근거와 입력 일치가 중요함. 우리의 합성 입력 감사는 잘림 0이므로 그 사례의 입력 한도 확장을 바로 복사하지 않음 |

현재 학습 환경은 Laya 0.3.5 / torch 2.8.0+cpu / transformers 4.56.2다. 조사한 최신 README는 0.3.21을 안내한다. [공식 학습 논의 #26](https://github.com/NandhaKishorM/laya/issues/26)에 후속 보정·적재 수정이 있으나, 현재 사용자 정의 학습기는 보정 자료를 별도 분리한다. 버전 차이만으로 정답 불변의 원인을 확정하거나 업데이트가 해결책이라고 주장하지 않는다. 패키지 교체는 별도 출력 동등성 확인이 필요하다.

### 판단과 다음 진단 우선순위

가장 직접적인 미검증 가설은 **업무에 비해 지나치게 보수적인 head 학습 조건**이다. 우리 1e-5는 공식 head 설정의 1/10이며 실제로 정답 확률과 손실은 움직였다. 그렇다고 더 높은 학습률이면 반드시 정답이 늘어나는 것은 아니다. 본체 표현의 한계, 선택지·부정문 민감성, 자료의 구분 가능성도 남아 있다. 모든 step의 gradient가 잘렸다는 관찰만으로 clipping을 원인으로 지목하지 않는다. 비교한 외부 구현도 같은 상한 1을 사용한다.

다음은 train/development만 사용하는 원인 분리다. 공개 부정문과 선택지 순서의 대조 입력으로 민감도를 확인하고, Python/Ollaya 원시 선택·확률을 대조한다. 그다음 같은 원본·15개 학습 자료에서 head 학습률을 비교한다. 동결 encoder의 특징을 캐시할 경우에는 먼저 원래 logits와 일치를 확인한다. 새 최종 시험은 이 진단에 사용하지 않는다. 상세 절차와 미실행 상태는 [계획 보완](laya-finetuning-plan.md#2026-09-29-외부-조사에-따른-진단-보완)에 기록했다.

이번에 완료한 것은 외부 조사와 코드 대조·계획 보완이다. 외부 예제 재현, 다른 학습률, encoder 학습, 새 시험 평가는 아직 실행하지 않았다. 결론은 계속 **개선 미확인**이며 Ollaya 전체 또는 Laya 전체가 학습 불가능하다는 결론은 아니다.

## 2026-09-29 후속 원인 진단 실행

승인된 후속 계획에 따라 감사·새 자료 고정·15개 학습 진단을 완료했다. 앞선 실험은 보존하며 새 실행 경로는 `.local/laya-finetuning/retry-20260929/`다. 진단이 실패하면 후보 탐색을 중단한다는 사전 기준을 적용했다. 사용자에게 라벨을 요청하거나 기준을 낮추지 않았다.

### 입력과 답안 감사

기존270개의 원래 입력·정답·근거와 선택지 구성을 재검토했다. 답안 검토에 모델 예측을 사용하지 않았으며 사람 검토로 기록하지 않았다. 기존 train90/development45/calibration45의 입력·정답을 유지했다. 새 시험90개는 NULL 이전, 링크 경로, 원자적 저장 복구, 시간대 중복 시각, 조직별 캐시, WAL 스냅샷, 스트림 취소, 페이지 cursor, 승인 범위, 유니코드 인덱스라는10개 상황으로 작성했다. 기존 물리·행정 업무의 이름만 바꾼 복제는 아니다. 다만 학습 자료와 시험 분야가 달라지는 합성 실험이며 실제 코딩 업무 일반화를 보장하지 않는다.

예약 뒤 작성·검토한 새 고정 자료는 train90/dev45/cal45/test90, 목적·라벨 균형 및 test 목적별10그룹·라벨별2그룹 조건을 통과했다. 기존 시험/실제 개발 노출 자료와 출처 그룹 및 입력 중복을 검사했다. 기존180개는 이전 검토를 보존하면서 예약과 검토 시점을 새 revision에 연결했다. 같은 Codex가 작성·검토하는 한계는 그대로다.

기존270개와 새270개 모두 토큰·질문·선택지 순서·정답 인덱스·MASK 위치를 검사했다. 최대359토큰, 잘림0이며 choice형 qtype=0과 라벨 인덱스가 일치했다. 목적별 질문은 하나이며 서로 다른 질문을 덮어쓰지 않고 거절한다. 입력/정답/출처hash가 검토와 묶여 있고 후보 ID에 정답 라벨은 없다. 이 검사는 의미 판단의 외부 독립 검증이 아니다.

실제 개발의 이전 실패12개는 원문을 그대로 토큰화했을 때1,091~1,633토큰으로 모두1,024한도를 넘었다. 관련성7개, 근거관계5개이며 직렬화 원문에서 첫 초과 문자 위치와 필드별 토큰 수를 로컬 `input-audit.json`에 기록했다. 요약·절단으로 성공 처리하지 않았다. 실제66확정/42보류, 실제 평가600기준은 변경하지 않았다.

새 자료: [문제·답안·근거](../evaluations/laya-finetuning/synthetic-retry-20260929.json). 고정 JSONL SHA256은 `b4e5f0957f54e05f72fddca7e7af083106cd92e2fa460cf86f06e56c8cf3b4c0`이다. 새 시험은 **예측 미실행·미소진**이다. 이전 시험 소진 표식은 그대로다.

### 같은15개 반복 학습 결과

ID순 선택 규칙에 따라 library/meal 두 상황의 목적별5개를 사용했다. 근거관계5라벨 전체, 관련성 relevant2/irrelevant1/insufficient2, 도구 fit1/unfit2/insufficient2다. 두 상황을 반복 학습한 진단이며 독립 성능 평가가 아니다.

| 시점 | 동일15개 정답 | 평가모드 평균 NLL |
|---|---:|---:|
| 학습 전 | 4/15 | 2.054917 |
| 100 step | 4/15 | 1.898715 |
| 300 step | 4/15 | 1.670520 |

원본부터 seed20260928, AdamW1e-5, batch1, CPU4threads로 실행했다. 총260.08초, 최대step1.234초로 제한 안에 완료했다. head/type embedding/scorer31개 텐서가 변경되고 encoder는 그대로였다. 모든step에서 각 학습 모듈의 유한한 gradient와 update를 기록했다. 전체300step에서 clip 이전 gradient norm이1을 넘었으며 기존clip1을 그대로 적용했다. 이것만으로 clipping이 실패 원인이라고 단정하지 않는다.

틀린11개 모두 정답 확률이 올라가고 정답과 최고 오답의 점수 차이가 줄었지만, 어느 것도 역전하지 못했다. 기존 정답4개의 정답 확률도 내려갔다. 이는 분포가 덜 확신하는 방향으로 움직였다는 관찰이며 올바른 의미 구별 능력이 개선됐다는 증거는 아니다. **평균 손실 감소 조건은 통과, 정답수 증가 또는14/15 조건은 실패**다.

직접 logits와 Python 추론은 0/100/300step의 각15개에서 선택과 온도 적용 후 확률이 일치했다. 저장/재로드12개 선택도 모두 일치하고 최대확률차0.0001로 기준0.005 이하다. 원본 Python과 Ollaya 경로12개는 선택·확률 차이0으로 일치했다. 진단 가중치SHA256은 `2e8771bbb091fa974e364bb21b60d37e140c9e3be7eff9dca119a8c896c735c1`이다. 진단 모델 자체는 Ollaya로 포장하지 않았으며, 포장·후보 고정에서 명시적으로 거절한다.

확인된 사실은 학습 갱신과 실행 경로 일치, 그리고 제한된 반복 학습에서 선택 불변이다. 학습률·횟수·encoder고정·입력 표현 중 무엇이 주된 원인인지는 분리해 입증하지 못했다. 전체 모델이 학습 불가능하다고 해석하지 않는다.

### 미실행 단계와 재현

A/B/C 후보 학습, 개발 후보 선택의 실제 모델 비교, 보정, 후보 반입, 새 최종 시험과 bootstrap은 **진단 기준 미달로 미실행**이다. 도구의 설정 검증·진단 자료 제한·개발 선택·후보 고정 차단은 구현하고 로컬 검사했다. 제품 설정과 원본 모델은 유지한다. 새 시험을 열지 않았으므로 새로운 성능 수치는 없다. 결론은 **개선 미확인**이다.

원본 모델 및 가상환경이 설치된 저장소 루트에서 실행한다. 출력은 반드시 아직 없는 별도 디렉터리를 사용한다. 아래는 실제 실행과 같은 입력을 재현하는 명령이며 기존 결과를 덮어쓰지 않는다.

```powershell
$retry='.local/laya-finetuning/retry-reproduction'
.venv/Scripts/python.exe -X utf8 -m scripts.prepare_laya_training_data prepare --data-profile synthetic-experiment --reviewed evaluations/laya-finetuning/synthetic-retry-20260929.json --output "$retry/frozen"
$diagnosticIds=@('synth-library-0','synth-library-1','synth-library-2','synth-meal-0','synth-meal-2','synth-library-3','synth-library-4','synth-library-5','synth-meal-3','synth-meal-4','synth-library-6','synth-library-7','synth-library-8','synth-meal-7','synth-meal-8')
[IO.File]::WriteAllText((Join-Path (Get-Location) "$retry/diagnostic-ids.json"), (ConvertTo-Json -InputObject $diagnosticIds), [Text.UTF8Encoding]::new($false))
.local/laya-venv/Scripts/python.exe -X utf8 -m scripts.train_laya_pilot train --data-profile synthetic-experiment --dataset "$retry/frozen/frozen.jsonl" --manifest "$retry/frozen/prepare.json" --split train --steps 300 --learning-rate 1e-5 --diagnostic-ids "$retry/diagnostic-ids.json" --output "$retry/diagnostic"
# 같은 인수에서 train을 reload로 바꾸면 저장 후 참조 추론을 기록한다.
```

원래 실행은 기존 비공개 actual-reviewed.jsonl 및 이전 시험을 노출 자료로 연결해 추가 충돌 검사를 수행했다. 위 공개 재현은 개인 자료를 요구하지 않으며 저장소의 시험 재사용 회귀 검사와 함께 검증한다. 결과는 train.json의 diagnostic_snapshots/steps/diagnostic_passed, reload.json, base-runtime-parity.json, review-audit.json, input-audit.json에 보존한다. 마지막 네 파일의 개인 정보가 포함될 수 있는 원시 기록은 Git에 올리지 않는다. 사용자가 추가로 작성할 문제나 답안은 없다.

### 후속 코드 검증

최종 전체 검사 **362 passed, 2 skipped**(119.35초), Ruff와형식검사162파일, 변경 문서 로컬링크162개를 확인했다. 진단 모델의 포장을 실제 명령으로 요청했을 때 `Diagnostic checkpoints cannot be packaged`로 거절됐고 저장소가 생성되지 않았다. 진단 ID의 중복/미등록/분할 혼입, 승인 외 학습 설정, 개발 선택의 입력 변경/실패/시험 혼용/정확도 퇴보, 진단 후보 고정과 기존 시험 상황 재사용을 거절하는 검사를 포함한다.

첫 전체 검사는 긴 임시 디렉터리 경로에서 파일 생성 오류가 났고, 경로를 줄인 실행은 기본cp949 디코딩으로6개가 실패했다. 최종 검사는 기존 코드나 기준을 바꾸지 않고 짧은 경로와 UTF-8 환경으로 실행했다. 재현 조건은 다음과 같다. 실패 실행 로그도 로컬에 보존한다.

```powershell
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short --basetemp .local/qa-retry-utf8 -p no:cacheprovider
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
```

원본 대조용으로 실행한 소유 Ollaya daemon11442는 모델 해제 후 종료했다. native Jev는 `matches_disk`/idle, 기존 profile fingerprint 및 promotion=false를 유지한다. 재시작은 필요하지 않다. 개인 원문·진단 가중치·로컬 실행 로그는 업로드하지 않고 검증한 코드·문서·합성 자료만 기존 원격에 반영한다.

## 2026-09-29 외부 재현·학습률 비교 실행

승인된 계획과 자체검토를 실행 전에 고정했다. 실행 경로는 `.local/laya-finetuning/lr-20260929/`이며 기존 실행을 덮어쓰지 않는다. 현재 상태: 코드·40개 실행 경로 대조 완료, 추가 학습은 사용자 요청으로 보류. 실행 결과는 아래와 같다.

### 40개 공개 재현·선택지 순서 대조

`evaluations/laya-finetuning/lr-probes-20260929.json`의 40개 입력을 예측 전에 고정했다. payload hash는 `ebe31fb1ffb9544d88a1036eaed6237b706e92e4db45d1ed12d808586eb8b575`다. 기존 train15와 역순15, 공식 #377 원문5와 역순5이며, 같은 의미의 원본·역순을 독립40개로 세지 않는다. 질문·라벨 순서를 그대로 전달했고 입력별 원출처·정답 이유·hash를 보존했다. 사람 검토가 아닌 Codex 검토다.

Python과 Ollaya는 **40/40 선택 일치, 최대 확률차0**, 실패0이었다. 원본15개 정답4개, 공개 원문5개 정답3개이며 역순에서도 각각 같았다. 20쌍 중 선택 변화0개다. 공개 부정문 중 앞서 취소를 언급한 뒤 취소하지 말라고 명시한 두 사례는 양쪽 실행기에서 모두 틀렸다. 이 설치 버전에서도 공개 증상을 재현했지만 부정문 전체의 실패율로 확대할 수 없다. 이번 입력의 오답을 Ollaya 전달 오류로 설명할 근거는 없고, 순서 민감성도 이20쌍에서는 관측되지 않았다.

원시 입력별 결과는 로컬 `probe-python/probe.json`, `probe-ollaya/probe.json`, `probe-parity.json`에 보존한다. 기존 multilingual checkpoint와 Ollaya0.7.3/CPU 설정을 사용했으며 패키지 업데이트는 없었다.

### 학습 실행과 사용자 요청에 따른 보류

| 실행 | 실제 진행 | 동일15개 평가 | 종료 이유 |
|---|---|---|---|
| D1 / 1e-5 | 252 updates | 0회4/15·NLL2.054917, 100회4/15·NLL1.898715 | 실행 중 자원 하한 위반; 조사 시 가용메모리 약1.6GiB |
| D2 / 1e-4 | 0 updates | 미측정 | 적재 전 가용4GiB 조건 미달 |
| D3 / 6e-4 | 0 updates | 미측정 | 적재 전 가용4GiB 조건 미달 |
| D1 재시도 | 123 updates | 0회·100회 결과는 위와 동일 | 사용자가 학습 보류 요청, 실행기와 자식 학습 프로세스 종료 |

어느 설정도300회 진단을 완료하지 못했다. **높은 학습률의 효과는 아직 측정되지 않았고 후보 학습·보정·반입·새 최종 시험은 미실행**이다. 자원 실패나 사용자 중단을 모델의 학습 실패로 계산하지 않는다. 기존 결론인 개선 미확인은 유지한다.

호스트 조사에서 WSL의 높은 메모리 사용을 확인했다. Linux 파일 캐시를 회수했으나 다른 프로젝트의 Docker 서비스는 종료하지 않았다. Windows 가용메모리가4GiB를 회복한 뒤 새 출력 경로로 D1을 재개했고, 이후 사용자가 “이번에는 코드·검사까지 마무리하고 학습은 보류해”라고 지시해 중단했다. 재시도 원시 train.json은 강제 종료 당시의 부분 기록으로 보존하고 `user-stop.json`으로 종료 이유를 별도 연결한다. 부분 기록을 완료된 학습 보고서로 수정하지 않는다.

실패 뒤 다른 설정을 즉시 적재하려던 첫 실행기의 절차는 부족했다. 재개 시에는 모델 해제와4GiB 회복을 먼저 확인하고 새로운 실행 디렉터리를 예약해야 한다. 사용자 보류 이후 자동 재시도·예약 작업은 남기지 않는다. 소유 Ollaya daemon11443도 모델0개 확인 후 종료했다. 제품 설정·원자료·미사용test90은 보존했다.

### 코드 변경과 재현 방법

평가 CLI에 `probe --probe-dataset`과 Python backend를 추가했다. frozen 입력과 혼용을 거절하고 사례별 질문을 그대로 사용하며, 순서 포함 hash·역순 쌍 의미 라벨을 검사한다. 오류·미실행 행을 보존하고 기존 실행을 덮어쓰지 않는다. 학습기의 높은 학습률은 synthetic-experiment/300회만 허용하며 진단과 최종 후보는 계속 분리한다. 별도 후보의 재로드 검사에 같은15개를 지정할 `--parity-ids`를 추가했다. 후보 고정은 학습기·평가기 코드 파일도 hash로 잠근다.

다음은 보류 해제 후 사용할 명령이며 이번에 완료된 실행으로 해석하지 않는다. 출력 디렉터리는 매번 새 이름을 사용한다.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH='src'
# 격리 Ollaya 서버: CPU, MAX_LOADED_MODELS=1, OMP_NUM_THREADS=4, 루프백11443.
# Python 대조는 기존 호환 reference-base를 사용하며 원본 가중치는 변경하지 않는다.
.local/laya-venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya probe --data-profile synthetic-experiment --probe-dataset evaluations/laya-finetuning/lr-probes-20260929.json --backend python --python-model .local/laya-finetuning/retry-20260929/diagnostic/reference-base --endpoint http://127.0.0.1:11443 --output .local/laya-finetuning/lr-resume/probe-python
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya probe --data-profile synthetic-experiment --probe-dataset evaluations/laya-finetuning/lr-probes-20260929.json --backend ollaya --endpoint http://127.0.0.1:11443 --output .local/laya-finetuning/lr-resume/probe-ollaya
# 실제 학습은 사용자의 보류 해제 후, 자원과 대조 통과 확인 뒤 실행한다.
.local/laya-venv/Scripts/python.exe -X utf8 -m scripts.train_laya_pilot train --data-profile synthetic-experiment --dataset .local/laya-finetuning/retry-20260929/frozen/frozen.jsonl --manifest .local/laya-finetuning/retry-20260929/frozen/prepare.json --split train --steps 300 --learning-rate 1e-4 --diagnostic-ids .local/laya-finetuning/lr-20260929/diagnostic-ids.json --output .local/laya-finetuning/lr-resume/D2
```

코드의 출력 보존·오류 처리 보완은 모델 대조 이후에도 이루어졌다. 40개 모델 출력은 대조 시점의 코드 hash와 연결된 결과이며 최종 코드의 새 모델 실행을 했다고 표현하지 않는다. 사용자 보류 이후에는 모델을 다시 실행하지 않고 로컬 회귀 검사만 수행한다.

최종 로컬 검사: **367 passed, 2 skipped**(121.37초), Ruff 및 형식 검사162파일, 문서 로컬링크16개, 공개probe40개 출처/해시/개인 경로 제외 확인. UTF-8 환경과 짧은 pytest 임시 경로를 사용했다. 학습 보류 후 모델 재실행은 하지 않았다.

마지막 검토에서 후보 모델뿐 아니라 원본 Laya·제품 SemIf의 최종 시험도 후보 고정 전에는 차단하도록 보완했다. 이후 관련 검사 **58 passed**(1.73초), Ruff/형식 재검사 통과. 앞의367개 전체 검사는 이 마지막 보완 전 결과이며 전체를 다시 실행한 것으로 표현하지 않는다.

## 2026-09-29 WSL 상한 변경 후 완료 진단과 후보 비교

사용자가 학습 재개를 요청했고 호스트 가용 약9.1GiB를 확인한 뒤 새 경로 `.local/laya-finetuning/resume5-20260929/`에서 실행했다. 이전 메모리 실패·사용자 중단은 보존한다. 최종 코드의40probe는 다시40/40 선택일치·최대확률차0으로 통과했다. 본 실험은 Windows CPU 실행이며 WSL5GB 설정은 다른 작업의 호스트 메모리 사용을 제한하기 위한 환경 변경이다.

### 세 학습률 진단 완료

| 설정 | 0회 정답 | 100회 정답 | 300회 정답 | 300회 NLL | 실행 시간 | 진단 통과 |
|---|---:|---:|---:|---:|---:|---|
| D1 / 1e-5 | 4/15 | 4/15 | 4/15 | 1.670520 | 326.12초 | 미통과 |
| D2 / 1e-4 | 4/15 | 4/15 | 15/15 | 0.101841 | 322.48초 | 통과 |
| D3 / 6e-4 | 4/15 | 6/15 | 8/15 | 1.147247 | 290.22초 | 통과 |

세 실행의 원본 모델·코드·자료 hash와300단계 사례 순서가 모두 일치한다. 각 실행은 정상 완료됐고 encoder는 변경되지 않았다. D1 checkpoint는 이전 완료 진단과 동일한 `2e8771bbb091fa974e364bb21b60d37e140c9e3be7eff9dca119a8c896c735c1`이다. D2는 `7b67abdc7260ad204eb8a7cd114fccfbd7c0b07bd6d82779a0c91039961c3e35`, D3는 `0ecb3c67b84708a7a25c3d53afdc7be2d35bce714b9200ac1f81e56eb51d2402`다.

**원인 범위가 좁혀졌다:** 고정된15개를 배우는 데 기존1e-5/300회 조건이 지나치게 보수적이었다는 직접 비교 근거가 생겼다. 동일 조건의1e-4는15/15를 학습했으므로 이 경로 전체가 학습 불가능하다는 가설은 지지되지 않는다. 하지만6e-4가 더 나빴으므로 학습률 증가 자체를 일반적인 해결책으로 삼지 않는다. 이는 학습한 문제의 진단이며 새 문제나 실제 업무의 성능 개선 증거는 아니다.

D2/D3만 각 원본부터 train90/300의 C2/C3 후보를 만들었다. 원본을 development45에서 먼저 측정하고 후보 선택까지 이 분할만 사용했다. 진단checkpoint를 이어 학습하거나 배포하지 않았다.

### 후보 학습·개발 선택·보정 완료

D2/D3 진단 통과 뒤 각 원본에서 train90/300회로 새로 학습했다. C2(1e-4)는255.69초, C3(6e-4)는289.60초에 완료됐다. 두 실행 모두 encoder를 유지하고 head/type embedding/scorer의31개 tensor만 변경했다. 작은15개를 외운 진단checkpoint는 후보에 사용하지 않았다.

| development45 | 목적별 정확도 평균 | 정답 | 중요 오판 | NLL | 적격 |
|---|---:|---:|---:|---:|---|
| 원본 Laya | 33.33% | 15/45 | 22 | 1.736490 | 기준 |
| C2 / 1e-4 | 33.33% | 15/45 | 22 | 1.231125 | 예 |
| C3 / 6e-4 | 33.33% | 15/45 | 20 | 1.263617 | 예, 선택 |

정확도가 같으므로 사전에 정한 두 번째 기준인 중요 오판 수로 **C3를 선택**했다. C2의 낮은 NLL이나 진단15/15를 근거로 선택 순서를 바꾸지 않았다. 개발 자료는 frozen development45에서 입력·질문·라벨 순서를 대조해 만든 Python probe로 평가했다. 원시 probe와 분할/hash를 연결한 development 보고서를 보존했다.

C3 저장 후 재로드15개와 격리 Ollaya 반입15개 모두 선택 일치15/15, 최대 확률차0으로 통과했다. calibration45만으로 원본 온도3, 후보 온도1.5를 선택했다. 온도는 비교 보고서의 확률 지표에 적용했으며 제품 설정은 바꾸지 않았다. 후보 checkpoint·보정·코드·자료·토크나이저·실행 조건과 runtime hash를 고정한 뒤 SemIf→원본→후보 순으로 시험을 실행했다.

### 새 합성 시험90개 결과

**결론: 개선 미확인.** 원본35/90(38.89%), 후보33/90(36.67%)이다. 중요 오판 합계는45→43개지만 관련성과 도구 적합성에서는 각각1개 늘었다. 전체8개는 정답으로 개선됐고10개는 오답으로 퇴보했다. 다른 오답으로 바뀐 경우는 이8/10에 포함하지 않는다. 선택이 실제로 바뀌었으므로 이전의 무변화 문제는 벗어났지만, 새로운 상황에서 정답을 더 잘 고르는 효과는 입증하지 못했다.

| 목적 | 원본 정답 | 후보 정답 | 정확도 차이 | 그룹 bootstrap95% 구간 | 중요 오판 원본→후보 | 개선/퇴보 사례 수 | 기준 통과 |
|---|---:|---:|---:|---|---:|---:|---|
| 관련성 | 16/30 | 14/30 | -6.67%p | [-20.00, 6.67]%p | 10→11 | 1/3 | 미통과 |
| 근거 관계 | 8/30 | 9/30 | +3.33%p | [-16.67, 23.33]%p | 22→18 | 5/4 | 미통과 |
| 도구 적합성 | 11/30 | 10/30 | -3.33%p | [-16.67, 10.00]%p | 13→14 | 2/3 | 미통과 |

목적별10개 상황 그룹을 단위로 paired bootstrap10,000회(seed20260928)를 적용했다. 세 구간 모두0을 포함한다. 세 번 반복한 관측은 시간 측정에 쓰며 정확도 표본을270개로 부풀리지 않는다. 총90개, 목적별30개인 합성 시험이고 같은 Codex가 문제 작성·검토를 맡았으므로 외부 독립 평가나 실제 업무 평가가 아니다.

제품 비교는 별도다. 제품 SemIf는 GPU, Laya는 CPU이며 설정을 보존했다.

| 제품 비교 | 관련성 | 근거 관계 | 도구 적합성 | 합계 | 중요 오판 합계 |
|---|---:|---:|---:|---:|---:|
| SemIf / GPU | 23/30 | 15/30 | 24/30 | 62/90 (68.89%) | 17 |
| 원본 Laya / CPU | 16/30 | 8/30 | 11/30 | 35/90 (38.89%) | 45 |
| 후보 C3 / CPU | 14/30 | 9/30 | 10/30 | 33/90 (36.67%) | 43 |

SemIf도 이 시험에서 지지 정답6개를 모두 모순으로 분류했고 부분 근거6개 중1개만 맞혔다. 제품 비교 우위가 모든 라벨의 신뢰성을 뜻하지 않는다. 이전55/90과 이번62/90은 서로 다른 시험이므로 SemIf 자체가 개선됐다는 비교로 사용하지 않는다.

라벨별 오답 수(반복0, 분모는 해당 정답 라벨 수):

| 목적 / 정답 라벨 | 사례 수 | SemIf 오답 | 원본 Laya 오답 | 후보 C3 오답 |
|---|---:|---:|---:|---:|
| 관련성 / `insufficient_evidence` | 10 | 5 | 10 | 10 |
| 관련성 / `irrelevant` | 10 | 0 | 0 | 1 |
| 관련성 / `relevant` | 10 | 2 | 4 | 5 |
| 근거 관계 / `contradicts` | 6 | 0 | 4 | 5 |
| 근거 관계 / `insufficient_evidence` | 6 | 4 | 6 | 6 |
| 근거 관계 / `partial` | 6 | 5 | 6 | 6 |
| 근거 관계 / `supports` | 6 | 6 | 0 | 3 |
| 근거 관계 / `unrelated` | 6 | 0 | 6 | 1 |
| 도구 적합성 / `fit` | 10 | 3 | 6 | 6 |
| 도구 적합성 / `insufficient_evidence` | 10 | 2 | 7 | 9 |
| 도구 적합성 / `unfit` | 10 | 1 | 6 | 5 |

주요 변화의 원문 대조:

- `retry-sandbox-link-3`: “업로드 링크의 최종 대상은 허용 폴더 안이다”라는 주장에 “모니터 화면 밝기가70”이라는 근거를 주었다. 무관한 내용의 정답 `unrelated`를 후보가 새로 맞혔다.
- `retry-migration-null-4`: “NULL이 유지됐다”는 주장에 “이전 후 NULL이 아닌 빈 문자열”이라는 관측을 주었다. 정답은 `contradicts`지만 후보는 원본이 맞힌 문제를 틀렸다.
- `retry-migration-null-0`: NULL/빈 문자열 보존 조사에 실제 전후 값이 적힌 가상 기록을 주었다. 관련 자료를 후보가 무관으로 오판했다.
- `retry-unicode-offset-1`: 문자 위치 변환 조사에 바탕 색상만 적힌 후보를 주었다. 무관한 자료를 후보가 관련으로 오판했다.

모든 예시는 합성 기록이다. 입력·추천 정답·근거는 기존 `synthetic-retry-20260929.json`, 전체 사례별 선택·확률·변경 목록은 로컬 `*/test.json`과 `comparison/comparison.json`에 남아 있다. 정보 부족과 무관, 부분 근거 구별은 여전히 약하다. 라벨 빈도나 문장 패턴에 치우쳤는지, encoder 표현력이 부족한지는 이번 실험만으로 확정하지 않았다. 이를 확인하려고 소진된 시험으로 다시 후보를 조정하지 않는다.

### 실행 비용과 실패 보존

각 모델90개×3회, 총810회가 모두 관측됐고 실패0·미실행0·반복 간 선택 변화0이었다. 아래 추론 시간은 준비 시간을 제외한270개 요청 전체다. 메모리는5초 간격으로 관측한 프로세스 트리 working set 최대값이며 순간 peak나 GPU 메모리를 포함한 총량이 아니다.

| 실행 | 준비 시간 | 추론 p50 | 추론 p95 | 관측 메모리 최대 |
|---|---:|---:|---:|---:|
| SemIf GPU | 119.55초 | 1116.33ms | 1723.76ms | 6.776GiB (7275565056bytes) |
| 원본 Laya CPU | 5.03초 | 497.16ms | 772.91ms | 1.399GiB (1501790208bytes) |
| C3 CPU | 5.50초 | 422.56ms | 640.02ms | 1.397GiB (1500446720bytes) |

| 목적 | 원본 p50/p95 | 후보 p50/p95 |
|---|---:|---:|
| 관련성 | 298.87/453.07ms | 269.18/362.47ms |
| 근거 관계 | 580.27/790.94ms | 498.64/663.36ms |
| 도구 적합성 | 582.04/813.02ms | 492.31/684.48ms |

이번 순차 측정에서 후보의 p95·메모리는10% 악화 기준에 걸리지 않았다. 하지만 정확도 구간과 중요 오판 기준을 통과하지 못했으므로 적용하지 않는다. 같은 구조의 모델이며 실행 순서와 시스템 부하의 영향도 있어, 측정 시간 감소를 학습으로 인한 일반적인 속도 개선이라고 단정하지 않는다. SemIf 준비119.55초도 별도 기록했고 추론 시간에 숨기지 않았다.

보정 후 시험 전체 NLL은 원본1.298459→후보1.263104, Brier는0.714671→0.707509다. 확률 지표의 작은 감소를 정답률 개선으로 대체하지 않는다.

### 고정 식별값·재현 기록

이번 실행 코드는 `ee4d83e3999ab9bb63426b94d681e1c0caabc548`이며 코드 수정 없이 완료했다. 설치 버전은 Laya0.3.5, torch2.8.0+cpu, transformers4.56.2, Ollaya0.7.3이다. 학습 공통 조건은 seed20260928·batch1·CPU4 threads·AdamW weight_decay0.01·clip1·300updates다. encoder 고정, head/type embedding/scorer만 갱신했고5개 실행 모두300회·20분/step30초 제한 안에 완료했다.

| 대상 | SHA256 |
|---|---|
| 원본 checkpoint | `9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204` |
| C2 checkpoint | `a2bd76ab0393514839201fe361647d189053f65c9865e0c111ef3353be3a3279` |
| 선택 C3 checkpoint | `ce006280dd2ec6dbe1d6b1cfbeb1750782adf8cf914b1e2575bae7272279e2a3` |
| 고정 자료 | `b4e5f0957f54e05f72fddca7e7af083106cd92e2fa460cf86f06e56c8cf3b4c0` |
| 분할 manifest | `2603447e3c5f563740ca5f8f79f4768380422cc73fc87a5d8f0e610b66f5c8bf` |
| tokenizer.json | `609d8f4c067cd3950f88594c5a802616cea245823836ef5848ee4fc40aab5b6f` |
| tokenizer_config.json | `6c6b2d8e3c84ce0e671c129cd6b374b235d6f9863042a5836358d00a89bbb5a1` |
| trainer | `25cd85b4d6469ff21375c1391f66384f547878fda0911c2ac021ddf6d9ec777f` |
| evaluator | `c3609b11167e43df060263a305d29e170a6ea72802f01e2a98ccf9f78992914f` |
| C3 Ollaya manifest | `cf2508538095096564f458ab0c9220a7677bf7ffab7f20a0b61fd6194a21851b` |
| 후보·조건 잠금 | `875f07c57f6569747296376811c51a893f1b4affe0d652ccc299c7fafaefbeab` |
| 최종 comparison.json | `d7ea1ab8aa04aa229677c7a3a1ec30d0f021b39c7a35b007d854018a2a2dc989` |

질문별 hash와 입력 순서는 frozen/실행 행, 모델 store 전체 파일 hash는 candidate-lock, GPU profile 식별값은 product/test.json에 보존한다. calibration·원본 runtime·execution-conditions.json도 시험 전에 잠금에 추가했다. 최종 검증에서 잠긴 모든 파일 hash,810개 입력·정답·순서, 모델별 시작/종료 시점의 비중첩, 시험 소진 표식을 다시 대조했다.

다음은 이번에 실행한 CLI의 인자 구성이다. 자료 준비·15개ID 작성은 위 재현 절을 사용했다. 아래 경로의 실행 기록은 이미 완료됐으므로 **덮어쓰기/재실행하지 않는다**. 과거 재현 검토에 쓰고, 시험 결과를 보고 모델을 수정하려면 새 상황 시험을 준비해야 한다. 소진 표식을 지우거나 자료를 복사해 독립성을 되돌리지 않는다.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH='src'
$run='.local/laya-finetuning/resume5-20260929'
$frozen='.local/laya-finetuning/retry-20260929/frozen'
$ids='.local/laya-finetuning/lr-20260929/diagnostic-ids.json'
$dataArgs=@('--data-profile','synthetic-experiment','--dataset',"$frozen/frozen.jsonl",'--manifest',"$frozen/prepare.json")
# D1/D2/D3: rate=1e-5/1e-4/6e-4, name=D1/D2/D3; 매 실행은 원본부터 시작.
.local/laya-venv/Scripts/python.exe -X utf8 -m scripts.train_laya_pilot train @dataArgs --split train --steps 300 --learning-rate 1e-4 --diagnostic-ids $ids --output "$run/D2"
# 진단 통과 후 별도 후보.C2=1e-4, C3=6e-4.
.local/laya-venv/Scripts/python.exe -X utf8 -m scripts.train_laya_pilot train @dataArgs --split train --steps 300 --learning-rate 6e-4 --parity-ids $ids --output "$run/C3"
.local/laya-venv/Scripts/python.exe -X utf8 -m scripts.train_laya_pilot reload @dataArgs --split train --steps 300 --learning-rate 6e-4 --parity-ids $ids --output "$run/C3"
.local/laya-package-venv/Scripts/python.exe -X utf8 -m scripts.package_laya_pilot --pilot "$run/C3" --output "$run/candidate-store"
```

개발 평가에는 `load_frozen(..., 'development', 'synthetic-experiment')`의45개만 `development-probes.json`으로 연결했다. 기존 `probe --backend python --python-model <checkpoint>`로 원본/C2/C3를 순차 평가하고 `select_development_candidate`에 분할·입력 hash와 원시 보고서 hash를 연결해 전달했다. 로컬 `run_candidates.py`에 이 변환과 입력·질문·라벨 순서 대조를 보존했다. 새 수집 서비스나 공개 MCP 계약은 추가하지 않았다.

최종 실행은 모델을 동시에 적재하지 않는 격리 CPU daemon 두 개(원본11444, 후보11445), `OLLAYA_MAX_LOADED_MODELS=1`, `OMP_NUM_THREADS=4`를 사용했다. `--server-pid`에는 당시 실제 PID를 전달했다. 실행을 재현할 때 PID는 새로 확인해야 한다.

```powershell
# 원본은 endpoint11444/model laya:multilingual/store .local/ollaya-evaluation/models.
# 후보는 endpoint11445/model jev-laya:pilot/store $run/candidate-store.
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya frozen @dataArgs --split calibration --backend ollaya --model jev-laya:pilot --endpoint http://127.0.0.1:11445 --server-pid 578488 --model-store "$run/candidate-store" --output "$run/candidate"
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya freeze-candidate @dataArgs --checkpoint "$run/C3/checkpoint/model.safetensors" --model-store "$run/candidate-store" --calibration-report "$run/candidate/calibration.json" --candidate-lock "$run/candidate-lock.json"
# 후보/원본 보정과 추가 실행조건 hash 잠금 뒤 SemIf→원본→후보 각각 test(자동3회).
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya frozen @dataArgs --split test --backend ollaya --model jev-laya:pilot --endpoint http://127.0.0.1:11445 --server-pid 578488 --model-store "$run/candidate-store" --candidate-lock "$run/candidate-lock.json" --output "$run/candidate"
.venv/Scripts/python.exe -X utf8 -m scripts.evaluate_ollaya compare @dataArgs --candidate-lock "$run/candidate-lock.json" --baseline-report "$run/baseline/test.json" --candidate-report "$run/candidate/test.json" --product-report "$run/product/test.json" --output "$run/comparison"
```

실제 전체 순서는 로컬 `run.py`→`run_candidates.py`→`finish_selected.py`와 단계별 로그에 보존했다. 이 보조 실행 파일과 모델 store는 로컬 실행 산출물이며 원격 저장소에는 올리지 않는다. 공개 fixture·기존 CLI·이 문서가 재현 방법의 공개 부분이고, 원본 모델·로컬 환경·고정 자료의 준비가 별도로 필요하다.

### 완료 범위와 남은 한계

최종 로컬 회귀 검사68개 통과(3.07초), 관련 trainer/evaluator/package/test의 Ruff 통과. 잠금 파일·전체 관측·질문/입력/정답·순서·학습 대상·자원 종료 조건을 결과 파일과 재대조했다. 소유 Ollaya 서버11444/11445는 적재 모델0개 확인 후 종료됐고 SemIf 평가 worker도 종료됐다. 원격CI·유료 자원·제품 기본 모델 교체는 사용하지 않았다.

이번 승인 범위인 공개 재현·세 학습률 진단·두 후보 학습·개발 선택·보정·반입·새 시험 비교는 완료됐다. **학습 경로는 작동하지만 품질 개선은 미확인**이다. 자료/입력 표현의 일반화와 encoder 적응은 미해결 원인 후보이며 이번 범위를 넘는 추가 학습을 자동 실행하지 않는다. 새 시험90개는 이제 소진됐다. 실제 업무66개 확정·42개 보류,600개 기준과 독립 시험 부족은 그대로이며 이 합성 결과를 실제 개선으로 합산하지 않는다. 사용자에게 필요한 즉시 조치는 없다.


## 2026-09-30 자료 규모 비교: 준비와 실행 기록

이 절은 긴 실행을 중단한 시점의 기록이다. 15개 중6개 완료·2개 미완료·7개 미실행이며 **필요량 미확정·최종 개선 미확인**이다. 현재 모델 실행과 자동 재개는 없다. 실행 root는 `.local/laya-finetuning/curve-20260930-r3/`이며, 이전 root의 무효화된 revision2와 원본 모델·이전 실험 결과를 보존한다. 실제 자료66확정·42보류/600기준은 변경하지 않았다.

### 첫 산출물: 다양성·분리·검토

[공개 상황 목록](../evaluations/laya-finetuning/learning-curve-catalogue-20260930.json)에는6개 업무 주제별60개, 총360개의 작업과 두 판정 조건이 있다. [5400개 합성 입력·정답·이유·근거](../evaluations/laya-finetuning/learning-curve-20260930.json)는 그 상황별15개 문항이다. 이 문항들은 실제 실행 기록이 아니다.

| 분할 | 관련성 | 근거 관계 | 도구 적합성 | 상황 묶음 |
|---|---:|---:|---:|---:|
| train | 1350 | 1350 | 1350 | 270 |
| development | 150 | 150 | 150 | 30 |
| calibration | 150 | 150 | 150 | 30 |
| test | 150 | 150 | 150 | 30 |

모든 분할에서 관련성/도구적합성의 각 라벨은 목적 수량의1/3, 근거관계의 각 라벨은1/5이다. 각 주제는 train45묶음·다른 분할5묶음이다. 학습 부분집합450/1350/4050은30/90/270개의 전체 묶음으로 중첩하며 주제와 라벨 비율을 유지한다.

Codex가 작성한360개 조건과 문장 생성의 판정 규칙을 검토했고, 문항별 조합·입력/hash·라벨·근거 문장·출처 연결은 자동 검사했다. 개별5400문장을 사람이 수동으로 읽었다고 기록하지 않는다. `review.scope=authored_conditions_and_rendering_rules`, `individual_manual_read=false`, `human_reviewed=false`다. 같은 작성자와 공통 문장 구조를 사용하는 내부 합성 실험이다. 이름·숫자나 최소 변화만으로 독립 상황 수를 늘리는 검사를 거절하지만, 어휘 hash만으로 의미상 독립성을 증명할 수는 없다. 상황 간 공통 문법을 배운 성공을 실제 업무 일반화로 확대하지 않는다.

초기 고유 입력 검사에서120개 반복이 발견돼 모델 실행 전에 수정했다. 최초 fixture와 고정본은 로컬 revision1에 남겼다. revision2 실행 중 후보 ID 끝번호가 정답 라벨과 연결되고 질문 제약이 라벨별로 다른 결함을 발견했다. 개발 정확도 점수와 새 시험 예측을 열람하기 전에 실행을 중단했다. 그때 완료된450개/6epoch/seed20260928 실행도 무효로 보존하며 후보 선택에서 제외한다. 모든 라벨에 동일한 opaque 후보 ID와 질문 제약을 적용한 revision3으로 원본부터 다시 시작했다. 재시작으로 예산 시계를 리셋하지 않았고 두 root의 산출물을40GiB 한도에 합산한다.

revision3의5400개 목적/입력 쌍은 모두 고유하며 기존 실제·설명용44·공개 진단·이전 합성 자료852개와 자동 연결 충돌이 없다. `curve-20260930-r3/diversity-review.json`에 결과를 저장했다. 모든 분할과 답안은 새 모델 예측을 보기 전에 고정했다. 새 test450개의 예측은 아직 실행하지 않았다.

| 고정 항목 | SHA-256 |
|---|---|
| revision3 frozen.jsonl | `39ee5bb4e36ccbedaf2f4c5fd884dbf6364b43e3c64536b45f16225209c9fdfc` |
| 질문 | `fdc5984c609ffd1cd13078e7499bae8d42e5e8e75eaa4c62abcb9593c48b204a` |
| 분할 | `4934069dcab5073a6314240c75eab4cafe3d82dce3cc8bcc277a95769db200c5` |
| 선택지 표시 순서 | `f8e6507522f12a169a35ed3aad257e1307c4702a7b7cec47171961ead9506e20` |

### 첫 산출물: FP32 캐시 대조와 짧은 측정

revision3의 고정train15개를 원본 전체 경로와 새로 계산한 FP32 특징 경로로 다시 대조했다. 선택15/15일치·최대 확률차0으로0.0001기준을 통과했다. 무효화한 revision2의 캐시나 대조 결과는 재사용하지 않았다. encoder 특징을 train/development에 별도 저장하고, 입력·질문·토큰·원본 모델·토크나이저·분할·특징 파일 hash로 연결한다. 잘못된 데이터나 다른 모델의 캐시로 대체하지 않는다.

revision3의 optimizer 갱신 없는15개 forward/backward 측정은3.779초, 전체 학습 추정17.57시간·특징 준비 추정0.83시간이었다. 실제 실행별 시간을 별도로 보고하고 기존12시간 한도를 유지한다.

무효화한 revision2의 준비 단계에서는15개 forward/backward3.655초로 전체16740업데이트 약61190초(17.00시간), 특징 준비 약4260초(1.18시간)를 추정했다. 이 이전 측정은 기록으로만 보존하며 현재 실행 예측에는 revision3 측정을 사용한다. 워밍업을 포함해 과대 추정할 수 있으므로 실제 step 중앙값과 함께 해석한다. 승인된12시간·실행별90분·마지막2시간 확보 조건은 늘리지 않는다. 주 비교9개를 먼저 하고 남은 예산에서 대조6개를 수행한다. 시간 또는 자원으로 미완료인 실행을 정확도 실패나 효과 없음으로 해석하지 않는다.

캐시/조정기 코드와 학습 코드의 hash를 별도로 남긴다. 어떤 optimizer 갱신 전, 실패/미실행 행 보존과 저장 정밀도 및 메모리 기록을 보강했다. 캐시 생산의 encoder/결정 계산 경로와 고정 입력은 변경하지 않았으며 `implementation-audit.json`에 변경 이유와 두 코드 hash를 저장했다. 실행 시작 시각을 다시 설정하지 않는다.

### 외부 근거와 재현

[공식 학습 안내](https://huggingface.co/convaiinnovations/laya-typed-decisions#training)의1200사례/6000판단은 해당 공개 실험의 규모이며 최소 필요량이 아니다. [공식 notebook](https://github.com/NandhaKishorM/laya/blob/9d955671415fc19f069b9cc998928075c1f255ec/notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb)은 다른 모델/장치/encoder 학습을 포함한다. [layaMOE 작성자의 CPU 사례](https://github.com/vishalmysore/layaMOE#results)와 [고정된 학습 코드](https://github.com/vishalmysore/layaMOE/blob/866456198d29cca836c83e66f32fb556857c4292/scripts/train_expert.py)는 frozen head·6e-4·특징 캐시를 조사할 근거다. 성공 수치나 자료량을 우리 multilingual 모델의 보장으로 가져오지 않는다. 원본 조사 파일/hash는 기존 `upstream-research-20260929/`에 보존한다.

```powershell
$curve='.local/laya-finetuning/curve-20260930-r3'
$env:PYTHONPATH='src'
.venv/Scripts/python.exe -X utf8 scripts/prepare_laya_training_data.py prepare --data-profile synthetic-learning-curve --reviewed evaluations/laya-finetuning/learning-curve-20260930.json --output "$curve/frozen"
.local/laya-venv/Scripts/python.exe -X utf8 scripts/train_laya_pilot.py curve --data-profile synthetic-learning-curve --dataset "$curve/frozen/frozen.jsonl" --manifest "$curve/frozen/prepare.json" --split train --output $curve
.venv/Scripts/python.exe -X utf8 scripts/evaluate_ollaya.py curve-summary --data-profile synthetic-learning-curve --curve-root $curve --output $curve
```

위 명령은 당시 실행 인자 기록이며 현재 긴 실행의 재개 명령이 아니다. 고정본/기존 실행은 덮어쓰지 않는다. 별도 root를 사용하거나 코드를 수정해도 이번 예산 시계는 리셋하지 않는다. 변경된 trainer hash로 기존 ledger를 재개하는 것은 거절한다. 소진 시험 표식을 지우지 않는다. 실제 원문을 접근할 수 있는 로컬 감사에서 이전852개와 추가 검사했으며 개인 원문은 저장소에 포함하지 않는다. revision3 코드/핵심 회귀 검사는87passed다. 당시 전체 UTF-8 로컬 실행은372passed·3failed·2skipped였고, 기존 프로세스 준비/종료 시간 검사3개는 단독 재실행에서3passed였다. 이 실패 때문에 제품 코드를 변경하거나 검사 기준을 낮추지 않았다. Ruff와 diff 공백 검사도 통과했다. 이후 변경의 검사와 실행 여부는 아래 중단 기록을 따른다.

이후 보고서끼리 일치하더라도 고정 원자료와 다른 입력·질문·정답·순서를 거절하도록 결과 검증을 보강했다. 학습/캐시 계산과 trainer hash는 변경하지 않았다. 학습 도구66개·기존 평가/튜닝22개 검사(합계88개)를 통과했다. 준비 구현과 공개 자료는 `0a05f3d`로 기존 원격 master에 반영했으며 실제 모델 결과는 실행 종료 후 추가 반영한다.

### 첫 학습과 사용자 중단 후 재개

revision3 train 특징4050개는1760초, 별도 development 특징450개는249초에 완료했다. 첫450문항/6epoch/seed20260928 실행은180updates·모든 문항6회 노출·708초에 완료했다. encoder 변경 없이 head·type embedding·scorer가 갱신됐다. 원본과 첫 후보의 개발450개 입력·질문·정답·순서가 고정본과 일치함을 다시 대조했다.

| 목적(개발 각150개) | 원본 정답 | 첫 seed 정답 | 원본 중요 오판 | 첫 seed 중요 오판 |
|---|---:|---:|---:|---:|
| 관련성 | 56 | 139 | 52 | 7 |
| 근거 관계 | 47 | 112 | 103 | 37 |
| 도구 적합성 | 58 | 144 | 50 | 4 |
| 합계 | 161/450 | 395/450 | 205 | 48 |

이는 한 seed의 내부 합성 개발 결과다. 자료량의 충분성·포화·최종 시험 개선은 아직 확정하지 않는다. 학습 설정은 결과를 보고 바꾸지 않았다.

사용자의 중단 요청 후 재개 시 실제 프로세스가 남아 있지 않음을 확인했다. 두 번째 seed20260929는30updates의 마지막 저장 기록만 있고 checkpoint가 없었다. 해당 디렉터리와 로그를 `n450-e6-s20260929-interrupted-20260930T0225`로 같은 root에 보존하고 `execution.json`의 `interrupted_attempts`에 기록했다. 마지막 저장 이후 실제 갱신 수는 알 수 없으며 완료 모델로 세지 않는다. 원본부터 같은 seed를 재시작했고 완료된 첫 모델·캐시는 재사용했다. 전체 예산 시작 시각과40GiB 합산 범위를 유지한다.


### 450문항 세 seed와 최종 CPU 실행 조건 확인

revision3에서450문항·6epoch·180updates를 원본에서 세 번 완료했다. 같은 개발450개(목적별150개)를 사용한 중간 결과다.

| seed | 관련성 | 근거 관계 | 도구 적합성 | 합계 | 중요 오판 |
|---|---:|---:|---:|---:|---:|
| 원본 |56/150|47/150|58/150|161/450|205|
|20260928|139/150|112/150|144/150|395/450|48|
|20260929|121/150|101/150|144/150|366/450|74|
|20260930|143/150|103/150|143/150|389/450|53|

이 수치는 합성 개발 자료에서 세 seed 모두 원본보다 높다는 관측이다. 자료 규모의 포화나 최종 시험 통과를 뜻하지 않는다.1,350/4,050문항과 같은 업데이트 수 대조가 끝나기 전에는 필요한 자료량을 확정하지 않는다.

[설치 버전0.7.3의 공식 scheduler](https://github.com/ollaya-dev/ollaya/blob/b89397464ae6bb33627680eb5b0ea6fb5987b4dd/crates/ollaya-server/src/scheduler.rs#L433)를 대조한 결과 일반 daemon은 runner에 `--threads`를 전달하지 않는다. `OMP_NUM_THREADS=4`를 실제 ONNX4스레드 검증으로 대신하지 않는다. 동일 바이너리의 기존 `runner --threads 4 --device cpu` 경로를 평가 CLI의 `--runner-threads 4`로 선택할 수 있게 보완했다. 모델과 설정을 업데이트하지 않았으며 공개 MCP 계약도 유지했다. 설치 바이너리의 `runner --help`에서 해당 옵션을 직접 확인했다. 실제 모델의15개 대조와 최종 비교는 학습 뒤에 수행하며, 이 절 작성 시점에는 미실행이다.

새 로컬 검사에서는 logits/선택지 순서 연결·비정상 값·입력 초과 거절, blob hash 변경 차단, CPU/FP32/4스레드 실행 인자와 자식 프로세스 종료, 비교 중 다른 binary/미확인 스레드 조건 거절을 확인했다. 기존 관련 검사와 함께91개가 통과했다. 최초 기본 임시 폴더 실행은 Windows 접근 권한으로 fixture를 만들지 못했으며, 저장소 내 새 임시 검사 경로로 재실행했다. 실제 모델 검증을 단위 검사 성공으로 대신하지 않는다.

```powershell
$env:PYTHONPATH='src'
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_laya_training.py tests/test_ollaya_evaluation.py tests/test_ollaya_tuning.py -q -p no:cacheprovider --basetemp .local/test-runs/curve-runner-20260930-0312
```

공식[ONNX 출력](https://github.com/ollaya-dev/ollaya/blob/b89397464ae6bb33627680eb5b0ea6fb5987b4dd/crates/ollaya-runner/src/onnx.rs#L343)과[choice 렌더링](https://github.com/ollaya-dev/ollaya/blob/b89397464ae6bb33627680eb5b0ea6fb5987b4dd/crates/ollaya-decision/src/answer.rs#L110)을 추가 대조했다. Laya는 choice에도 보조 act 두 값을 반환하지만 선택과 확률은 option logits로 계산한다. 직접 경로는 유한한 보조 출력은 허용하고 choice에는 사용하지 않으며, 입력 조건별 온도나 온도 범위가 지정된 모델은 거절한다. 공식softmax와 첫 최대값 선택 방식도 대조했다. 수정 후91개 재검사와 보정 설정 거절을 포함한 직접 경로3개 검사가 통과했다.

### 1,350문항 중간 결과

6epoch·540updates의 세 seed를 완료했다. 모든 문항의 노출 횟수는6회이며 원본에서 각각 시작했다. 이후 최대 규모·반복량 대조는 아래 시간 문제로 미완료다.

| seed | 관련성 | 근거 관계 | 도구 적합성 | 합계 | 중요 오판 | 개발 NLL |
|---|---:|---:|---:|---:|---:|---:|
|20260928|144/150|115/150|148/150|407/450|38|0.341280|
|20260929|149/150|143/150|150/150|442/450|8|0.071360|
|20260930|150/150|138/150|148/150|436/450|14|0.099766|

`execution.json`의 실행 시간은 각각2,282.2초·2,158.4초·2,370.2초다. 세 seed 평균 정답률은450문항의85.19%에서1,350문항의95.19%로 높아졌다. 이는450개 개발 문항에서 각각 세 번 얻은 결과이며1,350개 독립 평가 표본으로 세지 않는다. 같은 seed의450문항 결과395개·366개·389개보다 높지만, 업데이트 수도180→540으로 증가했다. 자료량만의 효과나 포화의 증거로 해석하지 않는다. 최대 규모가 미완료이면 결과에 **최대 규모 미완료로 비교 미실행**을 표시하고 낮은 정확도와 구분한다. 해당 문구의 누락 실행 검사를 통과했다.

캐시용/학습용 원본 복사본의 설정도 실행 중에 대조했다. `encoder/config.json`의 SHA-256은 둘 다 `84a676ef3263dc1a96f2d1c057c8ec501f19555c92bea58f8643a7a4e4337427`, `rl_agent_config.json`은 둘 다 `25061739243b617ad88d1219ba6f8a9c86c5881ca28df024fa2d9b3b2fcc30c6`이다. `.local/laya-finetuning/curve-20260930-r3/reference-config-audit.json`은 캐시 작성 후 확인한 기록이며 이를 작성 당시의 자동 검사로 소급 표현하지 않는다.

### 첫 최대 규모 실행의 시간 한도

4,050문항·6epoch·seed20260928은1,620updates를 완료하기 전에90분 한도로 종료됐다. `watchdog-abort.json`은5,400.037초에서 `time_or_memory_limit`, 프로세스 종료 코드는124, coordinator 실행 시간은5,403.4초다. 마지막 저장된 학습 기록은1,290updates이고 그 이후 실제 갱신 수는 확정할 수 없다. checkpoint와 개발 정확도는 없으며, 이를 낮은 정확도나 학습 불가능으로 해석하지 않는다. 기존 결과를 보존하고 다음 seed는 원본에서 시작했다.

Windows 프로세스 조회·종료에 수초가 걸리는 것을 실측했다. 최종 검증용2시간을 확보하기 위해 원래 학습 cutoff30초 전부터 이 실험의 정확한 소유 PID 트리를 종료하도록 로컬 실행 도우미의 여유를 늘렸다. 당시에는 모델을 적재하지 않은 대기 도우미만 종료·검사·재시작했다. 아래 사용자 지적 이후에는 학습과 이 대기 도우미를 모두 종료했다. 실제 학습에 사용한 코드 hash는 `e7017e6361d4c92f036dcdf8ca16c5a96199935d1aaa7b549dd6e7aab6946a21`이며 원본 파일을 로컬 `trainer-e7017e63.py`로 보존했다.

### 긴 반복 중단과34초 병목 진단

사용자가 “또 그럼90분 넘게 기다리라고?”라고 지적한 뒤 이 실험의 coordinator/learner와 모델을 적재하지 않은 대기 도우미를 종료했다. 명령과 PID의 소유 관계를 대조하고 종료 후 해당 학습 프로세스가0개임을 확인했다. 다른 프로젝트의 서비스는 종료하지 않았다. 두 번째4050/seed20260929는 마지막 저장270updates이며 이후 갱신 수는 미확인, checkpoint와 개발 결과가 없어 완료 후보로 사용하지 않는다. 중단 전 `execution.json`을 `execution-before-runtime-diagnosis.json`으로 보존하고 현재 상태를 `development_stopped_for_runtime_diagnosis`로 기록했다.

| 실행 범위 | 완료 | 미완료 | 미실행 | 이유 |
|---|---:|---:|---:|---|
|450/6epoch·세 seed|3|0|0|개발450개까지 완료|
|1350/6epoch·세 seed|3|0|0|개발450개까지 완료|
|4050/6epoch·세 seed|0|2|1|첫90분 한도, 두 번째 긴 반복 중단, 세 번째 미시작|
|450/54epoch·세 seed|0|0|3|1620updates가90분을 넘길 것으로 실측 추정|
|1350/18epoch·세 seed|0|0|3|동일한 실행 시간 문제|
|합계|6|2|7|15개 설계는 미완료|

캐시 읽기와 계산 중 어디가 느린지 확인하기 위해 원본부터 CPU4·microbatch1/실효batch15·AdamW6e-4/wd0.01/clip1로5updates만 실행했다. 입력15개는 첫4050 실행의 첫 batch ID를 그대로 고정했다. 학습 자료만 사용하고 FP32 캐시 hash와 정답/선택지 연결을 확인했다. 결과는 `runtime-diagnosis/profile.json`, 로컬 측정 스크립트 hash는 `59bea057c91d6393b446557c6fde9bbe9873ff50ea72347d557105174f82f0f9`다. 이 스크립트는 기존 단계의 시간 분해용 로컬 산출물이며 원격 공개 실행기를 추가하지 않았다.

전체33.879초, 모델 적재9.592초로 종료했다. 학습 대상은14,770,945개 파라미터이며 encoder306,939,648개는 고정했다. 원본 모델 hash가 유지됐고 encoder gradient가 없음을 확인했다. checkpoint·배포 후보·품질 평가를 만들지 않았다.

| 단계 | 워밍업 이후4step 중앙값(초) |
|---|---:|
|캐시 읽기|0.02297|
|특징 검사|0.01343|
|forward|1.38455|
|backward|2.40126|
|gradient clipping|0.00465|
|optimizer|0.02787|
|유한한 파라미터 검사|0.01821|
|update 전체|3.86531|

중앙값들의 합은 전체 중앙값과 정확히 같지 않다. 계산/역전파가 약98%이며 캐시 I/O는1% 미만이었다. 이15개를 기준으로도1620updates는 약104.36분의 계산 시간이 예상된다. 모든 문항의 길이·CPU 부하를 대표하는 확정 시간이 아니며, 캐시를 개선해90분 안에 완료할 수 있다는 근거는 없다. 모델의 판단 능력이나 전체 학습 가능성을 이 시간 진단으로 판정하지 않는다.

### 실행 전 실측 시간 검사와 미완료 결론

기존 coordinator에 같은 코드·자료·원본·문항 수·CPU4·실효batch15의 관측만 사용하는 시간 추정을 추가했다. 완료 또는90분 watchdog 종료 기록의 최소30step을 검사하고 가장 빠른 실행의 중앙값을 사용한다. 전체 updates의 계산 추정이 실행별90분/원래 학습 예산 잔여보다 길면 모델을 시작하지 않고 `not_run` 및 `estimated_runtime_exceeds_run_or_training_budget`을 남긴다. 시간 실패를 낮은 정확도로 기록하지 않는다. Windows 시간 종료는 소유 자식 PID 트리까지 정리하며 watchdog 원문과 이유를 보존한다.

현재 원시 실행 기록에 새 추정 함수만 적용한 `runtime-forecasts.json`은 다음과 같다. 새 모델을 실행하거나 기존 ledger의 코드 hash를 수정하지 않았다.

| 문항/epoch | updates | 추정 계산 시간(분) | 사용한 실측 seed |
|---|---:|---:|---|
|450/6|180|10.49|20260928|
|1350/6|540|33.80|20260929|
|4050/6|1620|107.55|20260928(90분 종료)|
|450/54|1620|94.40|20260928|
|1350/18|1620|101.41|20260929|

이는 적재·저장·개발 평가를 제외한 낙관적 추정이며 CPU 부하에 따른 불확실성이 있다. 최초 추정17.57시간과 첫4050의 실제 실패가 있었는데도 다음 긴 seed를 시작한 것은 실행 가능성 검토가 부족했다. 이번 수정은 반복 낭비를 막지만 학습 계산이 빨라졌다는 결과는 아니다. 원래 예산 시작 시각은2026-09-30 09:39:52 KST,12시간 한도/최종2시간 확보 조건을 유지한다. 다른 root나 변경 코드로 시계를 리셋해 재실행하지 않는다. 고정 자료·원본·이전 산출물을 보존한다.

중단 후 다시 대조한 원본 모델 SHA-256은 `9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204`, frozen hash는 위 revision3 값 그대로다. 새 coordinator hash는 `18e80121bc43fda3dd8617a6e368c881b3efd3d68f6b9cb72e29e5dbb1dc614e`로 시간 검사·종료 기록 변경 이후의 값이며, 이전 실행의 코드 hash를 바꾸지 않았다. 두 실행 root의 산출물 합계14.302GiB로40GiB 이내이고, 소유 모델/대기 Python 프로세스가 없으며 후보 잠금·최종 test 보고서도 없음을 재확인했다.

학습/평가/튜닝 관련 로컬 검사94개가9.13초에 통과했고 Ruff도 통과했다. 새 검사에서는 다른 자료/코드/원본/장치/스레드/진단의 시간 기록 거절, 동일 실패가 예상되는 나머지 실행의 모델 미시작, 원래 시각 보존과 소유 Windows 자식 트리 종료를 확인했다. 직접 파일 CLI의 저장소 모듈 import 경로도 수정했고 모델 없이 `curve-summary`의 실제 실행을 확인했다.

```powershell
$env:PYTHONPATH='src'
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_laya_training.py tests/test_ollaya_evaluation.py tests/test_ollaya_tuning.py -q -p no:cacheprovider --basetemp .local/test-runs/curve-forecast-full-20260930-0654
.venv/Scripts/python.exe -X utf8 -m ruff check scripts/train_laya_pilot.py scripts/evaluate_ollaya.py tests/test_laya_training.py
# 요약만 다시 계산한다. 모델 학습/추론을 실행하지 않는다.
.venv/Scripts/python.exe -X utf8 scripts/evaluate_ollaya.py curve-summary --data-profile synthetic-learning-curve --curve-root .local/laya-finetuning/curve-20260930-r3 --output .local/laya-finetuning/curve-20260930-r3
```

`learning-curve.json`은 `incomplete`·필요량 미확정이다. 개발 자료의 대안 순위는1350/6epoch와 중간 seed20260930을 제시하지만, 전체 규모 비교의 완료나 최종 후보 고정이 아니다. 규모별 전체 구간·반복량 대조·포화 판단은 미완료이며 새450개 시험은 미사용 상태를 유지한다. 후보 보정450·재로드·Ollaya/4스레드 실제 반입 대조·최종 세 모델 시험은 미실행이다. 현재 모델/대기 도우미/자동 재개를 두지 않는다. 실행 조건을 충족하는 방법을 다시 검토하기 전까지 긴 학습을 시작하지 않는다.

이번에 확인한 것은 **내부 합성 개발 결과의 향상**과 **현재 CPU 실행 조건의 시간 한도 충돌**이다. 자료량만의 효과·충분한 양·실제 업무 개선·최종 시험 개선은 확인하지 못했다. 결론은 **필요량 미확정·최종 개선 미확인**이며 제품 기본 모델과 설정은 그대로다. 원격에는 검증한 코드·기존 문서·한영 README만 반영하고 개인 원문·모델·로컬 실행 파일은 포함하지 않는다. 사용자에게 필요한 즉시 조치는 없다.
