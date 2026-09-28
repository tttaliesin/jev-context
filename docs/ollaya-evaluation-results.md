# Ollaya 튜닝·실행기 비교 결과

2026-09-28 KST. [계획](ollaya-evaluation-plan.md) → [사전 자체 검토](ollaya-evaluation-review.md) → 격리 설치·실측·판정 순으로 실행했다.

## 결정

**현재 SemIf OpenVINO를 유지한다.** Ollaya의 준비·추론은 훨씬 빨랐지만, 이번 한국어 진단에서 질문 튜닝과 확률 보정 후에도 정답은 13/30, 중요 오류는 5개였다. SemIf는 27/30, 중요 오류 0개였다. 계획의 교체 후보 기준을 충족하지 않아 제품 어댑터나 중복 모델 실행 레이어를 추가하지 않았다.

이는 Ollaya 전체나 모든 모델의 평가가 아니다. Windows CPU의 `laya:multilingual` 322M과 현재 Intel GPU의 Qwen3.5-4B SemIf 구성 비교다. 에이전트 작성 사례 60개를 개발 30개·최종 진단 30개로 나눴으며 사람 검토를 거친 독립 평가가 아니다. 최종 사례도 같은 작성자의 유사 판단 유형이라 일반화 주장에 한계가 있다. 실제 코딩 작업 효율 개선이나 자동 추천 승격을 입증하지 않았다.

## 질문 튜닝과 보정

추론 전에 사례·분할·질문 두 후보와 선택 규칙을 고정했다. 기본 제품 질문은 개발 집합 15/30, 간결한 한국어 질문은 16/30이었다. 정해 둔 규칙에 따라 한국어 질문을 선택했다. 개발 NLL 최소 temperature는 **1.5**였다. 기본 모델 온도가 1.0이어서 절대 온도도 1.5를 `choice:3-5` 버킷에 적용했다.

이 설정으로 파생 모델을 실제 생성하고 추론했다. 별도로 생성한 Modelfile도 `ollaya create`에서 성공했다. temperature는 확률의 확신도를 조절하므로 argmax 정답 수는 바뀌지 않았다. 반올림된 기본 확률로 계산한 보정값과 실제 파생 모델 확률의 최대 차이는 0.000158이었다.

개발 집합의 “수락 정확도 90% 이상, 중요 오수락 0개, 최소 10개 수락”을 만족하는 임계값은 없었다. 따라서 계획에 따라 **전부 보류**를 선택했다. 파일의 `threshold: 1.01`은 실험 도구에서 수락 없음이라는 표시이며 제품에 적용할 임계값이 아니다. 최종 결과를 보고 재튜닝하지 않았다.

| 최종 진단 구성 | 정답 / 30 | 중요 오류 | 추론 p50 | 추론 p95 | NLL | Brier |
|---|---:|---:|---:|---:|---:|---:|
| Ollaya 기본 질문 | 12 | 6 | 139 ms | 205 ms | 1.465 | 0.760 |
| Ollaya 선택 질문 | 13 | 5 | 124 ms | 175 ms | 1.483 | 0.745 |
| Ollaya 선택 질문 + 보정 | 13 | 5 | 118 ms | 193 ms | 1.344 | 0.720 |
| 현재 SemIf | 27 | 0 | 1,061 ms | 1,580 ms | 1.222 | 0.684 |

모든 구성에서 최종 30개 요청은 오류·잘림·시간 초과 없이 관측되었고 2초 이내였다. NLL/Brier는 낮을수록 좋으며 여기서는 관측된 전체 30개에 대한 값이다. 샘플이 작으므로 작은 지연 차이를 개선으로 단정하지 않는다. 각 목적별 confusion과 모든 개별 확률은 [원시 결과](../evaluations/ollaya/run-20260928.json)에 있다.

대표적인 실패는 “검사하지 않은 파일이 있음”을 전체 검사 완료의 지지로 판단하거나, 한영 화면 중 영어 번역 누락을 전체 정상의 지지로 판단한 것이다. 금지된 파일 수정이나 기능 설명 부재도 일부 잘못 분류했다. 빠른 응답만으로 이 오류를 상쇄할 수 없다.

## 준비 시간·메모리·종료

PC는 Intel Core Ultra 7 258V, Intel Arc 140V, RAM 32 GB다. Ollaya는 CPU fp32, SemIf는 GPU INT8, 정적 1024 토큰 IR을 사용했다. 동시에 두 모델을 상주시킨 적은 없다. 시작 전 가용 메모리는 약 12.3 GiB였다.

| 관측 | Ollaya | SemIf |
|---|---:|---:|
| 최종 진단 모델 준비 | 2.39–2.63초 | 44.35초 |
| 별도 로드 후 프로세스 트리 working set | 1.38 GiB | 6.78 GiB |
| 고정 사례 3회 반복 | 117 / 114 / 114 ms | 1,095 / 1,072 / 1,124 ms |

준비 시간은 새 worker 프로세스 시작이며 운영체제 파일 캐시를 비운 cold boot가 아니다. SemIf는 기존 OpenVINO 캐시를 사용했다. 메모리는 별도 자원 검사에서 자식 프로세스와 콘솔 호스트를 포함한 working set 합계의 한 시점 샘플이다. 공유 페이지 중복을 제거한 시스템 비용이나 GPU 전용 메모리 peak가 아니다. 처음 시도한 비동기 샘플은 모델 해제 후여서 서버만 잡혔으므로 표에 사용하지 않았다.

- Ollaya `/v1/systemone`에 초과 입력을 보내 `422 STATE_TRUNCATED` 거절을 확인했다.
- `/v1`의 거절은 state 잘림만 보장한다. 공식 v0.7.3 입력 구성 소스와 다운로드한 tokenizer로 별도 감사해 이번 두 질문 후보의 지시·선택지가 모두 head 예산에 들어감을 확인했다. [질문 토큰 감사](../evaluations/ollaya/question-budget-audit.json). 다른 질문도 자동으로 안전하다는 뜻은 아니다.
- 명시적 모델 해제와 1초 유휴 설정 후 해제를 `/api/ps`로 확인했다.
- 모델 해제 후 daemon과 console host는 약 50 MiB였다. 실험 종료 시 소유 PID·실행 경로를 확인하고 daemon도 종료했다.
- SemIf `close()` 뒤 worker 프로세스 트리가 비었고, 마지막 전체 확인에서도 실험 모델 프로세스는 0개였다.
- 모델 off에는 분류 정확도를 부여하지 않는다. 이번 비교가 off 대비 코딩 효율을 검증한 것은 아니다.

## 저장소에 적용한 것

- [고정 사례](../evaluations/ollaya/cases.json), [질문 후보](../evaluations/ollaya/questions.json), [입력 hash](../evaluations/ollaya/frozen.json), [모델 lock](../evaluations/ollaya/model-lock.json).
- [선택 기록](../evaluations/ollaya/selection.json), [Modelfile](../evaluations/ollaya/Modelfile), [calibration](../evaluations/ollaya/calibration.json), [선택한 질문](../evaluations/ollaya/selected-questions.json).
- [독립 평가 도구](../scripts/evaluate_ollaya.py)와 [계산·분리·실패 집계 테스트](../tests/test_ollaya_evaluation.py), 한영 README 문서 링크.

제품 설정·작업 DB·MCP 연결·기존 모델은 유지했다. 기존 실행기를 제거하거나 Ollaya를 자동 시작하도록 등록하지 않았다. 모델 가중치·portable 실행 파일은 Git에서 제외된 `.local/ollaya-evaluation/`에 있으며 결과 재현용으로 남겼다.

측정 후 실행 도구에 완료·중단 단계의 재실행 방지, 선택 기록 일치 검사, 모델 이름·버전·registry hash 확인과 별도 자원 검사 단계를 보완했다. 입력·질문·선택 규칙·점수 계산은 바꾸지 않았다. Git의 Windows 줄바꿈 변환으로 고정 입력 hash가 달라지지 않게 두 입력 파일에만 변환 금지를 지정했다.

## 재현

공식 v0.7.3 `ollaya-windows-amd64.zip`의 SHA-256은 `239753359bf1164d297a6e80b537908feb01baadee2798784c76a4ea113eaba4`로 공식 `sha256sum.txt`와 일치했다. 모델 manifest hash는 `2840506e1f978aeb696a6f84af43ecc7fca95754cb63a67bafe6132532d384eb`다. 모든 blob의 실제 bytes와 길이를 manifest에 대조했다. Registry가 갱신되면 모델 이름이 같아도 도구는 이 실험과 다른 파일을 거절한다.

프로젝트의 Python 환경과 기존 SemIf 프로필이 필요하다. 공식 zip을 검증 후 `.local/ollaya-evaluation/runtime/`에 풀고, 사용하지 않는 포트에서 아래 환경으로 **별도 터미널**에서 실행한다. 시스템 전체 환경 변수나 PATH는 수정하지 않는다.

```powershell
$env:OLLAYA_HOST='127.0.0.1:11437'
$env:OLLAYA_MODELS=Join-Path (Get-Location) '.local/ollaya-evaluation/models'
$env:OLLAYA_DEVICE='cpu'
$env:OLLAYA_MAX_LOADED_MODELS='1'
& .local/ollaya-evaluation/runtime/bin/ollaya.exe serve
```

다른 터미널에도 같은 HOST·MODELS 환경 변수를 설정하고, 모델이 없다면 `ollaya.exe pull laya:multilingual`을 실행한다. 기존 결과를 덮어쓰지 않도록 새 출력 경로를 지정한다.

```powershell
.venv/Scripts/python.exe scripts/evaluate_ollaya.py develop --output .local/ollaya-evaluation/reproduction
.venv/Scripts/python.exe scripts/evaluate_ollaya.py validate --output .local/ollaya-evaluation/reproduction
.venv/Scripts/python.exe scripts/evaluate_ollaya.py contracts --output .local/ollaya-evaluation/reproduction
.venv/Scripts/python.exe scripts/evaluate_ollaya.py semif --output .local/ollaya-evaluation/reproduction
# resources는 위에서 직접 실행한 daemon PID를 --server-pid에 전달한다.
```

모델이 새 revision으로 갱신되어 lock과 다르면 같은 실험이라고 보고하지 말고 새 실험으로 설계해야 한다. 사람 검토 사례와 실제 작업 효율 평가를 준비하기 전까지 자동 추천 승격은 계속 차단한다.

## 검사 기록

추가 단위 테스트 10개와 최종 전체 검사 **Ruff lint·format, pytest 298 passed / 2 skipped**가 통과했다. 문서 링크와 고정 입력 hash도 확인했다.

첫 전체 실행은 기존 사용자 Temp의 권한 문제로 255개 setup 오류가 났다. 프로젝트 안으로 임시 경로를 바꾼 두 번째 실행은 297 passed / 2 skipped였고, 한 테스트의 깊은 경로가 Windows 파일 경로 제한에 걸렸다. 짧은 새 경로 `.t/oe1`로 바꾼 최종 실행이 위 결과다. 제품 코드나 테스트 합격 조건은 바꾸지 않았다.

```powershell
$env:PYTEST_ADDOPTS='--basetemp=.t/oe1 -p no:cacheprovider'
.venv/Scripts/python.exe -X utf8 scripts/check.py
```

재검사할 때는 다른 새 임시 경로를 쓴다. 테스트의 tmpdir 문제는 모델 정확도 측정과 별개이며, 실패 기록을 성공 기록으로 대체하지 않고 여기에 함께 남겼다.
