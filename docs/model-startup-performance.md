# 모델 시작 지연 조사

2026-09-26. **프로젝트 설정 적용 및 실제 MCP 수명 검증 완료, Desktop 재로딩 대기:** rank-3 상수 변환 후보가 별도 프로세스의 전체 캐시 적중에서 준비 **20.142초**와 30문항 추론 완료를 기록했다. 기준 237.160초와 비교하면 준비 시간이 약 91.5% 줄었다. 캐시를 새로 만드는 준비는 여전히 241.524초다. 프로젝트 프로필에는 검증한 파생 모델과 managed cache를 적용했고, 원래 프로필을 `profile-before.json`에 보존했다. oneDNN 전역 비활성화나 구현 강제 환경값은 사용하지 않는다. 현재 Desktop MCP 프로세스의 프로필 재로딩은 남아 있다. 아래 표는 `.local/startup-20260926/`의 실제 측정 결과다.

## 측정 대상과 시간의 의미

대상은 Qwen3.5-4B의 SemIf OpenVINO 정적 1024-token INT8 IR, Intel Arc 140V GPU, OpenVINO `2026.4.0-22959-99c81491cc3-releases/2026/4`이다. 기준 모델 manifest SHA-256은 `6cf4d2e57b84ee8127cc07cf790483d1cf566dea41ed61fb1980c7adc65a86ae`다.

[`measure_model_startup.py`](../scripts/measure_model_startup.py)는 manifest 검증, import, tokenizer, compile, 첫 warmup, 두 번째 warmup을 구분한다. `ready_seconds`는 두 warmup을 완료한 시점까지이며 `total_seconds`에는 이후 30문항 추론도 포함된다. 실패한 실행의 짧은 `total_seconds`나 compile 시간은 사용할 수 있는 모델의 준비 시간으로 해석하지 않는다.

기준 실행 `cache-create.json`은 빈 전체 캐시에서 **준비 237.160초, compile 228.281초**였다. compile이 준비 시간의 약 96.3%였다. 각 조건은 이 환경에서 수행한 개별 실행이며 반복 측정 평균이나 일반적인 하드웨어 성능 추정이 아니다.

## 실제 관측

| 원시 기록 | 조건 | compile | 준비 완료 | 결과 |
| --- | --- | ---: | ---: | --- |
| `cache-create.json` | 전체 캐시 최초 생성 | 228.281초 | 237.160초 | 30문항 완료 |
| `cache-hit-1.json` | 전체 캐시 적중 | 9.694초 | 실패 | 첫 warmup의 oneDNN primitive 실행 실패, 문항 0개 |
| `kernel-reuse.json` | 커널 캐시 재사용 시도 | 232.948초 | 242.291초 | 30문항 완료, 기준보다 빠르지 않음 |
| `cache-stream.json` | 전체 캐시 적중, mmap 끔 | 11.206초 | 실패 | 같은 oneDNN primitive 오류, 문항 0개 |
| `cache-size-create.json` | `OPTIMIZE_SIZE` 캐시 최초 생성 | 230.707초 | 239.892초 | 30문항 완료 |
| `cache-size-hit.json` | `OPTIMIZE_SIZE` 캐시 적중 | 10.206초 | 실패 | 같은 oneDNN primitive 오류, 문항 0개 |
| `opencl-create.json` | `OV_GPU_USE_ONEDNN=0` | 실패 | 실패 | GroupConvolution의 weight/input feature-map 불일치로 compile 실패 |
| `rank3-create.json` | rank-3 상수 변환 후보, 빈 managed 캐시 | 232.531초 | 241.524초 | 30문항 완료, cold 준비는 기준보다 빠르지 않음 |
| `rank3-hit.json` | 같은 후보·프로필, 별도 프로세스 캐시 적중 | 9.146초 | 20.142초 | 두 warmup과 30문항 완료 |

원래 IR의 전체 캐시·mmap·크기 최적화 실패 기록에는 `loaded_from_cache: true`가 있지만 준비 성공은 없다. oneDNN을 전역으로 끈 실행은 245.327초 후 실패했으며, 완료된 compile 시간이 기록되지 않았다. 이 결과는 원래 IR에서 해당 경로들이 사용할 수 있는 지연 개선책임을 뒷받침하지 않는다. rank-3 후보의 캐시 적중 성공은 별도 변환·manifest를 사용한 결과다.

## 판단 결과 보존 비교

[`compare_model_startup.py`](../scripts/compare_model_startup.py)는 저장된 JSON만 읽는다. 모델이나 OpenVINO를 시작하지 않는다. 성공한 실행끼리 30개 고유 ID, 선택, 입력 token 수, 확률의 옵션 집합과 유한성, 최대 절대 확률차를 검사하고 준비·compile·문항 시간도 함께 기록한다. 기본 확률 허용 오차는 **`1e-6`**다. 후보 결과를 본 뒤 이 값을 완화해 같은 판정으로 취급하지 않는다.

기준 `cache-create.json`과의 비교:

| 후보 | 선택 일치 | 입력 token 수 일치 | 최대 절대 확률차 | 준비 시간 변화 |
| --- | ---: | ---: | ---: | ---: |
| 커널 캐시 재사용 | 30/30 | 30/30 | 0 | +5.132초 |
| `OPTIMIZE_SIZE` 최초 생성 | 30/30 | 30/30 | 0 | +2.732초 |
| rank-3 후보 cold | 30/30 | 30/30 | 0 | +4.364초 |
| rank-3 후보 cache hit | 30/30 | 30/30 | 0 | −217.017초 |

여기서 0은 기록된 반환 확률값의 차이다. IR이나 모델 내부 연산 전체의 bitwise 동일성을 증명하지 않는다. 원래 IR의 네 실패 실행은 판단 결과가 없으므로 보존 비교도 실패하며 준비 시간 개선 비율을 계산하지 않는다. 상세 결과와 각 입력 JSON의 SHA-256은 `comparison-existing.json`, `comparison-rank3.json`에 있다. 실패 실행을 함께 검사한 기존 비교 명령의 종료 코드 1은 의도한 결과다.

후보의 cache hit 준비 시간은 기준 대비 **11.77배의 속도 비율, 약 91.5% 감소**를 기록했다. 이는 성공한 단일 실행끼리의 비교이며 반복 분포를 추정하지 않는다. 준비 후 문항 추론의 중앙값은 기준 0.808초, 후보 cache hit 0.864초였다. 따라서 이번 관측을 문항 추론 속도 개선으로 해석하지 않는다.

30문항은 [`korean-diagnostic.json`](../models/korean-diagnostic.json)의 진단 자료다. 동일 결과는 모델 정확도나 일반 코딩 성능 개선을 뜻하지 않는다. 초기 probe에는 `dataset_sha256`가 없어 ID·token 일치만으로 입력 원문 전체의 동일성을 입증할 수 없다. 후보 cold와 cache hit끼리는 dataset SHA-256이 같고 선택·token·반환 확률도 모두 같았다(`comparison-rank3-restart.json`). 이 추가 확인이 초기 기준 파일의 dataset hash 누락을 소급 보완하지는 않는다.

재현 명령 예시:

```powershell
.\.venv\Scripts\python.exe -B scripts/compare_model_startup.py `
  --baseline .local/startup-20260926/cache-create.json `
  --candidate .local/startup-20260926/kernel-reuse.json .local/startup-20260926/cache-size-create.json `
  --output .local/startup-20260926/comparison-successful.json
```

## rank-3 상수 변환과 적용

OpenVINO 2026.4의 oneDNN FullyConnected 구현에서 cold 생성 경로는 scale을 per-output-channel로 처리할 때 `ngroups == 1`과 `weight_rank <= 2`를 함께 검사한다. 캐시 load 경로는 같은 지점에서 `ngroups == 1`만 검사한다. zero point에도 rank·축 처리 차이가 있다. 원래 runtime graph에서 48개 대상이 oneDNN FullyConnected로 선택된 점과 함께 보면 **rank-3 descriptor 복원의 불일치가 유력한 소스상 후보**다. 실패한 primitive를 개별 특정하거나 OpenVINO 내부 패치로 인과관계를 확정한 것은 아니다. [공식 2026.4 FullyConnected 소스](https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/plugins/intel_gpu/src/graph/impls/onednn/fully_connected_onednn.cpp#L275)

같은 버전의 MatMul→FullyConnected 변환은 압축 가중치가 아닌 경우 weight의 batch dimension이 1이 아니면 변환을 거부한다. 이 조건에 따라 원래 dequantization 결과를 f16 Constant `[32,128,128]`로 구체화하고 기존 f32 Convert를 유지해 압축 가중치 패턴과 해당 FC 변환 경로를 피하는 후보를 만들었다. 이는 공식 변환 조건에서 도출한 회피 방식이며, 준비 성능과 출력 보존은 별도 실제 probe로 확인했다. [공식 변환 패스 소스](https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/plugins/intel_gpu/src/plugin/transformations/convert_matmul_to_fc.cpp#L172)

후보는 원래 INT8 IR의 rank-3 가중치 dequantization을 미리 계산해 f16 Constant로 저장한 별도 IR이다. `.local/models/qwen3.5-4b-ov1024b-int8-rank3-f16/materialization.json`과 독립 IR 대조에서 확인한 범위는 **48개 MatMul과 24개 Multiply가 공유하는 24개 영 tensor의 표현 변경**이다. 원래 f16 `Convert/Subtract/Multiply`를 OpenVINO `Model.evaluate`로 계산하고 이후 f32 Convert는 유지했다. 변경된 연결은 24개 f32 Convert의 입력이며, 나머지 공통 연산 속성은 동일했다. 24개 결과 tensor는 모두 양의 0이며 각각 1,048,576바이트다. 이는 기존 연산의 reference 결과를 확인한 값으로, 임의로 모델 가중치를 0으로 바꾼 실험이 아니다.

보고서는 재저장·재로딩 후 reference와의 비트 일치(`serialized_reference_bits_verified: true`)를 기록한다. 이 확인은 변환한 상수의 값에 한정된다. 전체 GPU 모델의 출력 동등성은 변환 당시 보고서에서 `not_tested`로 분리되어 있으며, 이후 30문항 대조는 위의 probe 기록으로 확인했다. 원본 XML·BIN hash는 변환 전후 그대로였고, 별도 후보의 `model.bin`은 4,211,215,577바이트에서 4,211,727,577바이트로 **512,000바이트 증가**했다. 변환한 상수의 논리적 크기 합계 25,165,824바이트를 파일 증가량으로 취급하지 않는다. 후보 manifest SHA-256은 `f2e933ad25ad2eb20b22b4efe9c04f5f61ce927dd6982f1aaecde94ba513cade`다.

2026-09-26 03:15:35 UTC에 시작한 별도 GPU cold probe는 `rank3-create.json`에서 `passed: true`, 30문항 완료를 기록했다. 이어 03:20:19 UTC에 시작한 별도 프로세스가 같은 managed cache를 읽고 첫·두 번째 warmup과 30문항을 모두 완료했다. 두 실행 모두 oneDNN을 전역 비활성화하거나 구현 강제 환경값을 쓰지 않았다. 프로젝트 프로필 적용 후 실제 MCP 두 연결이 약 20초의 캐시 준비와 `observed` 판단에 성공했다. 유휴 종료 후 재시작 검증은 아래 상태표에 기록한다.

| 남은 확인 | 현재 상태 |
| --- | --- |
| 후보 변환 내용·manifest·상수 reference/reload 검증 | 기록 확인, 전체 모델 동등성과 구분 |
| 캐시 miss에서 첫·두 번째 warmup 및 30문항 완료 | 성공, 준비 241.524초 |
| 별도 프로세스 cache hit에서 첫·두 번째 warmup 성공 | 성공, 준비 20.142초 |
| 기준과 30개 선택·token·확률 비교 | 30/30 일치, 최대 절대 확률차 0, 허용 오차 `1e-6` 유지 |
| 동일 프로필 shared demand → ready 및 startup 메타 | 첫 준비: worker 내부 19.687초, 상태 조회로 20.500초에 준비 관측; cache hit |
| 공식 MCP 두 개의 observed 판단, 하나 종료 후 다른 쪽 유지 | 실제 판단 3회 성공; 마지막은 첫 MCP 종료 후 성공 |
| 상태 조회만으로 idle 해제 → 다음 실제 요청으로 새 worker 준비 | 첫 유휴 해제 121.859초, 다른 worker의 cache hit 준비 18.578초(조회상 20.563초), 두 연결 및 한 연결 종료 후 판단 성공; 두 번째 유휴 해제 122.016초까지 성공 |
| 설치 프로필 적용 | 검증된 파생 모델과 managed cache로 적용, 기존 프로필 백업 보존 |
| 현재 Desktop MCP 프로필 재로딩 및 native required 판단 | 재시작 후 확인 필요; 별도 SDK 검증과 구분 |

마지막 수명 경로는 [`validate_model_lifecycle.py`](../scripts/validate_model_lifecycle.py)의 `--repeat-after-idle`로 검증할 수 있다. `--require-cache-hit`을 함께 지정하면 반복 준비의 `startup.loaded_from_cache`가 true여야 한다. 이 검증기의 가짜 모델 통과와 실제 후보 모델의 통과는 구분한다. 기본 유휴 해제를 제거하거나 모델을 계속 상주시켜 준비 시간을 숨기는 방식은 이번 개선 결과로 세지 않는다.

## 검사 중 발견한 Windows 긴 경로 처리

전체 검사 중 캐시 복구 2개가 격리된 파일을 찾지 못했다. 실제 격리와 재시도는 성공했으나, 격리 디렉터리에 기존 64자리 key와 UUID를 함께 붙여 내부 파일 경로가 264자가 됐다. 일반 `Path.exists()`는 false, 같은 경로의 Windows 확장 경로 조회는 true·64바이트를 반환했다. 같은 테스트를 짧은 임시 경로에서 실행하면 2개 모두 통과했다.

이를 단순 검사 경로 변경으로만 회피하지 않도록 격리 디렉터리 이름을 `rejected-<uuid>`로 줄였다. 격리 후 경로는 기존 64자리 key 디렉터리보다 짧아지며, 원래 캐시 identity는 내부 `identity.json`에 보존한다. 더 긴 임시 경로 `.t/startup-long-regression`에서 해당 복구 검사 2개가 통과했다. 최종 전체 검사는 Ruff lint·format과 **199 passed, 1 skipped**로 통과했다. 제외 1개는 Windows symlink 권한 조건이다. 소스·검증기·적용 프로필 대상 SHA-256은 `4790ea2b50009abb7633c9d43b46d2441f24938fd553c20079b3c8909f2057e5`이며 `target.json`, `checks-v3.log`에 기록했다. 이전 revision의 명시적 제외 기준 때문에 새 revision을 영구히 완료하지 못하던 기록 처리도 수정하고 회귀 검사했다. 사유 없는 제외나 이전 revision의 통과·실패 결과는 새 revision 완료 근거가 되지 않는다.

최종 별도 SDK 검증은 `live-report.json`의 `passed: true`로 완료됐다. 새 요청의 모델 준비는 내부 계측 19.687초와 18.578초, 상태 조회로는 20.500초와 20.563초였다. 모든 판단은 observed였고 유휴 해제는 121.859초와 122.016초에 관측됐다. 설정은 120초이며 상태 조회 간격을 포함한 값이다. 두 주기에서 다른 모델 worker PID를 확인했고, 미사용 MCP의 worker 0개와 한 MCP 종료 후 남은 MCP의 판단도 확인했다. 검증용 persistent 실행기는 이후 종료했으며 12:27:57 KST OS 대조에서 모델·실행기·해당 launcher가 모두 0개였다(`active-processes.json`, `idle-processes.json`). 이 결과와 현재 Desktop native 연결의 검증은 구분한다.

## 적용 범위와 재로딩

현재 `.local/semif-ov-profile.json`은 검증된 파생 모델과 `.local/model-cache/semif-openvino`를 사용한다. 원본 모델·tokenizer·원문 저장소·작업 이력은 유지하며 모델 판단은 shadow다. 기존 프로필 백업은 `.local/startup-20260926/profile-before.json`이다. 캐시가 없거나 모델·OpenVINO·기기·OV_* 환경값이 바뀌어 새 캐시가 필요한 경우 최초 생성에 약 4분이 다시 필요할 수 있다. 이후 같은 캐시를 쓰는 재준비가 이번 개선 대상이다.

현재 Desktop의 native `workspace_status`가 반환한 fingerprint는 이전 `02938dc98ca6186fcd908c823d84345ebb1429855ef9634d128879fcfad0e90f`였다. 새 프로필로 실행한 별도 SDK 검증 fingerprint는 `788e0196d66ae2b7d13476284adb6d1f3dd0afd5c24eb56febe651f4ece9cad9`다. 따라서 현재 Desktop 연결이 새 프로필을 읽었다고 보고하지 않는다. Desktop 재시작 후 native `workspace_status`의 새 fingerprint와 required 판단 성공을 확인해야 한다. 읽기 도구나 전체 앱 재시작으로 모델이 자동 상주하는 설정은 추가하지 않았다.
