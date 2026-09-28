# 판단 모델 비교 진단

2026-09-23 · 한국어 원문 30개와 대응 영어 입력 30개를 로컬 CPU에서 평가하고 같은 날 Laya·OpenJev Modal과 비교
엔진 후보 여부를 판단하기 위한 단발 실행이며 프로젝트 코드·설정·profile은 변경하지 않은 범위

2026-09-28 후속 확인: 이 문서의 Python Laya 환경(torch 2.8.0, transformers 4.56.2)이 checkpoint의 새 rotary 설정을 일부 무시하는 호환성 문제가 발견됐다. 아래 Laya 수치는 당시 환경의 관측으로 보존하며, 올바른 설정을 검증한 Laya 모델의 대표 성능으로 사용하지 않는다. [진단·수정·동등성 검증](laya-finetuning-results.md#첫-실패와-수정). AgentJev·OpenJev 및 이후 별도 Ollaya 실행 결과와는 구분한다.

## 관찰 결과

같은 문항·질문 지시문·선택지·채점 기준에서 AgentJev가 Laya보다 정답 수가 적고 중요 문항 오답이 많은 결과
엔진 어댑터를 추가하지 않고 후보에서 제외, 가중치는 평가 후 삭제

| 모델·입력 | 관련성 | 주장 관계 | 도구 적합성 | 합계 | 중요 문항 오답 | p50 | p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OpenJev Modal·한국어 | 12/12 | 12/12 | 6/6 | 30/30 | 0 | 718ms | 987ms |
| Laya multilingual·한국어 | 6/12 | 8/12 | 3/6 | 17/30 | 2 | 196ms | 313ms |
| AgentJev·한국어 | 5/12 | 6/12 | 3/6 | 14/30 | 6 | 1,500ms | 3,521ms |
| Laya multilingual·영어 | 6/12 | 9/12 | 4/6 | 19/30 | 1 | 203ms | 281ms |
| AgentJev·영어 | 6/12 | 4/12 | 3/6 | 13/30 | 5 | 1,374ms | 3,167ms |

Laya 두 행은 같은 날 profile fingerprint `109866a5…`로 재실행한 값
OpenJev 행은 같은 날 운영 profile fingerprint `f4546e85…`(template `jev-context-v2-2`)의 작업별 Modal 세션에서 한국어만 실행한 값
OpenJev 지연은 Windows 클라이언트·로컬 연결·Modal Queue·토큰 검사·GPU를 포함한 왕복, 나머지 행은 로컬 CPU 추론만의 측정으로 직접 비교하지 않는 기준
OpenJev는 30문항 모두 observed, 최장 요청 1,006ms로 2초 제한 충족, 최고 확률이 가장 낮은 정답은 ko-06의 0.69
두 로컬 모델이 틀린 무관 후보 관련성과 반박 문항 ko-14·16·20·22·24를 모두 정답 처리
[이전 OpenJev Modal 진단](openjev-modal-evaluation.md)의 30/30은 다른 fingerprint·컨테이너 내부 측정 기록

### 오답 양상

관련성 12문항 중 11개를 relevant, 도구 적합성 6문항 모두를 fit으로 선택한 한쪽 선택지 편중
두 목적의 정답 상당수는 편중된 선택과 기대 답이 일치한 결과로 구별 능력의 근거가 아닌 수준
한국어 중요 오답 6개 중 반박 문항 4개를 partial, 1개를 supports로 분류
Laya가 맞힌 반박 문항 ko-14·16·20도 AgentJev는 partial로 분류
partial은 기존 선별에서 보호 대상으로 올라 근거는 유지되지만 주장이 틀렸다는 판정은 누락

부정문을 지지로 읽는 ko-22는 두 모델 모두 오답
지시문을 짧은 영어 질문으로 바꾼 별도 확인에서도 AgentJev의 ko-22 판정은 supports로 유지
질문 형식보다 모델 자체의 한계로 해석하되 1문항 확인이므로 일반화하지 않는 기준

### 지연

AgentJev의 p95가 기존 판단 제한 2초를 초과해 실사용 시 시간 초과가 잦을 조건
CPU float32와 선택지마다 긴 지시문 전체를 다시 계산하는 path 인코딩이 주된 원인으로 추정
공개 README의 60–70ms와 측정 장치·입력 길이·인코딩 방식이 달라 직접 비교하지 않는 기준

## 가중치 로드 확인

공개 README 예제에서 실패 테스트가 있는 상태를 false, 다음 행동을 실패 테스트 읽기로 선택
정답과 일치하는 판단으로 가중치 로드와 입력 조립 오류 가능성을 낮춘 확인
safetensors 텐서 343개를 `strict=True`로 로드해 누락·잉여 키 없음 확인

## 실행 범위

| 항목 | 값 |
| --- | --- |
| 가중치 | Hugging Face `aimeigaoshou/agent-jev` |
| 가중치 revision | `b3bf6b6dd443d6e724943b9194da31a4f055428e` |
| `model.safetensors` SHA-256 | `8166e46dc6019ae13f0d8fc97d603cdbcc2d20eb1882c7350c8deb9fc6bae215` |
| 코드 | GitHub `malevrigns/agent-jev` `b66a8cef7abdf66e6795eb229b55b387709f6ff7` |
| 실행 환경 | 기존 `.local/laya-venv`, torch 2.8.0+cpu, transformers 4.56.2 |
| 정밀도·스레드 | CPU float32, 4스레드 |
| 확률 보정 | 저장소의 `typed_decisions/temperatures.json` choice 온도 1.0353 |

Qwen3-0.6B 기반에 선택지 판단 head를 붙인 모델, 영어·중국어 문서만 제공되고 한국어 지원은 미명시
가중치는 2026-09-22 게시된 개인 저장소 자료이며 GitHub·Hugging Face 계정명이 다른 상태
upstream 서버의 `torch.load(weights_only=False)` 경로는 pickle 실행 위험으로 미사용
`agentjev/model.py`·`jev_service/contract.py`·`jev_service/prefix.py`만 import하고 safetensors state dict를 직접 로드
Qwen3 골격은 config만으로 생성해 별도 기반 가중치를 받지 않는 구성
`HF_HUB_OFFLINE=1`로 평가 중 네트워크 접근 차단

기존 서비스의 `question()`으로 지시문과 선택지를 생성해 Laya 평가와 동일한 과제 유지
각 문항을 AgentJev의 choice 질문 하나로 전달, 채점은 [평가 스크립트](../scripts/evaluate_model.py)와 같은 기대 답 일치 기준
지연은 인코딩부터 확률 계산까지의 단일 요청 측정이며 모델 준비 시간은 제외

문항은 agent-authored development fixture이며 사람 검토 heldout이 아닌 자료
AgentJev가 학습한 질문 형식과 다를 수 있어 이 결과만으로 모델의 일반 성능을 판정하지 않는 범위

## 재현

평가 후 가중치·upstream 코드·평가 스크립트를 모두 삭제, 재현하려면 다시 준비 필요
코드는 위 commit으로 clone, 가중치는 위 revision에서 `config.json`·tokenizer 파일·`model.safetensors`를 받은 뒤 SHA-256 대조
평가 스크립트는 위 실행 범위의 로드 방식과 질문 전달 방식에 따라 다시 작성
결과는 `.local/evaluations/agentjev-ko-development.json`·`agentjev-en-development.json`·`laya-en-development.json`에 보관
OpenJev 결과는 `.local/evaluations/openjev-modal-ko-development.json`에 보관
OpenJev는 기존 Modal 작업 ID로 `session-start`(ttl 600초·idle 90초) 후 준비 완료에서 30문항 실행, 직후 `session-stop`
[실행 앱](https://modal.com/apps/tttaliesin/main/ap-kQX0O3Jian2KzUqFp9CM5D)은 12:31:37–12:34:39 KST 동작, 종료 후 stopped·live container 0개 확인
해당 세션의 Modal 사용액은 미조회
