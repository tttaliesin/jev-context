# 데스크톱 연결 온보딩 적용 결과

2026-09-27. [적용 계획](desktop-onboarding-plan.md)에 따라 Electron 관리 앱을 **0.4.0**으로 갱신했다.

## 적용한 흐름

| 계획 | 적용 결과 |
|---|---|
| 프로젝트 준비 | native 폴더 선택, Python 확인·선택, 새 설정과 빈 DB 생성, 수집 허용 범위 입력. 기존 설정과 별도 위치의 설정 파일 지원 |
| 설정 설치 | 프로젝트 MCP·Skill 내용 미리보기, 변경 hash 검사, Jev 항목만 갱신, 백업, 실패 롤백, 중단된 설치 복구 |
| 호출 확인 | 서버 통신과 Codex 확인 요청 분리, 문구 생성·복사, 10분 유효기간, 설정 변경 무효화, MCP 수신 시각 표시 |
| 앱 UX | 4단계 안내, 상단·빠른 실행 진입점, 뒤로·나중에·재시도, 입력 보존, 설정 되돌리기, modal 키보드 격리 |
| 배포 | 새로운 화면·설치 API를 패키징 목록에 포함하고 실제 EXE 재빌드. README와 사용 안내 갱신 |

설치 백업과 확인 기록은 프로젝트 `.local/jev-setup`에 둔다. renderer는 임의 명령·설정 경로를 지정할 수 없다. 파일·폴더 선택은 Electron 메인 프로세스에서 수행하고 선택한 폴더와 다른 프로젝트를 가리키는 설정은 거절한다. Python 실행은 고정된 onboarding 모듈과 JSON 입력으로만 수행한다.

## 수행한 검사

- 최종 전체 Python 검사: **287 passed, 2 skipped**, pytest 83.46초, exit 0. Ruff lint·86개 파일 format 확인 통과. 제외 두 개는 Windows symlink 권한 조건이다. `PYTEST_ADDOPTS=--basetemp=.t/onboarding-publish -p no:cacheprovider`로 실행했다.
- 신규 설치·확인 검사: **17 passed, 1 skipped**. 한글 경로, 기존 설정/타사 MCP/Skill 보존, 중복 설치, 오래된 미리보기, 설치 중 실패 롤백, 중단 복구, 수정된 파일 복원 거부, 잘못된 TOML, 실행 중 잠금, 다른 프로젝트 거부, read-only, 확인 만료·잘못된 요청·변경된 설정·오래된 서버를 검사했다. skip은 Windows symlink 생성 권한 조건이다.
- 기존 MCP·관리 bridge와 새 설치 검사의 묶음도 **28 passed, 1 skipped**였다. 위 신규 경로 결속 검사 추가 이전 실행이다.
- 기존 화면 fixture **42개**, Node 창 상태 검사 **6개** 통과.
- 기존 실제 앱 조회·연결 점검·최소 창·확대 검사 통과. 실제 앱 UX의 재시작·사이드바·필터·단축키·창 상태 복원 **6단계** 통과.
- 실제 EXE 온보딩: 설정이 없는 프로젝트에서 시작 → 폴더 선택 → 기존 설정 선택 취소/실패 시 입력 보존 → 설정과 DB 생성 → 미리보기 후 외부 변경 거부 → 재조회·설치 → MCP 점검 → 확인 문구 복사 → 미확인 상태로 계속 → 다시 열기 → MCP 테스트 클라이언트 요청 수신 표시 → 설정 복원까지 통과. 페이지 오류 0개. 모델 worker/broker 없음, 등록 source 0개.
- 940×690 창, 125% 확대에서 modal과 하단 실행 버튼이 화면 안에 있는지 좌표 검사 및 네이티브 캡처 확인. 내용 영역은 스크롤된다.

설치 후 변경 검사를 추가하면서 Windows의 기본 문자 인코딩으로 한글 오류 JSON을 읽지 못하는 실패를 발견했다. onboarding 진입점의 출력을 UTF-8로 고정한 뒤 해당 검사와 실제 앱 경로를 다시 통과했다.

## 근거 파일과 재실행

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check.py
powershell.exe -NoProfile -File scripts/build_desktop.ps1
node scripts/test_onboarding.cjs
node scripts/test_desktop_states.cjs --output .local/onboarding-validation/fixtures
node scripts/test_desktop.cjs --scale=1 --output-dir .local/onboarding-validation/desktop-regression
node scripts/test_desktop_ux.cjs --output-dir .local/onboarding-validation/ux-regression
node --test scripts/test_window_state.cjs
```

Node UI 검사는 개발 환경에 설치된 Playwright를 사용한다. 별도 위치에 설치됐다면 해당 `node_modules`를 `NODE_PATH`로 지정한다. Electron 빌드는 기존 고정 런타임과 아이콘 검증을 사용한다.

- 최종 실제 앱 흐름: `.local/onboarding-validation/1790501906706/report.json`
- 기존 실제 앱: `.local/onboarding-validation/desktop-regression/app-read-test.json`
- 창·탐색 복원: `.local/onboarding-validation/ux-regression/ux-roundtrip-report.json`
- UI fixture: `.local/onboarding-validation/fixtures/fixture-state-report.json`
- [README의 연결 화면](images/desktop-onboarding.png): 1220×860 실제 앱 캡처. SHA-256 `8a7a29637431abdc4a92e6806833d3d879986a6ee64176b3a632f04279c530f9`. 최종 회귀 직전 동일 화면 구성의 실행에서 캡처했으며 내용 합성·픽셀 보정 없음.

실제 파일 쓰기·MCP 서버·Electron main/preload/renderer를 사용했다. 자동화에서는 OS 파일 선택기 응답만 임시 프로젝트로 대체했다. 확인 수신 성공 경로는 공식 MCP SDK를 사용하는 `codex-ui-test` 클라이언트로 검사했으며 **실제 Codex 호스트의 재연결 성공으로 보고하지 않는다**.

## 제품 경계

Codex의 프로젝트 신뢰·계정·승인 설정은 사용자 영역이다. 앱은 이를 자동 변경하거나 대화를 대신 전송하지 않는다. 같은 프로젝트를 Codex에서 열고 필요하면 MCP를 다시 연결한 뒤 확인 문구를 붙여넣는 단계가 남는다. 자가 보고 클라이언트 이름과 일회성 요청의 수신은 대화 신원 인증이나 영구 연결 증거가 아니다.

현재 배포는 Python·모델 가중치를 포함한 독립 설치 프로그램이 아니다. 이번 변경은 준비된 Python 환경에서 프로젝트 설정과 Codex 연결 작업을 앱으로 옮긴 것이다. 모델 다운로드·프로필 생성·active 승격·코딩 성능 개선을 완료한 것으로 해석하지 않는다.
