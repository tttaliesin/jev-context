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

다음에는 출처가 분리된 실제 업무 사례를 추가하거나, 합성·출처 기반 자료로 연구용 학습을 진행하는 것으로 범위를 정해야 한다. 후자의 경우에도 사람 검토 완료나 실제 업무 성능 검증으로 표시하지 않는다. 합성 자료를 사용한 본 학습으로의 범위 변경은 아직 확정하지 않았다.

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
