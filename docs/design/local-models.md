# 무료 로컬 판단 엔진 비교와 재사용 설계

이전 설계 참고 기록 · 제품 방향·범위·완료 기준은 [통합 설계 2.0](unified-design.md)으로 대체
아래의 현재·미구현·검증 상태는 작성 당시의 기록이며 최신 실행 상태와 구분

2026-09-22 조사 · [통합 설계안 0.4](proposal.md)의 모델 선택 근거
첫 버전의 엔진 수명·연결·활성화 방식은 [상세 설계 1.0](detailed-design.md)에서 정의
프로그램 설치·가중치 다운로드·실제 추론 없이 공식 저장소와 모델 카드로 확인한 결과

## 비용 요구와 결론

Jev는 입력량에 따른 유료 API이며 조사일 공식 가격은 입력 100만 토큰당 US$0.042, 출력 무료
근거: [TypeSafe 모델·가격 문서](https://docs.typesafe.ai/models)

이번 설계의 요구는 추가 판단 모델의 API 청구 없이 사용자 컴퓨터에서 실행하는 것
우선 재사용 검토 후보는 OpenJev/DiffusionGemma, 경량 비교 후보는 Laya multilingual
기본 검색만 사용하는 구성과 두 엔진을 먼저 비교하고 GLiClass·NLI·BGE는 특정 기능의 부족이 확인된 뒤 검토
기본 엔진은 미확정이며 하드웨어·한국어 품질·지연의 실측을 거쳐 선정
유료 Jev 대체 호출이나 Jev 호출이 필요한 평가·학습 절차를 기본 설계에 포함하지 않는 결정
로컬 연산의 전력·장비·메모리·운영 시간과 기존 Codex 비용은 계속 발생 가능한 별도 항목

## 확인한 후보

아래 라이선스는 링크된 정확한 모델 카드의 표기이며 계열 전체·모든 파생 모델에 대한 동일 조건 보증과 구분
역할 추천은 공식 기능 설명을 바탕으로 한 설계 판단이며 우리 데이터의 성능 측정 결과가 아닌 상태

| 후보와 공식 근거 | 공개 라이선스 표기 | 맡기기 좋은 역할 | Jev와의 차이·선택 조건 |
| --- | --- | --- | --- |
| [OpenJev](https://github.com/razorback16/openjev), [DiffusionGemma 가중치](https://huggingface.co/google/diffusiongemma-26B-A4B-it) | 서버·Google 가중치 Apache-2.0, 파생 가중치 약관 별도 확인 | Jev 호환 API로 Choice·Noul·Score 판정 | 우선 재사용 후보, 지원 장비·패치 실행체·점수 정의 검증 필요 |
| [Laya](https://github.com/NandhaKishorM/laya), [공식 가중치](https://huggingface.co/convaiinnovations/laya) | Apache-2.0 | 작은 상태와 질문의 Choice·Noul·Score 판정 | 경량 비교 후보, 입력 예산·보정 필요 |
| [GLiClass x-base](https://huggingface.co/knowledgator/gliclass-x-base), [라이브러리](https://github.com/Knowledgator/GLiClass) | Apache-2.0 | 요청 분류, 복수 스킬 후보 선택 | 제로샷 다중 라벨 분류에 적합한 비교 후보, 세 판단 형식의 일대일 대체 아님 |
| [mDeBERTa NLI](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli) | MIT | 원문이 주장을 지지·반박하는지 분류 | entailment·neutral·contradiction 모델, 전반적인 진실 판정이나 임의 채점 아님 |
| [BGE reranker v2 m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) | Apache-2.0 | 질의에 맞춰 문서 후보 재정렬 | 관련성 점수 모델, 반박 판정·권한 결정·Choice 계약을 대신하지 않음 |

### DiffusionGemma-as-Jev의 공개 범위

DiffusionGemma의 고정된 답변 자리에서 선택지별 확률을 읽어 구조화된 판단을 반환하는 방식
원본 구현은 Matt Mastracci의 [vLLM PR #57250](https://github.com/vllm-project/vllm/pull/57250)이며 조사 시점에 미병합 상태
독립 공개 구현의 존재를 확인했으며 Jev 자체의 내부 구조·학습·품질을 재현했다는 보증으로 해석하지 않는 기준

| 공개 프로젝트 | 확인한 제공 범위 | 재사용 판단 |
| --- | --- | --- |
| [razorback16/openjev](https://github.com/razorback16/openjev) | Jev 호환 서버, NVIDIA/vLLM·Apple Silicon/MLX 경로 | 실행 서버의 우선 검토 후보 |
| [mmastrac/djev-spark](https://github.com/mmastrac/djev-spark) | DGX Spark·GB10용 컨테이너와 구조화 판정 서버 | 원본 실행 구성 참고, 장비 조건에 맞을 때 검토 |
| [GenericJevMCP-via-DiffusionGemma](https://github.com/Bizuayeu/GenericJevMCP-via-DiffusionGemma) | MCP·CLI·HTTP 연결, Linux ARM64·GB10 환경 검증 설명 | 연결 계층 참고, 우리 Desktop 검증은 별도 |

OpenJev README의 NVIDIA 경로는 GPU 메모리 최소 24GB, NVFP4 가중치 약 18GB, RTX PRO 6000 Blackwell 검증을 명시
같은 메모리 용량만으로 모든 GPU의 실행 가능성을 확정하지 않고 장비 세대·연산 지원·드라이버·실행체를 함께 확인
Apple Silicon에는 MLX 경로가 있으나 실제 장비의 여유 메모리·지연은 별도 측정 대상
NVFP4 가중치의 [NVIDIA 모델 카드](https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4)는 Apache-2.0과 Gemma 약관 참조를 함께 표기하므로 고정 revision의 조건을 확인할 필요
다운로드 크기를 실제 메모리 사용량으로 대체하지 않고 KV 캐시·동시성·입력 길이·시작 순간의 점유까지 측정

OpenJev의 confidence는 정규화된 엔트로피 기반 값이며 djev-spark는 최상위 선택지 확률을 사용
같은 이름의 필드라도 엔진별 정의가 달라 임계값과 보정값을 공유하지 않는 설계
근거: [OpenJev API](https://github.com/razorback16/openjev#api), [djev-spark 응답](https://github.com/mmastrac/djev-spark#response)

현재 상태는 문서와 공개 코드 구성의 조사이며 소스 전체 감사·실제 실행·한국어 우열 검증은 미수행
처음 조사에서 누락한 이 계열을 반영해 Laya가 가장 가까운 기본 후보라는 기존 선정 근거를 수정
Jev 호환 엔진이나 MCP 연결 자체를 새롭게 처음 만드는 제품으로 설명하지 않는 기준

### 장비에 따른 선택 절차

| 확인된 환경 | 먼저 검토할 경로 | 기준 미달 시 동작 |
| --- | --- | --- |
| 장비 미확인 또는 모델 미준비 | 모델 없는 기억·검색·출처 조회 | 같은 기본 기능 유지 |
| CPU 중심 또는 큰 모델을 유지할 메모리 부족 | Laya multilingual의 CPU 호환성·지연 평가 | 의미 판단을 끄고 검색 결과 제공 |
| 여유 메모리와 지원 연산을 갖춘 NVIDIA GPU | OpenJev/vLLM, Laya와 같은 실제 과제 비교 | 실행 실패·품질 미달 후보를 제외 |
| 충분한 메모리의 Apple Silicon | OpenJev/MLX 경로 평가 | 의미 판단 없이 기본 기능 유지 |
| DGX Spark·GB10 | OpenJev와 djev-spark의 실제 지원 구성 검토 | 동작이 검증된 경로만 평가에 포함 |

사용자 장비 정보는 아직 미확인으로 기록하고 장비 구매·유료 GPU 대여·외부 호스팅을 기본 경로에 포함하지 않는 결정
Windows 클라이언트와 GPU 서버의 실행 환경을 구분하고 Windows 네이티브·WSL·컨테이너 호환성을 문서만으로 확정하지 않는 기준
현재 설계의 판단 데이터 처리는 사용자 컴퓨터의 로컬 실행으로 제한하며 다른 장비 전송은 후속 요구사항으로 별도 결정

### Laya의 확인된 제약

공식 README는 기본 체크포인트의 언어 한계·짧은 입력·큰 후보 집합의 성능 저하·확신도 보정 필요성을 함께 명시
typed-decisions 결과는 해당 벤치마크의 학습 분할에 맞춘 체크포인트 결과이며 일반 코딩 판단의 성능으로 전용할 수 없는 근거
Jev 비교도 동일 환경의 직접 대조 실험이 아니라는 설명이 있어 “항상 더 정확·더 빠름”으로 채택하지 않는 기준
근거: [Laya README의 Honest limits·Calibration](https://github.com/NandhaKishorM/laya)

모델 카드상 multilingual은 mmBERT 기반 322M 규모, 기본 총 입력 1,024 토큰과 질문·선택지 예산 256 토큰으로 설명
나머지 원문 공간은 약 768 토큰이며 특수 토큰·실제 포장에 따라 추가 여유가 필요한 조건
확장 가능한 encoder 길이를 기본 운용 길이·동일 정확도 보장으로 간주하지 않는 기준
근거: [Laya 모델 카드의 Architecture·Honest Limits](https://huggingface.co/convaiinnovations/laya)

### GLiClass의 선택

`gliclass-x-base`는 다국어 backbone과 다중 라벨 인터페이스가 확인되는 직접 비교 후보
정답이 복수인 스킬 추천을 한 개의 Choice로 강제하지 않고 후보별 적합도로 표현하는 대안
다중 라벨 점수는 합이 1인 상호 배타적 확률분포가 아니므로 Laya의 출력과 동일 처리하지 않는 설계
근거: [x-base 모델 카드](https://huggingface.co/knowledgator/gliclass-x-base)

추가로 확인한 [GLiClass multilang-mini](https://huggingface.co/knowledgator/gliclass-multilang-mini)는 별도 후보
카드의 명시된 20개 학습 언어 목록에 한국어가 없으므로 이름의 multilingual만으로 한국어 우위를 가정하지 않는 판단
한국어·영어 혼합 입력으로 x-base와 함께 비교할 수 있으나 모두를 동시에 제품에 적재하지 않는 제안

### NLI와 검색 모델의 경계

NLI는 원문을 premise, 주장을 hypothesis로 두는 구조이며 두 입력의 방향이 중요
확인한 mDeBERTa는 다국어 기반과 MNLI·XNLI 튜닝을 사용하지만 이 프로젝트의 한국어 기술 문서 판정은 미검증
neutral을 곧바로 “허위”로 변환하지 않고 지원 근거 부족으로 표현하며, 별도의 자료 누락 상태도 유지
근거: [mDeBERTa 모델 카드](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli)

BGE의 높은 점수는 질의와 자료의 관련성을 나타내며, 질의의 주장을 반박하는 자료도 관련 자료일 수 있는 관계
sigmoid로 0~1에 맞춘 점수를 정답 확률로 해석하지 않는 원칙
근거: [BGE 모델 카드](https://huggingface.co/BAAI/bge-reranker-v2-m3)

## 한국어 처리 설계

### 확인된 사실과 아직 모르는 부분

[TypeSafe 언어 지원](https://docs.typesafe.ai/models#language-support)은 영어를 주요 학습 언어이자 가장 정확한 언어로 설명하고 CJK를 포함한 다른 언어의 품질 차이를 명시
이를 한국어의 특정 정확도 수치로 확대하거나 다른 모델인 OpenJev에 그대로 적용하지 않는 기준

[Laya 공개 평가](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md)의 MASSIVE 20개 선택지 의도 분류에서 한국어 정확도는 영어 모델 0.110, 다국어 모델 0.450으로 보고
저자 보고의 특정 실험 결과이며 우리 코딩 작업의 정답률·일반 한국어 능력·OpenJev 대비 순위로 환산하지 않는 기준
다국어 모델 선택의 필요성을 보여주는 근거이지만 충분한 제품 품질의 증거는 아닌 상태

[DiffusionGemma 모델 카드](https://huggingface.co/google/diffusiongemma-26B-A4B-it)는 다국어 능력을 설명하나 이번에 읽은 자료에서 OpenJev 판단 방식의 한국어 코딩 평가 결과는 확인하지 못한 상태
일반 생성 모델의 다국어 지원과 한 번의 구조화 판정 품질을 별도로 검증할 필요

### 보완 경로의 순서

| 순서 | 구성 | 채택 조건 |
| --- | --- | --- |
| 1 | 한국어 원문을 다국어 엔진에 직접 전달 | 해당 한국어 과제에서 원문 근거·부정·예외 보존과 품질 확인 |
| 2 | 원문 유지, 검토된 한영 질문·선택지 템플릿 사용 | 원문 단독 대비 개선, 입력 예산 초과와 해석 변화 없음 |
| 3 | 필요한 발췌만 로컬 번역하고 원문 판정과 대조 | 번역 의미 보존·최종 품질·추가 지연이 기준 충족 |
| 4 | 모델 판정을 유보하고 원문·주변 맥락을 Codex에 제공 | 평가 미달·번역 손실·모델 불일치·대상 불명 |

순서 1·2는 개발 평가에서 비교해 과제별 한 프로필을 선택하며 매 요청마다 전부 실행하지 않는 구성
순서 3은 별도의 로컬 번역 모델·실행 호환성·라이선스 검토가 필요한 실험 후보이며 아직 선정·검증하지 않은 상태
번역은 사용자에게 영어 작성을 요구하지 않고 내부 보조 자료로만 사용
추가 유료 번역 API나 Codex 호출을 자동 번역 서비스로 붙이지 않는 조건
Codex에 원문을 반환한 이후의 재검토는 기존 작업 흐름과 할당량에 포함되며 정확도 보증으로 해석하지 않는 기준

번역문은 원문의 권한·민감도·출처를 계승하는 파생 자료로 저장하고 독립된 추가 증거로 중복 집계하지 않는 규칙
한국어 원문·번역문·질문·선택지의 토큰을 실제 입력 기준으로 계산하고 한도를 넘으면 원문을 조용히 삭제하지 않는 기준
원문 revision·언어 프로필·번역 모델·용어집·질문 템플릿 변경 시 관련 판정과 번역 캐시 무효화
부정·예외·시간·조건·승인 범위의 번역 오류를 일반적인 문체 차이와 분리해 평가
다국어 프로필의 높은 confidence만으로 한국어 성능 부족을 우회하지 않는 운영

## 판단 엔진을 교체할 수 있는 구조

기본 검색 → 필요한 원문 묶음 → 선택한 로컬 엔진의 판정 → 정책·예산 검증 → Codex에 원문과 판정 반환
엔진이 불확실하거나 실행 불가이면 기본 검색 결과와 유보 이유를 반환하고 유료 API를 호출하지 않는 흐름
자료 저장·작업 기록·검색 인덱스는 엔진과 독립적으로 유지하고 엔진 변경 시 영향받는 판정과 문맥 묶음만 무효화

| 판단 과제 | 기본 설계 | 무료 대체 후보 | 채택 조건 |
| --- | --- | --- | --- |
| 스킬·도구 후보 적합성 | 선택한 엔진과 해당 없음 | GLiClass | 후보 누락·오선택·유보를 같은 자료로 비교 |
| 문서 관련성 | 기본 검색 뒤 필요한 후보에 선택형 판정 | BGE reranker | 검색 회수율·최종 품질 개선이 추가 비용을 정당화 |
| 주장 지원·반박 | 원문과 주장의 Choice, 근거 부족 포함 | mDeBERTa NLI | 부정·예외·한영 혼합 자료에서 개선 확인 |
| 상세도 선택 | 예산·보호 규칙 우선, 모델 추천 보조 | 규칙만으로 동작 | 중요 사실 누락 없이 선택 비용 감소 |
| 실행 권한·계산 | 결정적 코드와 호스트 | 모델 대체 불필요 | 모델의 확신도와 독립된 규칙 유지 |

처음에는 검색만 사용·검색과 Laya·검색과 OpenJev를 차례로 비교하고 결과에 따라 한 엔진 또는 모델 없는 구성을 선택
모든 후보를 연결해 중복 판정하는 방식은 메모리·지연·운영 비용 때문에 기본 구성에서 제외
Ollama는 별도 실행 도구이며 이번에 확인한 Laya의 공식 Python 인터페이스와 같은 제품으로 취급하지 않는 기준

## 어댑터에서 보장할 계약

원문 ID·revision·언어·질문·선택지·입력 예산을 전달하고, 출력에는 선택·점수·유보 이유·모델 버전·판정 대상 범위를 포함
라이브러리가 동일한 필드 이름을 사용해도 실제 의미와 범위를 확인한 뒤 변환
Laya의 Choice·Score·Noul을 다른 모델의 임의 점수로 채워 호환된다고 선언하지 않는 기준
OpenJev의 Jev 호환 API도 지원 선택지 수·오류 형식·모델 ID·확신도 의미를 확인한 범위에서만 사용
로컬 엔진 주소는 설치 설정에서 고정하고 문서나 도구 입력이 임의 외부 서버를 선택하지 못하도록 제한
응답은 신뢰할 지시가 아닌 판정 자료로 처리하고 결과의 근거·범위·모델 출처를 유지

입력 순서·선택지 순서가 바뀔 때의 안정성과 토큰 잘림 여부를 별도 기록
원문 분할은 구문·의미 단위로 수행하고 chunk별 판정을 문서 전체의 사실로 일반화하지 않는 원칙
여러 chunk가 충돌하면 해당 원문들을 함께 반환하고 판정의 단순 평균으로 갈등을 없애지 않는 처리

가중치·tokenizer·기반 모델 설정·서버 및 실행체 revision·질문 포장·샘플링 설정·보정값·실행 정밀도를 버전 묶음으로 관리
파일 준비 완료 이후 로컬 파일 전용 추론과 네트워크 차단 조건에서 실행 가능한지를 합격 기준에 포함
모델 파일의 다운로드 크기와 실제 RAM·VRAM 점유는 서로 다른 수치로 측정

## 과금 없는 평가와 개선

우선 같은 실제 과제 30개로 기본 검색, Laya, OpenJev를 비교하고 기능별 부족이 확인되면 GLiClass·NLI·BGE 추가 평가
지원되지 않는 장비의 결과는 미평가로 표시하고 다른 장비의 공개 속도를 로컬 실측값으로 대입하지 않는 기준
Jev 유료 호출을 기준 정답 생성이나 필수 교사 모델로 요구하지 않는 구성
정답은 사람의 판단과 원문·실행 증거에서 만들고, 기존 Codex가 작성한 임시 라벨은 사람이 확인하기 전 확정하지 않는 기준

가중치 학습이 필요한 경우 학습·질문 조정·확신도 보정·최종 평가 자료를 분리
보정만으로 분류 정답률이나 누락된 정보가 자동 개선된다고 가정하지 않는 기준
부족한 기능은 관찰 모드 또는 비활성으로 유지하고, 로컬 후보의 기준 미달을 유료 경로 자동 도입의 이유로 사용하지 않는 정책

실제 한국어 성능·설치 호환성·Windows CPU/GPU 속도·최대 동시성·장기 메모리 사용은 아직 미측정
현재 결론은 무료 로컬 실행이 가능한 후보와 적용 설계의 조사이며 성능 우열 판정은 [검증 계획](acceptance.md)에 따라 결정
