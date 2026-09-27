# 기억 제품 연구 적용 결과 — 0.8.0

2026-09-27. [적용 계획](agent-memory-implementation-plan.md)의 1~6단계 산출물. 연구의 P0를 적용했으며 P1/P2의 조건부 확장과 타사 성능 비교는 아래 남은 범위로 구분한다.

## 적용한 동작

- **서버 식별:** MCP `workspace_status.data.runtime`과 Desktop bridge에 instance ID, 시작 시각, Python 서비스 버전, 계약 버전, 시작 시 소스 fingerprint를 제공한다. 디스크 소스가 바뀌면 `restart_required`, hash를 확인할 수 없으면 `unknown`이다.
- **문맥 응답 관측:** MCP transport가 상태·문맥 응답을 준비할 때 프로젝트의 `.local/jev-runtime/observation.json`에 마지막 메타데이터를 남긴다. 문맥에는 work/packet ID, 작업·source set revision, 근거 수, 최종 MCP 응답 바이트, 시각, 서버 식별, outcome을 기록한다. query·근거 원문·목표·클라이언트의 임의 이름·절대 경로는 넣지 않는다. token은 측정하지 않아 `null`이다.
- **관측의 의미:** `response_prepared`는 응답 준비 관측이다. `host_receipt`와 `answer_use`는 `not_observed`다. 현재 대화의 영구 연결이나 모델의 실제 사용 증명으로 표시하지 않는다. 한 프로젝트의 마지막 관측이며 모든 호스트·작업의 이력 목록은 아니다.
- **격리와 실패 처리:** 다른 프로젝트·설정 hash의 기록을 성공으로 재사용하지 않는다. 읽기 전용·CLI·앱 조회는 관측을 쓰지 않고, 별도 MCP 점검도 기존 호스트 기록을 덮어쓰지 않는다. 훼손·크기 초과·기록 실패는 진단 미확인으로 처리하며 본래 MCP 응답을 유지한다.
- **앱:** 연결 상세에 앱 서버 상태, 마지막 MCP 상태 응답, 마지막 문맥 응답을 표시한다. 선택 작업과 일치하는 기록은 현재 작업 revision 일치 여부를 보여 준다. checkpoint 요약·다음 행동을 우선 사용하고 완료된 단계를 다음 행동으로 다시 표시하지 않는다. 한국어/영어와 진단 요약에도 반영했다.

핵심 구현: [runtime 관측](../src/jev_context/runtime_observation.py), [MCP transport](../src/jev_context/server.py), [Desktop bridge](../src/jev_context/desktop_bridge.py), [화면](../desktop/renderer/renderer.js).

## 검증

| 검사 | 관측 결과 |
|---|---|
| 새 runtime/관측 회귀 | 8개 통과. 소스 변경·hash 미확인·인스턴스 변경, 원문 비저장, 설정/프로젝트 격리, read-only/probe 제외, 파일 실패, 실제 MCP 재시작 |
| 전체 Python | `scripts/check.py`: lint·format 통과, **305 passed, 2 skipped**, exit 0. pytest 94.04초 |
| Node | window/locales/project-switch/bridge/diagnostics **32개 통과** |
| UI fixture | **58개 통과**, page error 0. 새 3개는 코드 변경·과거 작업 revision, 다른 작업 기록, 완료 checkpoint 표시 |
| 실제 Electron + MCP | 격리 프로젝트에서 한국어/영어 UI, 실제 packet ID, 작업 revision 일치, 별도 MCP 점검 후 관측 파일 동일, worker/broker 없음, page error 0 |
| Windows 패키지 | `scripts/build_desktop.ps1` exit 0, 아이콘 검증 통과, 앱 0.8.0 |

Python 실행은 `PYTEST_ADDOPTS=--basetemp=.t/memory-full -p no:cacheprovider`를 사용했다. 모델을 실행하지 않았다. 기존 모델 계약 검사는 모의 worker이며 판단 품질 평가가 아니다. skip 2개는 기존 플랫폼/권한 조건이다.

실제 앱 시험기: [test_memory_observation.cjs](../scripts/test_memory_observation.cjs). 성공 보고와 영문 캡처는 `.local/memory-validation/1790519406532/result.json`, `memory-en.png`다. UI fixture 보고는 `.local/desktop-redesign-20260926/fixture-state-report.json`이다. `.local` 원본은 공개 저장소에 포함하지 않는다.

초기 새 테스트 3개는 유효하지 않은 테스트용 project ID로 실패했다. ID를 실제 설정 규칙에 맞춘 뒤 통과했다. 첫 실제 앱 시험은 온보딩 창이 반드시 열린다고 가정했고, 다음 시험도 제한된 실행 환경에서 화면 준비를 관측하지 못했다. 시험기는 온보딩 표시 여부에 대응하고 종료 대기를 제한하도록 수정했다. 같은 격리 앱 시험을 정상 데스크톱 권한으로 실행한 결과 통과했다. 실패 원본 보고는 덮어쓰지 않았다.

## 후속 평가와 적용 경계

[비교 평가 명세](agent-memory-evaluation-protocol.md)에 30개 한국어 후보 사례, 비교 조건, 입력 동결 방법, 사람 검토와 heldout 조건, 미실행/실패/시간초과 및 비용 기록을 작성했다. **사람 검토·타사 설치 비교·새 실제 코딩 성능 실험은 하지 않았다.** 제품 검사를 경쟁 비교 정답률로 해석하지 않는다.

P1의 기억 편집/내보내기·수집 확대와 P2의 의미 검색·자동 정리·판단 승격은 이 평가 이후 결정한다. 기존 shadow 설정과 기존 평가의 불리한 결과를 유지한다. 이번 변경으로 토큰 절감·코딩 속도 향상이 입증됐다고 주장하지 않는다.

현재 Codex 대화의 기존 MCP 프로세스를 강제로 종료하거나 재연결하지 않았다. 구버전 프로세스는 새 식별/관측 기능을 제공할 수 없으므로 **Codex에서 MCP를 다시 연결한 뒤 실제 도구 호출로 확인해야 한다.** 앱의 ‘앱 연결 다시 연결’은 앱 bridge만 교체한다. 이미 실행 중인 Electron 화면도 새 버전으로 다시 열어야 한다.

source fingerprint는 로컬 파일 바이트 식별이며 Git commit·서명·실행 코드의 원격 증명이 아니다. 클라이언트 종류도 self-reported다. 관측 파일은 선택적 로컬 진단이며 감사 로그나 신원 인증 수단이 아니다.
