# Ollaya 조사: 모델 실행기 대체 가능성과 Jev Context의 역할

조사일: 2026-09-28 KST. 공식 사이트·릴리스·API 계약·공개 소스와 로컬 Jev Context `63c6777`을 대조했다. 확인한 최신 릴리스는 **v0.7.3 (2026-09-27)**이다. 아래 소스 링크의 `main`은 조사 시점 자료이며, 릴리스 태그의 모든 파일과 일치한다고 검증한 것은 아니다. 설치·모델 다운로드·추론·성능 측정은 실행하지 않았다.

## 결론

Ollaya는 Jev Context의 **모델 다운로드·추론·프로세스 수명 관리 부분을 대체할 유력 후보**다. 작업 기억·원문 revision·제약 보존을 포함한 프로젝트 전체를 대신하는 제품은 아니다. 자체 모델 실행기를 계속 확장하기 전에 Ollaya와 같은 외부 실행기를 연결하는 방향을 검증할 가치가 있다. 다만 현재 Intel Arc 환경의 성능, 한국어 판단 품질, 기존 판단 계약과의 호환성은 미확인이다.

Ollaya 문서에서 말하는 “Jev”는 TypeSafe의 판단 모델이다. 이 저장소의 Jev Context와는 다른 제품이다. Ollaya도 Ollama·TypeSafe와 독립적인 프로젝트임을 명시한다. [공식 소개](https://ollaya.dev/)

## 어떤 제품인가

텍스트·JSON 상태와 정해진 질문을 받아 선택·점수·참/거짓 계열 판단을 반환하는 로컬 실행기다. 지원 목록에는 Laya, NLI, GLiClass, decider, Kev, Winnow 등이 있다. CLI·로컬 HTTP API·MCP·데스크톱 앱을 제공한다. 모델을 이름으로 내려받고, 실행 상태를 보고, 메모리에서 내리는 사용자 흐름이 이미 갖춰져 있다. 런타임은 Apache-2.0이며 모델 라이선스는 별도다. [저장소](https://github.com/ollaya-dev/ollaya)

구조는 Rust CLI/daemon → 모델별 runner 프로세스 → 추론 backend다. scheduler는 사용 중인 runner를 lease로 보호하고 마지막 요청이 끝나면 유휴 해제 시간을 계산한다. 로딩을 직렬화하고 슬롯이 부족하면 유휴 모델을 퇴거시킨다. 이는 우리 공유 broker의 목적과 겹친다. [scheduler 소스](https://github.com/ollaya-dev/ollaya/blob/main/crates/ollaya-server/src/scheduler.rs)

데스크톱은 **Tauri 2 + Rust + TypeScript**다. CLI 엔진을 sidecar로 묶고 별도 Cargo workspace를 사용한다. 웹뷰 의존성이 엔진 빌드에 들어가지 않게 분리한 점은 참고할 만하다. 이 사실만으로 현재 Electron UI를 다시 만들 이유가 되지는 않는다. [배포 구조](https://github.com/ollaya-dev/ollaya/blob/main/docs/distribution.md#desktop-app)

## 우리 프로젝트와의 경계

| 기능 | Ollaya | Jev Context 판단 |
|---|---|---|
| 모델 목록·다운로드·로딩·해제 | 제품의 중심 기능 | 중복 구현을 줄일 후보 |
| 여러 호출자가 모델 실행기 공유 | daemon과 모델별 runner | 자체 broker와 비교할 부분 |
| 에이전트에 판단 도구 제공 | MCP `decide`, `list_models`, `show_model`, `pull_model` | 외부 판단 경로로 사용할 수 있음 |
| 작업 목표·결정·제약 복원 | 확인한 MCP/API에는 해당 저장 계약 없음 | 현재 서비스가 담당 |
| 등록 원문 revision·삭제·출처 보존 | 확인한 계약에 없음 | 현재 서비스가 담당 |
| 한국어 제약 판단의 실제 효과 | 다국어 모델 지원만으로 입증되지 않음 | 별도 비교 평가 필요 |

MCP의 `decide`는 첫 사용에 모델 다운로드도 할 수 있고, MCP 명령은 필요하면 daemon을 시작한다. 우리 서비스의 “조회·복원만으로 모델을 준비하지 않는다”는 정책과 구분해야 한다. 직접 통합할 때는 모델을 명시적으로 준비하고 판단 호출에만 사용해야 한다. [공식 MCP 문서](https://ollaya.cobanov.dev/docs/agents)

## 현재 PC에서 중요한 제한

현재 프로젝트는 Windows의 Intel Arc GPU에서 SemIf OpenVINO를 사용한다. Ollaya 공식 안내는 Intel·AMD GPU에 CPU 실행을 안내한다. Windows 배포물에 DirectML DLL이 포함되어도 provider가 활성화됐다는 뜻은 아니며, 배포 문서는 비활성이라고 명시한다. Windows 데스크톱 번들도 CPU 구성이고, NVIDIA 가속 CLI 배포와 구별된다. 따라서 Ollaya를 설치하면 지금 Intel GPU 경로가 그대로 대체된다고 볼 수 없다. [플랫폼 안내](https://ollaya.dev/) · [Windows 배포 명세](https://github.com/ollaya-dev/ollaya/blob/main/docs/distribution.md#windows) · [현재 모델](models.md)

v0.7.3은 CUDA 12 드라이버용 패키지와 Laya CPU 그래프 최적화를 발표했다. 저자의 동일 입력 비교에서는 CPU 지연이 줄고 기준 구현과 판단 일치를 유지했다고 보고한다. 이는 **저자 측 관측**이며 현재 PC에서 재현한 결과가 아니다. 초기 검색에 남은 “Windows는 WSL만 지원” 문구는 최신 릴리스와 맞지 않아 채택하지 않았다. [v0.7.3 릴리스](https://github.com/ollaya-dev/ollaya/releases/tag/v0.7.3)

사이트의 RTX 4090 지연·typed-decisions 점수를 우리 노트북 또는 한국어 작업 기억 품질로 환산할 수 없다. 특히 ONNX 변환 전후의 판단 일치는 원래 모델의 정답률을 뜻하지 않는다. [공식 성능 주장](https://ollaya.dev/) · [변환 일치 보고](https://github.com/ollaya-dev/ollaya/releases/tag/v0.7.3)

## 주소만 바꾸면 연결되는가

**현재 코드에서는 아니다.** [OpenJev 어댑터](../src/jev_context/engines.py)는 `/v1/models`와 `/v1/systemone`을 사용하므로 HTTP 형태는 가깝다. 그러나 다음은 별도 처리해야 한다.

1. **입력 길이 사전 검사:** `OpenJev.evaluate()`는 정확한 `token_counter`가 없으면 `input_incomplete`로 거절한다. 현재 [CLI](../src/jev_context/cli.py)의 준비 경로는 이 counter를 주입하지 않는다. endpoint 변경만으로 판단이 시작되지 않는다.
2. **점수 의미:** 현재 SemIf confidence는 `1 − H(p)/ln(K)`다. Ollaya의 TypeSafe confidence는 `(K·p_max − 1)/(K − 1)`이다. 응답 모양이 같아도 기존 임계값·평가 보고서를 재사용해서는 안 된다.
3. **잘린 근거:** Ollaya `/v1/*`는 문맥 초과 시 `422 STATE_TRUNCATED`로 거절하지만 `/api/decide`는 `state_truncated`를 포함해 응답할 수 있다. 어떤 경로든 잘린 근거를 완전한 판단으로 채택하지 않도록 매핑해야 한다.
4. **모델 신원:** 우리 `model_revision`은 재현용 revision 정보이기도 하다. Ollaya 요청의 모델 이름과 실제 manifest/checkpoint 식별을 별도로 연결해야 한다.

위 2~3의 근거는 [API 계약](https://github.com/ollaya-dev/ollaya/blob/main/docs/api.md#5-questions-and-validation)이다. 1·4는 우리 소스와 계약을 대조한 통합 판단이다.

기존 준비·종료 UI까지 유지하려면 Ollaya의 native 수명 관리 API를 연결해야 한다. 외부 daemon을 쓸 때 자체 broker로 같은 모델을 또 실행하면 중복 관리가 생기므로, 실행 주체는 하나로 정해야 한다. Ollaya의 요청 취소 시에도 진행 중인 forward pass가 즉시 중단된다고 가정하지 않는다. [API 수명·취소 계약](https://github.com/ollaya-dev/ollaya/blob/main/docs/api.md#10-concurrency-queueing-timeouts-and-cancellation)

## 적용 전 검증 순서 — 아직 실행하지 않음

1. 현재 서비스는 유지하고 Ollaya 버전·모델 digest를 고정한 격리 시험을 만든다. Intel PC의 CPU에서 `laya:multilingual`을 첫 후보로 두고, 영어 전용 모델과 자동 언어 router는 첫 비교에서 제외한다.
2. 명시적으로 모델을 준비하고 최대 로드 모델은 1개로 제한한다. cold/warm 시작 시간, 메모리, 질문 수별 지연, 유휴 해제·앱 종료 후 상태를 측정한다.
3. 한국어 제약·예외·반박·근거 부족·초과 입력 사례를 고정해 현재 SemIf 및 모델 off와 비교한다. 판단 품질과 전체 코딩 작업 효율을 분리해 측정한다.
4. 입력 예산, 점수 정의, 실제 모델 신원, 오류·취소를 다루는 어댑터를 먼저 검사한다. 통과 전에는 shadow만 사용한다.
5. 이득이 확인되면 실행기와 모델 배포를 Ollaya에 맡기고, 우리 코드는 작업 기억·출처·검증에 집중한다. Intel 성능이나 한국어 품질이 부족하면 현재 OpenVINO 경로를 유지한다.

이 문서는 제품 선정·연결 가능성 조사다. Ollaya의 설치 성공, 현재 PC 성능, 한국어 품질 개선, 기존 실행기의 교체 완료를 주장하지 않는다.
