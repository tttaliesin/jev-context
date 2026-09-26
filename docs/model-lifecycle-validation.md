# 모델 수명 재설계 적용 검증

2026-09-26. [설계](model-lifecycle-design.md)에 따라 MCP 연결과 모델 소유권을 분리했다.

## 구현과 자동 검사

- MCP 시작 시 모델 준비와 20초 간격의 30분 재시도를 제거했다.
- 동일 실행 프로필의 여러 MCP가 인증된 로컬 실행기 하나를 공유한다.
- 실제 판단 요청 시 준비하고, 기본 120초 유휴 시 모델을 해제한다. 상태 조회는 유휴 시간을 연장하지 않는다.
- Windows MCP Job 수명과 독립된 실행기를 사용한다. 모델 본체는 실행기 소유 Job에 묶어 비정상 종료 후 잔류를 방지한다.
- 준비 실패, 잘못된 인증·프레임, 동시에 도착한 최초 요청, 죽은 endpoint, 이전 프로필과의 충돌을 검증했다.

전체 검사: **188 passed, 1 skipped**. Ruff·format 검사도 통과했다. 제외 1개는 Windows symlink 권한 조건이다.
가짜 모델만으로 확인한 항목과 아래 설치된 실제 모델 확인을 구분한다.
독립 검토에서 발견한 모델 전용 venv의 pywin32 부재, 이전 응답 큐 재사용, 영구 preparing 예외, persistent 옵션의 무시 문제는 수정했다.

검사 대상 소스 집합 SHA-256: `886274b274dca46b5e7a73bde7086c2c774b57a6f7d2a13e005816d86a5d8b7c`.
가짜 프로세스의 추가 테스트는 실제 두 MCP와 Windows Job 경계를 사용했다. 단순한 메서드 호출 모킹만으로 수명 분리를 주장하지 않는다.

## 현재 설치

이 프로젝트의 이전 eager 서버 네 개와 모델을 PID·명령·생성 시각을 대조한 뒤 종료했다. 데이터베이스와 원문 이력은 유지했다.
기존 서버 목록과 종료 기록은 `.local/lifecycle-20260926/legacy-processes.json`, `retired-process-ids.json`에 있다.
새 persistent 실행기는 모델이 없는 idle 상태로 시작했다. 모델 정확도 승격이나 새로운 모델 다운로드는 하지 않았다.

## 설치된 실제 모델 검증

`scripts/validate_model_lifecycle.py`가 설치된 SemIf OpenVINO GPU 모델과 공식 MCP client 두 개로 검증을 수행해 종료 코드 0, `passed: true`를 반환했다.
원시 호출·응답·시간은 `.local/lifecycle-20260926/live-report.json`에 기록했다.

| 확인 항목 | 실제 관측 |
| --- | --- |
| MCP 두 개 연결, 상태·작업 조회, 자료 동기화, 판단 off | 모델 worker 0개 유지 |
| 첫 required 판단 요청 | 0.047초에 준비 중으로 판단 보류 |
| 모델 준비 | 233.157초 |
| 두 MCP의 준비 후 판단 | 각각 0.860초, 0.875초, 모두 observed |
| 먼저 판단한 MCP 종료 후 남은 MCP 판단 | 0.844초, observed, 같은 실행기·worker 유지 |
| 상태만 조회하며 유휴 대기 | 121.657초에 idle 및 worker 없음 관측 |
| OS 프로세스 대조 | 실제 모델과 launcher, 이전 서버 모두 종료 확인 |

유휴 해제 시간은 5초 간격 상태 조회로 관측한 값이며 설정은 120초다. 프로세스 대조 시점은 2026-09-26 11:24:44 KST이다.
이 대조 시점에 남은 Python 프로세스 둘은 경량 공유 실행기와 그 가상환경 launcher였으며 모델 worker가 아니다. PID·생성 시각과 명령 대조 결과는 `active-processes.json`, `idle-processes.json`에 보존했다.

첫 시도는 모델 준비 후 검증 근거의 병행 저장으로 work revision이 바뀌어 `revision_conflict`로 중단됐다. 원본 `live-attempt1.json`과 console log를 보존했다. 준비 후 최신 revision과 목표·제약의 일치를 확인하도록 검증기를 수정한 뒤 위 검증을 다시 수행했다. 이 과정에서 제품 소스는 바꾸지 않았다.

준비에 약 4분이 걸리는 성능 문제는 남아 있다. 모델 해제 후 다음 판단 요청에는 다시 준비가 필요하며, 그동안 판단은 보류된다. 이번 수명 관리 수정으로 이 지연이나 모델 품질이 개선됐다고 주장하지 않는다.

위 내용은 수명 관리만 수정한 시점의 결과다. 이후 [시작 지연 개선](model-startup-performance.md)에서 원본과 같은 0 텐서 표현 및 정상 동작하는 GPU 캐시를 검증했고, 별도 프로세스의 준비 시간을 약 20초로 줄였다. 후속 적용 상태와 Desktop 재연결 여부는 해당 문서를 따른다.

## Desktop 재시작 후 확인

이전 stdio transport가 기존 서버 정리 후 closed가 되어 사용자가 Desktop을 재시작했다. 이후 이 대화의 native `workspace_status`와 `work_open`이 성공했고 저장된 작업 revision 11을 복원했다. hook 정의는 바꾸지 않았다.
첫 상태는 `idle`, 실행기·worker PID 없음이었으며, 첫 native required 판단 요청에서 새 일시 실행기(`persistent: false`)의 준비가 시작됐다. 이 동작은 OS가 독립 기동을 허용하는 현재 Desktop 환경에서 관측했다.

요청 후 243.627초 시점의 상태 조회에서 준비 완료를 확인했다. 이어 이 대화의 native `context_prepare(judge_mode=required)`가 0.875초에 `outcome: ok`, `judgment.status: observed`를 반환했다. 평가 ID는 `eval-bf5fa380e81d4c4ca45c574f8809d0b5`, packet ID는 `packet-87500f5198934b1da44f83a6626ea419`이다. 이 수치는 호출 성공 관측이며 모델 정확도나 높은 확신을 뜻하지 않는다. shadow는 유지했다.

마지막 판단 후 129.221초 시점에 `idle`, 실행기·worker PID 없음이 관측됐다. 준비와 유휴 종료의 시간은 간헐적 상태 조회 시점이므로 실제 완료 시각의 상한이며 유휴 설정은 120초다. 11:39:53 KST의 OS 프로세스 대조에서도 실제 모델·일시 실행기와 해당 자식이 모두 없음을 확인했다. 이 native 검증에서도 제품 소스 hash는 전체 검사 대상과 같았다.

현재 Desktop 재연결 검증 항목을 통과로 기록하고 모델 수명 재설계 작업의 완료 근거에 연결한다. 아래 원시 native 호출 기록과 프로세스 대조는 앞의 별도 MCP 두 개 검증과 구분한다.

개별 MCP 연결 종료 후 공유 모델 유지와 Desktop 앱 전체 재시작 후 실행기 생존은 다른 조건이다. 이전 문서의 “경량 실행기는 Desktop 재시작과 독립적”이라는 표현은 검증 범위를 넘었으므로 정정한다. 이전 실행기에 연결되지 않은 원인은 미확인이다. `persistent`는 유휴 시 모델만 해제하는 정책이며 서비스·자동 재시작 등록을 의미하지 않는다. 자동 기동이 허용되지 않는 환경은 [설계 문서의 시작 명령](model-lifecycle-design.md#windows-호스트-경계)으로 독립 실행기를 준비해야 한다.

## 기록 위치

- `.local/lifecycle-20260926/target.json`: 검증 대상 구현 hash
- `.local/lifecycle-20260926/checks.log`, `checks.json`: 전체 검사 결과
- `.local/lifecycle-20260926/live-report.json`: 설치된 실제 모델의 두 MCP 검증
- `.local/lifecycle-20260926/live-attempt1.json`: 첫 검증의 revision 충돌 원본
- `.local/lifecycle-20260926/idle-processes.json`: 실제 모델 및 이전 서버 종료 대조
- `.local/lifecycle-20260926/native-restart-report.json`: 재시작 후 현재 대화의 native MCP 호출·판단·유휴 상태
- `.local/lifecycle-20260926/native-restart-idle-processes.json`: native 자동 기동 실행기와 실제 모델 종료 대조
- `.local/lifecycle-20260926/ledger.jsonl`: revision에 연결한 작업 기준과 근거

수명 관리의 성공은 모델 품질·일반 코딩 효율 개선을 의미하지 않는다. 해당 효과는 별도의 평가 과제로 남는다.
