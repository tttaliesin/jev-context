# 프로젝트 정리 적용 결과

실행: 2026-09-27~28 · [적용 계획](project-cleanup-plan.md) 순서로 진행

## 적용한 구조 변경

- 중단한 출력 축약 실험과 관련 테스트 2개 파일을 `experiments/output_filter/`로 이동했다. 제품 wheel·기본 제품 pytest에서 제외하고 별도 검사와 CI 단계로 유지했다. 벤치마크 생성기의 실행 경로·hash 참조를 수정했으며, 기존에 동결한 입력과 결과는 수정하지 않았다.
- `project_files.py`에 안전 경로와 원자적 쓰기를 모았다. 온보딩은 기존 잠금·오류 계약을 유지하고, 실행 관측은 `observation.lock`으로 분리했다. 관측 모듈에서 온보딩 import를 제거했다.
- 엔진 fingerprint는 공통 코드(`common`, `engines`, `judgment`, `storage`)와 선택한 family의 실행 파일만 포함한다. Laya worker 수정은 SemIf의 식별값을 바꾸지 않는다. 공통 코드와 해당 worker 변경은 식별값을 바꾼다. 현재 서로 다른 어댑터가 `engines.py` 안에 있는 부분까지 완전히 분리한 것은 아니다.
- 한·영 README, 개발 안내, 실험 문서의 위치와 검사 명령을 갱신했다. Electron UI 기능과 공개 MCP 계약은 변경하지 않았다.

## 로컬 생성물 정리

다음 수치는 파일 길이의 논리 합계다. 파일시스템 압축·hardlink 등을 반영한 실제 디스크 여유 공간 증가량을 측정한 값은 아니다.

| 대상 | 정리량 | 보존한 내용 |
|---|---:|---|
| 이전 `.t/` 검사 디렉터리 185개 | 1,037,408,948 bytes (0.97 GiB) | 삭제 전 파일 경로·크기 목록 |
| `.local/startup-20260926/`의 `cache`, `cache-kernels`, `cache-opencl`, `cache-size` | 12,107,287,604 bytes (11.28 GiB) | 결과 JSON 21개·로그 12개 및 나머지 기록 |
| 미사용 변환본 2개의 중복 `model.bin` | 8,422,431,042 bytes (7.84 GiB) | 같은 SHA-256의 가중치, 각 변환본의 XML·토크나이저·설정·복원 명세 |
| **합계** | **21,567,127,594 bytes (20.09 GiB)** | 작은 정리 명세 파일 용량은 차감하지 않음 |

첫 실행은 사용자 계정에서 78개 디렉터리를 제거하고 접근 불가 111개를 보존했다. 그 뒤 프로세스 목록을 다시 확인하고 해당 디렉터리를 생성한 샌드박스 권한으로 재검사하여 나머지 111개를 제거했다. ACL을 변경하거나 소유권을 강제로 가져오지 않았다. 처음 조사한 약 514 MiB는 해당 계정이 읽을 수 있는 범위였으며 최종 `.t` 수치는 두 권한 범위에서 확인한 합계다.

모델 정리는 이름만 보고 결정하지 않고 실제 파일 hash를 비교했다:

| 제거한 변환본 | 동일 가중치를 보존한 변환본 | `model.bin` SHA-256 |
|---|---|---|
| `qwen3.5-4b-ov512-f16` | `qwen3.5-4b-ov512-int8` | `a56498fca576e14a6093903c15591bac791aaf17c9b64936908f0a760b3ae55b` |
| `qwen3.5-4b-ov512b-f16` | `qwen3.5-4b-ov512b-int8` | `f0ffc1bf8d86e9d07c7445059a6f2e40981cf05ac1e79a5715f32538e599e668` |

각 디렉터리의 `model.bin` 이외 파일을 백업하고 hash가 일치하는지 확인한 다음 디렉터리를 제거했다. 복원은 백업한 메타데이터와 표의 동일 가중치를 합쳐 원래 이름의 디렉터리를 만드는 방식이다. 정리 대상 2개는 현재 및 보존 프로필에서 참조하지 않았고 과거 GPU 실패 로그가 남아 있었다.

상세 명세는 Git 제외 경로 `.local/cleanup-20260927/`에 있다: `generated-results.json`, `generated-retry-results.json`, 개별 파일 목록, `model-results.json`, `model-metadata/<변환본>/cleanup-manifest.json`. 해당 metadata 디렉터리는 복원 근거이므로 임시 캐시처럼 지우지 않는다.

## 보존한 항목과 한계

- 작업 DB·설정·등록 원문, 기존 `.local` 벤치마크 결과와 동결 입력을 보존했다. MCP와 다른 프로젝트 앱 프로세스를 종료하지 않았다.
- 현재 SemIf `qwen3.5-4b-ov1024b-int8-rank3-f16`, 직접 변환 원본 `ov1024b-int8`, 원래 Qwen 가중치, 현재 model-cache와 bake 환경은 유지했다.
- Laya, 비교 모델, 그 외 IR·venv는 지원·재현 자료이므로 보존했다. 일부 과거 선택 프로필에는 이전 저장소의 절대 경로가 남아 있다. 이번에는 현재 설정을 바꾸거나 해당 후보를 실행하지 않았다.
- Electron–Python 연결, 공유 모델 broker, v1 기반 v2 계약은 실제 사용 중이므로 유지했다.
- 엔진 fingerprint 규칙이 바뀌므로 이전 실행 프로세스·평가·원격 세션과 새 식별값을 혼용하지 않는다. 앱과 MCP를 재연결해야 새 실행 코드가 반영된다. 모델 준비나 승격, 원격 GPU 호출은 실행하지 않았다.

## 검증

- 관련 제품·실험 검사: 85 passed, 1 skipped.
- 전체 제품 검사: lint·format 통과, **288 passed, 2 skipped**. 기존 305개에서 실험 22개를 분리하고 회귀 5개를 추가했다. skip은 기존 플랫폼·권한 조건이다.
- 별도 실험 검사: lint·format 통과, **22 passed**.
- Node 검사: **32 passed**.
- wheel 빌드 성공, 36개 항목에서 실험 코드가 없고 `project_files.py`가 포함됨을 확인했다.
- Electron 패키지 빌드 성공, 아이콘 검증 통과.
- 첫 전체 검사에서 변경 파일 2개의 줄바꿈에 대한 format 실패가 있었고 formatter 적용 후 위 전체 검사가 통과했다. 실패를 테스트 통과로 포함하지 않았다.

- 실제 MCP → Python bridge → 패키지 Electron 관측 검사 통과: `.local/memory-validation/1790521724149/result.json`, pageErrors 없음, 한영 UI와 packet ID·독립 probe 관측 보존 확인, worker·broker 실행 없음.

위 검사 이후 이번 작업에서 만든 `.t/cleanup-focused`, `.t/cleanup-full`, `.t/cleanup-experiments`도 별도 목록을 남기고 정리했다. 이 소량의 추가 제거는 위 20.09 GiB에 포함하지 않았다.

원격 커밋과 CI 상태는 이 문서가 포함된 Git 이력 및 해당 커밋의 Actions 결과로 확인한다.
