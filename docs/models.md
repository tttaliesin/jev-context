# 판단 모델과 실행 위치

모델은 선택 사항입니다. 기본 설정 `engine.state = "disabled"`에서는 기억·검색만 사용하고, 모델 판단은 `skipped`로 표시됩니다.

## 엔진

| family | 실행 위치 | 상태 |
|---|---|---|
| `semif_openvino` | 로컬 GPU (OpenVINO) | **현재 이 프로젝트의 구성.** Qwen3.5-4B INT8, 정적 1024 token IR |
| `laya` | 로컬 CPU | 실제 가중치로 실행 검증. 현재 미사용 |
| `openjev_modal` | Modal 원격 GPU | 작업별 세션으로 실제 문맥 연결 검증. 이전 실험 구성 |
| `openjev` | 직접 loopback HTTP | 준비 확인까지만 가능 |

## 판단 상태

| `engine.state` | 동작 |
|---|---|
| `disabled` | 모델을 쓰지 않음 |
| `shadow` | 판단을 요청하고 결과를 기록하지만 근거 선택·승인·완료 판정은 바꾸지 않음 |
| `active` | 평가를 통과한 목적(purpose)만 근거 선택에 반영 |

목적별 active 승격은 `engine.evaluation_file`의 평가 보고서와 그 SHA-256으로 검사합니다. 보고서의 프로필 fingerprint가 현재 엔진과 같고, 사람이 검토한(`human_reviewed`) heldout 문항이 30개 이상이며, 중대 회귀 0건과 품질·효율 기준을 모두 통과해야 합니다. **이 기준을 통과한 프로필은 아직 없어서** 모든 엔진은 shadow로 운영합니다.

## SemIf OpenVINO (현재 구성)

[SemIf](https://github.com/TheoLeeCJ/SemIf)(MIT, 23cf1f39)의 직접 선택지 판독 방식을 구현합니다. 근거, 판단 기준, 문자로 표시한 선택지를 한 번의 대화 입력으로 넣고, forward pass 한 번으로 다음 토큰 중 선택지 문자들의 logit만 읽습니다. 문장을 생성하지 않습니다.

- **confidence**는 선택지 분포의 `1 − 정규화 엔트로피`입니다. 정답 확률이 아닙니다.
- 선택지 판독은 순서에 민감하므로, 선택지 순서와 근거의 키 순서를 바꾸지 않고 worker에 전달합니다.
- 입력이 IR의 고정 길이(1024 token)를 넘으면 잘라내지 않고 판단을 거절합니다.
- 질문 하나마다 forward pass가 한 번씩 필요합니다. 아직 반영하지 않는 목적은 프로필의 `omit_shadow_purposes`로 건너뛸 수 있고, 현재 구성은 `presentation`을 건너뜁니다. active로 승격된 목적은 항상 묻습니다.

**준비 시간.** OpenVINO GPU 컴파일에 캐시가 없으면 약 4분(237초), 캐시가 맞으면 약 20초가 걸립니다. 컴파일에 실패한 캐시는 격리한 뒤 새 캐시로 한 번만 다시 시도합니다. 근거는 [모델 시작 지연 조사](model-startup-performance.md)에 있습니다.

**환경.** 전용 Python 환경을 [semif-requirements.txt](../models/semif-requirements.txt)로 만듭니다 ([개발 환경](development.md#모델-환경)). 모델 가중치 다운로드와 OpenVINO IR 변환은 별도 단계입니다. 변환 도구 중 저장소에는 [prepare_rank3_constants.py](../scripts/prepare_rank3_constants.py)만 있고, 원 변환 스크립트는 이 PC의 `.local`에만 있습니다.

## 모델 수명

로컬 엔진(`semif_openvino`, `laya`)은 여러 MCP 연결이 실행기 하나를 공유합니다. 설계는 [여러 MCP 연결에서의 모델 수명](model-lifecycle-design.md), 검증은 [적용 검증](model-lifecycle-validation.md)에 있습니다.

- MCP 연결, 도구 목록, 상태 조회, 작업 복원, `judge_mode=off`는 모델을 시작하지 않습니다.
- 실제 판단 요청이 처음 오면 준비를 시작하고, 준비되는 동안은 `engine_preparing`으로 판단을 보류합니다.
- 마지막 판단 뒤 `idle_timeout_seconds`(기본 120초) 동안 요청이 없으면 모델을 해제합니다. 상태 조회는 이 시간을 늘리지 않습니다.
- 한 연결이 끊겨도 다른 연결이 쓰는 모델은 종료되지 않습니다.

데스크톱 관리 앱에서 모델을 직접 준비하거나 종료할 수 있습니다. 실행기를 명시적으로 띄워 둘 때는 다음 명령을 씁니다.

```powershell
.venv\Scripts\python.exe -m jev_context.shared_engine --profile .local\semif-ov-profile.json --start --persistent
.venv\Scripts\python.exe -m jev_context.shared_engine --profile .local\semif-ov-profile.json --status
.venv\Scripts\python.exe -m jev_context.shared_engine --profile .local\semif-ov-profile.json --stop
```

## 프로필

프로필 JSON이 엔진과 모델을 정의합니다. 아래는 SemIf 예시이며, 경로와 revision은 실제로 준비한 값으로 바꿉니다.

```json
{
  "family": "semif_openvino",
  "model_revision": "Qwen/Qwen3.5-4B@<commit>",
  "implementation_revision": "SemIf@23cf1f39...; openvino 2026.4.0; nncf 3.4.0",
  "precision": "int8_asym weights, OpenVINO GPU, static 1024 tokens",
  "template_revision": "jev-context-v2-2",
  "sampling": "deterministic",
  "score_definition": "choice distribution; confidence = 1 - normalized Shannon entropy, not correctness probability",
  "python": "C:/Prepared/semif-venv/Scripts/python.exe",
  "model_path": "C:/Prepared/models/qwen3.5-4b-ov1024-int8",
  "manifest_sha256": "<model manifest.json SHA-256>",
  "lock_root": "C:/Prepared/model-locks/semif-openvino",
  "cache_dir": "C:/Prepared/model-cache/semif-openvino",
  "device": "GPU",
  "warmup": true,
  "prepare_timeout_seconds": 300,
  "omit_shadow_purposes": ["presentation"]
}
```

| 필드 | 의미 |
|---|---|
| `family`, `model_revision`, `implementation_revision`, `precision`, `template_revision`, `sampling`, `score_definition` | 공통 식별 정보. fingerprint에 포함되어 평가 보고서와 대조됨 |
| `python`, `model_path` | 전용 Python과 모델 디렉터리. 둘 다 절대 경로여야 함 |
| `manifest_sha256` | 모델 디렉터리의 `manifest.json` 해시. 준비할 때 모든 파일 해시를 확인 |
| `lock_root` | 같은 모델의 중복 적재를 막는 잠금 위치. 모든 프로젝트가 같은 사용자별 경로를 써야 함 |
| `cache_dir` | OpenVINO 컴파일 캐시 |
| `warmup` | 준비 중 warmup 실행. 질문당 소요 시간을 측정해 기한 안에 못 끝날 질문을 건너뜀 |
| `prepare_timeout_seconds`, `idle_timeout_seconds` | 준비 제한 시간, 유휴 해제 시간 |

어댑터는 프로필 선언이 실제 설치본과 같은지 스스로 증명하지 않습니다. `manifest_sha256`이 모델 파일 변경은 잡아내지만, 실행 전에 프로필 내용을 확인해야 합니다.

프로젝트 설정에서 프로필과 판단 상태를 지정합니다. 상대 경로는 설정 파일 위치를 기준으로 해석합니다.

```toml
[engine]
state = "shadow"
profile_file = "semif-ov-profile.json"
```

`jev-context codex-config`는 판단 상태가 `shadow`나 `active`이면 서버 명령에 `--prepare-engine`을 붙입니다. 로컬 엔진에서는 공유 실행기 연결만 만들고 모델을 바로 올리지는 않습니다.

## Laya

별도 Python 환경([laya-requirements.txt](../models/laya-requirements.txt))과 완전한 모델 디렉터리가 필요합니다. SemIf와 같은 worker 방식으로, 전용 worker가 모델을 한 번 적재하고 JSON 표준 입출력으로 질의를 처리합니다.

- 모델·tokenizer·encoder 파일이 없으면 준비에 실패합니다. Hugging Face offline 설정으로 자동 다운로드를 막습니다.
- 공식 SDK가 질문·선택지·원문을 자르기 전에 실제 tokenizer 길이를 검사합니다.
- device 자동 전환이 감지되면 결과를 보류하고 프로필을 다시 검토해야 합니다.
- 공식 SDK는 로드할 때 tokenizer 설정을 보정할 수 있으므로, 검토한 모델 사본과 고정한 SDK revision을 씁니다.

실제 Laya 가중치와 CPU 환경의 실행 결과는 [구현 0.2.0과 검증 범위](implementation-v2.md)에 있습니다.

## OpenJev

**Modal (`openjev_modal`).** [작업별 Modal 세션](openjev-session.md)에서 원격 GPU로 판단합니다. GPU 세션은 MCP 요청으로 만들어지지 않고, 명시적인 `jev-context session-start --config <설정> --work-id <작업 ID>`로만 시작합니다. 상태는 `session-status`, 종료는 `session-stop`입니다. 세션이 없거나 종료됐으면 근거를 보존하고 판단을 보류합니다. 같은 vLLM renderer로 토큰 수를 미리 검사하며, 고정 의존성은 [modal-requirements.txt](../models/modal-requirements.txt)입니다.

**직접 loopback (`openjev`).** 프로필의 고정 loopback 주소(`endpoint`)만 사용하고, 프록시 환경 변수·redirect·요청에서 지정한 주소·유료 API로 바꾸지 않습니다. `/v1/models`로 준비를 확인하고 `/v1/systemone` 응답의 선택지·분포·NaN·confidence·크기를 검사합니다. 정확한 packed-input tokenizer 검사가 없으면 추론을 보내지 않고 `input_incomplete`를 반환하므로, CLI 경로는 현재 준비 확인까지만 가능합니다.

판단이 시간 안에 끝나지 않으면 검색 결과는 유지하고, 같은 요청에서 자동으로 다시 시도하지 않습니다.

## 검증 한계

- 공개 코드 확인 기준은 [SemIf 23cf1f39](https://github.com/TheoLeeCJ/SemIf), [OpenJev e04794a](https://github.com/razorback16/openjev/tree/e04794ab36e4f7e6040c2547baecdb2737ce2e79), [Laya 573e5b6](https://github.com/NandhaKishorM/laya/tree/573e5b62696ba441230cd6be71d593331b5d23af)입니다.
- 모델 우열, 한국어 정확도, RAM·GPU 요구 충족은 이 구현의 단위 테스트로 증명하지 않습니다. 어댑터 테스트는 모의 worker와 모의 서버로 통신 계약만 검사합니다.
- SemIf 구성의 사람 검토 heldout 품질 평가와 실제 코딩 작업 효율 측정은 아직 없습니다. 준비 시간 측정의 30문항 비교는 설정 변경 전후의 **답 일치** 확인이며 정확도가 아닙니다.
