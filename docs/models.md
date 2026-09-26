# 선택형 모델과 실행 위치

기본 설정 `engine.state = "disabled"`로 기억·검색만 사용
목적별 active 승격 검사는 구현되어 있으나 사람이 검토한 heldout 품질·효율 기준을 통과한 프로필은 없는 상태
관찰 결과도 원문 선택·승인·완료 판정을 변경하지 않는 구조

## 구현된 경계

현재 사용 경로는 [작업별 OpenJev Modal 세션](openjev-session.md)의 `openjev_modal` 어댑터
Windows에서 실제 MCP 문맥 요청과 원격 GPU 판단 연결, 같은 vLLM renderer를 통한 토큰 검사와 세션 종료 검증 완료
GPU 준비는 별도 명령이며 준비되지 않았거나 종료된 세션에서는 근거를 보존하고 판단 보류

OpenJev 어댑터는 설치 프로필의 고정 loopback HTTP 주소만 사용
프록시 환경 변수·redirect·요청에서 지정한 주소·유료 API로 전환하지 않는 구성
`/v1/models` 준비 확인과 `/v1/systemone` choice 변환, 질문·라벨·분포·NaN·confidence·크기 검사 포함
정확한 packed-input tokenizer preflight가 제공되지 않으면 추론을 보내지 않고 `input_incomplete` 반환
기존 `openjev` family의 직접 loopback CLI 경로는 준비 확인까지만 가능하며 정확한 token counter 주입이 별도 필요
라이브러리 어댑터의 token counter 주입은 엔진 포장과 일치하는 검증된 구현에만 사용

Laya 어댑터는 별도 Python 환경과 이미 존재하는 완전한 모델 디렉터리 필요
전용 worker가 로컬 모델을 한 번 적재하고 제한된 JSON 표준 입출력으로 질의 처리
worker가 프로필 OS 잠금을 소유하므로 호스트 종료 중에도 살아 있는 worker의 중복 적재 차단
모델·tokenizer·encoder 파일이 없으면 준비 실패, Hugging Face offline 환경으로 자동 다운로드 차단
공식 SDK의 질문·선택지·원문 잘림 전에 실제 tokenizer 길이 검사
device 자동 전환이 감지되면 결과를 유보하고 프로필 재검토 필요

실제 Laya 가중치·전용 CPU 환경과 [실행 검증](implementation-v2.md)은 설치·실행 완료
Laya 공식 SDK는 로드 시 tokenizer 설정 호환성 보정을 할 수 있으므로 검토한 모델 사본과 고정 SDK revision 사용

## 프로필 준비

공통 JSON 필수 필드는 다음과 같으며 실제 검토한 값을 지정

```json
{
  "family": "laya",
  "model_revision": "replace-with-verified-model-commit",
  "implementation_revision": "573e5b62696ba441230cd6be71d593331b5d23af",
  "precision": "float32",
  "template_revision": "ko-v1",
  "sampling": {},
  "score_definition": "Laya choice entropy confidence; not correctness or permission",
  "python": "C:/PreparedLaya/python.exe",
  "model_path": "C:/PreparedLaya/model",
  "lock_root": "C:/PreparedLaya/locks",
  "device": "cpu"
}
```

예시의 경로·model revision은 준비한 실제 값으로 교체하고 모든 프로젝트가 같은 사용자별 `lock_root` 사용
OpenJev는 family `openjev`와 `endpoint`를 사용하며 `model_revision`은 서버가 지원하는 고정 API 모델 ID
실제 가중치·서버 실행체 revision은 프로필의 별도 필드로 추가해 fingerprint에 포함
어댑터는 프로필 선언이 실제 설치본과 일치하는지 독립 증명하지 않으므로 실행 전 확인 필요

프로젝트 설정에서 profile 파일과 관찰 모드 지정

```toml
[engine]
state = "shadow"
profile_file = "C:/PreparedLaya/profile.json"
```

Laya와 직접 loopback OpenJev 준비는 서버 시작 시 명시적으로 수행하고, Modal 준비는 별도 `session-start` 명령 사용

```powershell
.\.venv\Scripts\python.exe -m jev_context serve --config .local\project.toml --prepare-engine
```

판정 시간 초과 시 검색 결과 유지, 같은 요청에서 자동 재시도 없음
OpenJev의 서버 추론 종료 여부는 독립 확인 후 다시 준비
Laya는 worker 종료·정리 후 명시적으로 다시 준비

## 실제 근거와 검증 한계

공개 코드 확인은 [OpenJev e04794a](https://github.com/razorback16/openjev/tree/e04794ab36e4f7e6040c2547baecdb2737ce2e79)와 [Laya 573e5b6](https://github.com/NandhaKishorM/laya/tree/573e5b62696ba441230cd6be71d593331b5d23af) 기준
OpenJev choice confidence 정의는 `1 − H(p)/ln(K)`이며 정답 확률로 사용하지 않는 기준
모델 우열·한국어 정확도·RAM·GPU 요구 충족은 이 구현의 단위 테스트로 증명하지 않는 범위
모의 loopback 서버의 통신·응답 검증과 실제 모델 평가를 [검증 기록](verification.md)에서 분리
