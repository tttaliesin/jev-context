# OpenJev Modal 진단

2026-09-22 · 한국어 원문 30개와 대응 영어 입력 30개를 Modal GPU에서 평가하는 단발 실행
사용자가 지정한 `tttaliesin/main` 작업 공간에서 OpenJev 실제 추론과 번역 입력 효과 확인

## 관찰 결과

[완료된 Modal 실행](https://modal.com/apps/tttaliesin/main/ap-3Mrzp1KElgEmqXoQBHdrdA)에서 60문항 모두 정답
문항별 확률 합·최대 확률 선택지·기대 답·고정한 OpenJev 소스 hash를 원시 결과와 대조한 검증 통과

| 모델·입력 | 관련성 | 주장 관계 | 도구 적합성 | 합계 |
| --- | --- | --- | --- | --- |
| Laya multilingual·한국어 | 6/12 | 8/12 | 2/6 | 16/30 |
| Laya multilingual·영어 | 6/12 | 8/12 | 3/6 | 17/30 |
| OpenJev·한국어 | 12/12 | 12/12 | 6/6 | 30/30 |
| OpenJev·영어 | 12/12 | 12/12 | 6/6 | 30/30 |

| OpenJev 입력 | p50 | p95 | 2초 이내 응답 | 중요 문항 오답 |
| --- | --- | --- | --- | --- |
| 한국어 | 163ms | 235ms | 30/30 | 0 |
| 영어 | 161ms | 173ms | 30/30 | 0 |

최장 단일 요청 약 336ms, 두 번째 GPU 함수의 모델 준비·워밍업 약 137.6초, worker 전체 약 147.0초
처음 이미지 빌드·가중치 다운로드·중단한 첫 시도는 위 준비 시간에 포함하지 않은 별도 작업
최종 worker exit code 0, 두 임시 앱의 stopped 상태와 전체 live container 0개 확인
Modal 사용량 API 조회 시 첫 시도 $0.79101437, 완료된 두 번째 시도 $0.16101128, 두 앱 합계 약 $0.95 표시
이는 조회 시점의 앱 사용량이며 저장 자원이나 최종 청구서 전체 금액과는 별도 수치
해당 조회 결과는 `.local/evaluations/openjev-modal-billing.json`에 보관

이 진단에서는 한국어도 영어와 같은 정답률로, 번역 계층이 정확도 개선의 필수 조건이라는 근거 없음
OpenJev를 후속 실사용 평가의 우선 모델로 삼을 근거 확보
간단한 30문항에서 양쪽 모두 만점인 결과로 어려운 한국어·긴 문맥·예외 지시의 우열이나 실서비스 품질을 확정할 수 없는 범위

입력 길이 검증에서 추가 차이 발견
OpenJev client packer는 문항당 157–179토큰, 실제 vLLM 응답은 모든 문항에서 1토큰 더 큰 수를 보고
짧은 진단 문항의 길이 제한 충족에는 영향이 없지만, production의 정확한 사전 토큰 검사는 서버 경로와 추가 대조 필요
이 차이는 결과를 숨기거나 보정하지 않고 `.local/evaluations/openjev-modal-verification.json`에 보존

## 실행 범위

[실행 스크립트](../scripts/modal_openjev.py)에서 CPU 모델 다운로드 후 GPU 진단 함수 한 번 호출
[컨테이너 worker](../scripts/openjev_modal_worker.py)에서 vLLM과 OpenJev API를 loopback으로 실행하고 종료 시 프로세스 그룹 정리
GPU 함수 최대 1개, 호출 시간 제한 1,200초, 초기 컨테이너 준비 제한 180초, 애플리케이션 재시도 0회, 단일 사용 컨테이너 설정
지속 배포·공개 HTTP 주소 없이 `modal run` 종료 시 임시 앱 종료
원격 전달 자료는 worker 코드와 합성 진단 문항으로 제한

| 항목 | 값 |
| --- | --- |
| GPU | NVIDIA RTX PRO 6000 한 대 |
| CPU·RAM | GPU 함수 4코어·64GiB, 다운로드 함수 2코어·4GiB |
| OpenJev | `e04794ab36e4f7e6040c2547baecdb2737ce2e79` |
| 기반 이미지 | `razorback16/openjev@sha256:597dc873e59137e976f4ced5d5694ddb309650964d514bf0107d178a96986d28` |
| 가중치 | `nvidia/diffusiongemma-26B-A4B-it-NVFP4` |
| 가중치 revision | `ec4ff3df205028f4e81c954c2227f9312b3ec2ea` |
| Modal SDK | `1.5.5` |
| FlashInfer 사전 컴파일 패키지 | `flashinfer-jit-cache==0.6.18.post1+cu130` |
| 캐시 | `jev-openjev-model-cache` Modal Volume |

기반 이미지의 vLLM fork 유지, OpenJev Python 패키지만 명시한 commit으로 설치
추가 JIT cache wheel은 FlashInfer 공식 릴리스의 SHA-256 `d1729490636a98f22f158518e10c257eec5a188d3dadf30b4c9de298675d8d4b`로 고정
GPU 실행 전 이미지 빌드 단계에서 `fused_moe_120.so` 포함 여부 확인
최초 진단의 준비 시간을 줄이기 위해 `--enforce-eager` 사용, 문맥 8,192토큰·동시 sequence 8개·canvas 64토큰으로 제한
최적화된 production serving 성능과는 구분할 실행 조건

## 비교 기준

[한국어 원문](../models/korean-diagnostic.json)과 [영어 입력](../models/english-diagnostic.json)을 문항별로 교대
기존 서비스의 `question()`으로 질문 지시문과 선택지를 생성해 Laya 실험과 동일한 판단 과제 유지
OpenJev의 기본 자동 재읽기 정책 유지, 별도 think·samples·steps 인자 생략
실제 설치된 OpenJev의 schema·system prompt 조립 코드와 tokenizer로 입력 길이를 검사하며 임의 잘라내기 금지

정답 수, 중요 문항 오답 수, 단일 요청 지연 p50·p95와 2초 이내 응답 수 기록
측정 위치는 Modal 컨테이너 내부의 loopback HTTP 클라이언트로, Windows↔Modal 왕복이나 실시간 번역 지연은 제외
워밍업 요청은 평가와 분리
2초를 초과한 답도 정확도에는 포함하되 기존 제품 예산 충족 여부는 별도 집계
전체 context preparation·Codex 작업 효율·Desktop MCP 연결 검증과는 별도 진단

문항은 agent-authored development fixture이며 사람 검토 heldout이 아닌 자료
이 결과만으로 production profile 또는 자동 선별 활성화 금지

## 재실행

프로젝트 루트에서 격리된 Modal 환경과 [고정 의존성](../models/modal-requirements.txt) 사용
Modal 자격 증명은 `.local/modal.toml`에 저장하며 버전 관리와 원격 자료 전송에서 제외

```powershell
& .local/tools/uv.exe pip sync --cache-dir .local/uv-cache --python .local/modal-venv/Scripts/python.exe models/modal-requirements.txt
$env:MODAL_CONFIG_PATH = Join-Path (Get-Location) '.local/modal.toml'
$env:PYTHONUTF8 = '1'
& .local/modal-venv/Scripts/python.exe -m modal run --env main scripts/modal_openjev.py --output .local/evaluations/openjev-modal.json
```

종료 결과와 문항별 API 원문은 `.local/evaluations/openjev-modal.json`, 최종 CLI 로그는 `.local/evaluations/openjev-modal-run2.log`에 보관
캐시는 재실행을 위해 보존되며 GPU 종료와 별도로 남는 저장 자원
[Modal 요금표](https://modal.com/pricing)의 RTX PRO 6000 가격은 초당 $0.000842, CPU·메모리·저장 비용 별도
함수 제한 시간은 실행 제약이며 달러 기준의 계정 지출 한도를 변경한 설정은 아닌 범위

## 초기 실행에서 해결한 문제

첫 실행 앱 `ap-ODdQzQ1xJ54X1jP9k80BkW`에서 모델 로딩과 GPU 메모리 약 17.08GiB 사용 확인
기본 OpenJev 이미지에 SM120 MoE의 사전 컴파일 JIT cache가 없어 CUDA 커널 97개를 런타임에 빌드하는 지연 발생
CPU 4개 요청 환경에서 10분 가까이 지나도 23개 빌드만 완료된 로그 확인
진단 응답이 나오기 전 컴파일 프로세스를 정지하고 중간 파일·서버 로그를 Modal Volume에 저장한 후 앱 종료
GPU 컨테이너 관찰 시작 17:54:32, 앱 종료 18:07:10 KST, 이 첫 시도에서 평가한 문항은 0개

[FlashInfer 공식 설치 안내](https://docs.flashinfer.ai/installation.html)에 따라 동일 버전·CUDA 13용 cache wheel을 이미지에 추가해 재실행
첫 시도 기록은 `.local/evaluations/openjev-modal-attempt1.json`과 `.local/evaluations/openjev-modal-run.log`에 보존

## 참고

- [OpenJev 공식 저장소](https://github.com/razorback16/openjev)
- [NVIDIA NVFP4 모델 카드](https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4)
- [Modal GPU 안내](https://modal.com/docs/guide/gpu)
- [기존 Laya 번역 비교](translation-evaluation.md)
