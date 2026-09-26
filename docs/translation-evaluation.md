# 영어 번역 입력 비교

2026-09-22 · 기존 한국어 진단 30문항과 대응 영어 번역문 비교
목적은 번역 단계로 현재 판단 품질 문제를 해결할 수 있는지 확인
제품의 실시간 번역 계층이나 자동 선별을 활성화한 변경은 아닌 실험

## 입력과 실행 조건

[한국어 원문](../models/korean-diagnostic.json)의 state 문자열을 [영어 입력](../models/english-diagnostic.json)으로 번역
문항별 source_case_id·원문 dataset SHA-256·동일 기대 답과 중요 오류 표시 유지
30개 원문에 누락 없이 번역 대응, 코드 식별자와 허용 행동 값 유지 확인
번역자는 Codex, 별도 번역 API 호출 없음
번역문을 미리 작성한 실험이므로 번역 지연·토큰 비용·실시간 번역 오류율은 측정하지 않은 범위

동일 Laya SDK·CPU 실행 환경·질문 template·선택지·2초 판단 제한 사용
한국어와 영어의 실행 순서를 문항별로 교대하고 모델별로 별도 프로세스에서 실행
기존 질문 지시문과 선택지는 이미 영어이며 첫 비교에서 바꾼 부분은 state의 자연어 문자열

[공식 Laya 모델 카드](https://huggingface.co/convaiinnovations/laya)에 따라 multilingual과 영어용 root checkpoint를 각각 선택
모델 revision `1c5edc17a7acd8701df6fc341c0d179f1c62c982`, SDK revision `573e5b62696ba441230cd6be71d593331b5d23af`
영어용 가중치와 tokenizer 등 약 846MB를 별도 준비하고 hash 검증, 기존 프로젝트 profile은 유지

## 관찰 결과

| 모델 | 입력 | 관련성 | 주장 관계 | 도구 적합성 | 합계 |
| --- | --- | --- | --- | --- | --- |
| multilingual | 한국어 | 6/12 | 8/12 | 2/6 | 16/30 |
| multilingual | 영어 번역 | 6/12 | 8/12 | 3/6 | 17/30 |
| 영어용 root | 한국어 | 2/12 | 7/12 | 3/6 | 12/30 |
| 영어용 root | 영어 번역 | 4/12 | 9/12 | 1/6 | 14/30 |

모든 값은 작은 개발용 진단 자료의 관찰 수치이며 일반적인 모델·언어 우열의 추정이 아닌 범위
영어 번역은 같은 모델의 합계를 소폭 개선했지만 자동 선별을 활성화할 품질에는 미달
영어용 모델로 바꾸는 것만으로도 현재 과제의 문제를 해결하지 못한 결과

추가 진단에서 영어 query·claim을 질문 지시문으로 옮기고 candidate만 state에 전달
multilingual 14/30, 영어용 root 15/30으로 질문 형식 변경만으로도 해결되지 않은 결과
이 추가 진단은 첫 비교와 다른 template이므로 번역만의 효과에 합산하지 않는 기준

원시 결과 보관 위치

- `.local/evaluations/translation-paired-multilingual.json`
- `.local/evaluations/translation-paired-english-model.json`
- `.local/evaluations/explicit-condition-multilingual.json`
- `.local/evaluations/explicit-condition-english.json`

## 번역 계층을 넣을 경우의 구조

한국어 원문·사용자 지시 보관 → 판단에 필요한 부분만 영어화 → 로컬 모델 판단 → 원문 ID에 결과 연결 → 한국어로 작업 진행

처음에는 Codex가 기존 작업 과정에서 영어 판단용 입력을 작성하는 방식 검토
별도 번역 모델을 항상 띄우는 방식은 번역 품질과 전체 비용을 비교한 뒤 선택
파일 경로·코드·명령·source ID는 그대로 보존, 부정·예외·허용 범위는 생략 없이 번역
자료 전체를 매번 번역하지 않고 원문 revision과 번역 규칙 버전을 키로 재사용
번역문을 파생 자료로 취급해 원문 변경·삭제 시 함께 무효화
영어 입력만의 성적과 한국어→영어→판단 전체 경로의 성적을 구분해 평가

판단 결과는 관련·무관·반박 같은 고정 ID로 반환하므로 원문 전체의 한국어 역번역은 불필요
영어 전용 성능이 좋은 판단 모델 선정과 번역 오류·전체 처리 비용 검증이 선행 조건
현재 관찰로는 “한국어이기 때문에 실패”라고 단정할 근거가 부족하며, 번역 계층만 추가해 해결됐다고 보고할 수 없는 상태
