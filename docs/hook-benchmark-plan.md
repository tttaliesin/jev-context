# PostToolUse hook 비교 계획

> **설계 범위 밖 실험.** [통합 설계 2.0](design/unified-design.md)은 hook을 갱신 신호와 작업 ID 전달로 한정하고 출력 교체를 포함하지 않음. 2026-09-24 이 실험을 중단하고 `.codex/hooks.json`에서 `PostToolUse`를 제거, 설계 hook은 [호스트 기능표](host-capabilities.md) 참조. [experiments/output_filter/tool_hooks.py](../experiments/output_filter/tool_hooks.py)와 이 기록은 실험 근거로만 보존 (제품 패키지에서 분리)

큰 셸 출력을 요청 관련 줄로 교체하는 hook이 실제 코딩 작업의 토큰·요청 수·성공률에 주는 영향을 보는 소규모 비교
짧은 단일 질문 시험(관찰 55,188 → 교체 53,509 입력 토큰, 1회 측정)에서 확인하지 못한 긴 작업의 누적 효과가 대상
결함을 주입한 복사본 과제이며 실제 미해결 버그나 사람이 검토한 heldout으로 표시하지 않는 기준

## 과제

| 과제 | 주입 결함 | 큰 출력 | 채점 |
| --- | --- | --- | --- |
| `tests-uid` | `common.uid`의 구분자 `-` → `_` | 결함본 전체 테스트 출력 128,261바이트 | 복사한 테스트 전체 통과 |
| `log-budget` | `budget.bounded`가 필수 근거를 선택 자료처럼 제거 | 4,000줄 결정적 로그, ERROR 줄만 24KB 초과 | 숨긴 `check_budget.py`와 복사한 테스트 통과 |

`log-budget` 결함은 복사한 테스트로는 드러나지 않고 숨긴 검사에서만 실패, 로그로 원인을 찾아야 하는 과제
두 과제 모두 `src/` 밖 변경(테스트·pytest.ini·hook 설정 포함)이 있으면 실패 처리
과제 지시는 결함 파일을 알려 주지 않으며 hook이 요청 단어를 쓰도록 과제 문장을 첫 요청으로 직접 전달

## 조건과 실행

| 조건 | hook 명령 | 모델이 보는 결과 |
| --- | --- | --- |
| `observe` | `--mode observe --min-bytes 24000 --budget-bytes 8000` | 원래 출력 그대로, 교체했을 경우의 크기만 기록 |
| `filter` | `--mode filter --min-bytes 24000 --budget-bytes 8000` | 24KB 이상 셸 출력을 약 8KB 요약과 원본 경로로 교체 |

두 조건 모두 같은 hook을 실행해 hook 자체의 지연을 같게 유지하고 모드만 다르게 구성
과제·조건별 3회, 총 12회를 반복마다 조건 순서를 바꿔 교대 배치
시험마다 별도 git 저장소 작업 공간 `t\NN\w`와 hook 상태 `t\NN\h`, 결과를 덮어쓰지 않는 실행 기록
Windows 경로 길이 한도 때문에 짧은 폴더 이름과 작업 공간 안 pytest 임시 폴더 `.t` 사용

`codex exec --json --cd <작업 공간> -m <모델> -s workspace-write --dangerously-bypass-hook-trust`로 실행
신뢰 우회는 이 벤치마크가 만든 hook 설정에만 해당하는 자동화 전용 선택이며 평소 Desktop 설정과 구분
시험 중 `jev_context`·`web_image_bridge` MCP 서버는 끄고, 에이전트에 MCP·다른 에이전트·작업 공간 밖 읽기 금지 지시
hook이 저장한 원본 출력 파일은 예외로 읽기 허용

## 측정

- 성공: 고정된 검사 통과와 `src/` 밖 변경 없음
- Codex 세션 기록: 누적 입력·캐시 입력·캐시 제외 입력·출력 토큰, 모델 요청 수, 도구 호출 수
- 실행 시간: `codex exec` 시작부터 종료까지
- hook 기록: 24KB 이상 출력 수와 바이트, 요약 바이트, 저장된 원본 다시 읽기 횟수, hook 오류

조건별 중앙값과 성공 수를 비교하고 개별 12회 결과를 모두 보존
과제 2개·조건별 6회라 통계적 우위나 일반 코딩 효율을 주장하지 않는 범위
모델 응답의 변동이 커서 차이가 작으면 효과 없음이 아니라 이 표본으로 판단 불가로 기록
토큰 수를 요금으로 환산하지 않는 기준

## 동결

`suite.json`에 과제 문장, hook 구현 hash, hook 인수, 원본·결함본 검사 결과, 시험별 초기 파일 hash를 기록하고 `suite.sha256`으로 봉인
결과를 본 뒤 과제·검사·hook을 바꿔 재채점하지 않고, 바꿔야 하면 새 디렉터리로 다시 준비
준비 단계에서 Windows 경로 길이 초과로 원본 검사가 실패한 첫 시도는 폐기하고 짧은 경로로 다시 준비

### 실행 중 수정한 실행·채점 환경

과제·검사 기준·hook·봉인한 `suite.json`은 바꾸지 않고 실행 도구와 채점 환경만 수정
- 1번 첫 실행: 시험 작업 공간에 없는 `jev_context` MCP를 `-c`로 끄려다 설정 오류, 모델 호출 전 0.05초 종료. 해당 옵션 삭제 후 재실행, 실패 기록은 `t\01\launch-failed-1`에 보존
- 1번 첫 채점: 에이전트가 Codex 샌드박스에서 만든 `.t`의 권한 때문에 채점 pytest가 임시 폴더를 비우지 못해 110개 오류. 채점만 작업 공간 밖 `t\NN\g`를 임시 폴더로 쓰도록 수정 후 재채점
- 2번 첫 실행: 도구 시간 제한이 있는 반복 명령을 중단하면서 모델 첫 응답 전에 종료, 도구 호출과 작업 공간 변경 없음. 기록은 `t\02\aborted-1`에 보존하고 별도 프로세스로 다시 실행

## 중단과 부분 결과

2026-09-24 20:18 사용자 요청으로 12회 중 4회 완료 후 중단, 5번은 도구 호출 6회에서 종료하고 `t\05\aborted-1`에 보존
모델 `gpt-6-astra`, 완료한 4회는 과제별 관찰·교체 1쌍

| 시험 | 성공 | 입력 토큰 | 캐시 제외 | 모델 요청 | 도구 호출 | 시간 | 24KB 이상 출력 | 원본 다시 읽기 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| tests-uid 관찰 | 실패 | 427,443 | 37,811 | 13 | 12 | 164초 | 1 | 0 |
| tests-uid 교체 | 실패 | 645,302 | 31,286 | 21 | 20 | 230초 | 2 | 1 |
| log-budget 관찰 | 성공 | 375,920 | 36,592 | 10 | 9 | 128초 | 1 | 0 |
| log-budget 교체 | 성공 | 430,025 | 28,617 | 14 | 13 | 174초 | 1 | 1 |

두 쌍 모두 교체 조건에서 모델 요청·누적 입력·시간 증가, 캐시 제외 입력 감소
`tests-uid`는 두 조건 모두 ID 생성 대신 `policy.py` 검증을 바꾸는 우회 수정으로 MCP 하위 프로세스 검사 실패
과제별 1쌍이라 hook 효과를 판정할 수 없는 표본이며 조건 차이를 인과 효과로 확정하지 않는 기준
요청 증가가 저장된 원본 다시 읽기만으로 설명되지 않아 추가 탐색 원인은 미확인

## 재현

```powershell
.venv\Scripts\python.exe -X utf8 -m scripts.hook_benchmark prepare --directory .local\hb1
.venv\Scripts\python.exe -X utf8 -m scripts.hook_benchmark run --directory .local\hb1 --trial tests-uid-observe-1 --model <모델> --execute
.venv\Scripts\python.exe -X utf8 -m scripts.hook_benchmark grade --directory .local\hb1 --trial tests-uid-observe-1
.venv\Scripts\python.exe -X utf8 -m scripts.hook_benchmark report --directory .local\hb1
```

`run`은 `--execute` 없이는 실행할 명령만 출력
현재 준비본 `.local\hb1`의 봉인 hash `a0040deb2b214b0757602428a27bc9611ca3dea029f1df14b6555290e1ce9ce2`
