# Jev Context

**이 PC에서 실행:** 탐색기에서 `dist\JevContext\JevContext.exe`를 더블클릭합니다.
Electron 관리 앱 **0.3.4**에서 로컬 모델 준비·종료, 연결 상태 점검, 저장된 작업 조회를 할 수 있습니다. `Ctrl+B`로 작업 목록을 완전히 숨기고, `Ctrl+K`의 **빠른 실행**에서 명령이나 불러온 작업을 찾습니다. 목록의 제목·목표 필터는 `Ctrl+F`, 단축키 안내는 `F1`입니다. 빠른 실행으로 기록을 열어도 사이드바 접힘 설정은 유지합니다. 코딩 대화는 Codex에서 계속합니다.

작업 목록은 기본 **260px**, 창 공간에 따라 **220–400px**에서 드래그·키보드로 조절하고 경계 더블클릭으로 기본 폭을 복원합니다. 폭·접힘 상태, 프로젝트별 선택 작업·필터, 창 위치·크기를 기억합니다. 상단에 현재 작업을 표시하고 자동 조회는 읽는 흐름을 방해하는 로딩 표시를 억제합니다. 목록 필터와 빠른 실행의 기록 검색은 **현재 불러온 작업만** 대상으로 하며, 이전 기록은 더 불러올 수 있습니다. 앱 열기·빠른 실행 열기·필터링은 모델을 시작하지 않습니다.

이번 탐색 변경은 [공식 브랜드 사례 조사](docs/desktop-ux-references.md)와 [재적용 계획](docs/desktop-ux-plan.md)에 근거합니다. 작업표시줄에서는 밝은 lime 타일의 JEV 아이콘으로 구분합니다.

앱은 이 프로젝트의 기존 Python 서비스 **0.2.0**, `.local` 모델과 작업 DB를 사용합니다. 앱 폴더만 다른 PC에 복사해서 실행하는 구성은 아닙니다. [데스크톱 앱 실행 안내](docs/desktop-manager.md)에 프로젝트 선택, 모델 수명, 연결 점검의 범위와 빌드 방법을 정리했습니다.

[모델 수명 재설계](docs/model-lifecycle-design.md): 연결만으로 모델을 시작하지 않고, 여러 MCP가 하나의 실행기를 공유하며, 마지막 판단 후 유휴 상태에서 모델을 해제

[시작 지연 개선](docs/model-startup-performance.md): 같은 모델의 0 텐서 표현을 바꾸어 GPU 캐시 오류를 피하고, 캐시 재시작 준비를 약 4분에서 약 20초로 단축한 실측과 적용 상태
[수명 관리 적용 검증](docs/model-lifecycle-validation.md): 다중 MCP·비정상 종료·유휴 해제와 실제 설치 결과

[2026-09-26 프로젝트 이동 복구와 실사용 검증](docs/recovery-validation.md): 기존 기록 보존, 상대 경로 설정, 실제 MCP·로컬 판단·서버 재시작 확인
현재 작업 폴더의 판단 엔진은 SemIf OpenVINO의 로컬 GPU shadow 구성, 이전 OpenJev Modal 실험 기록과 구분

[통합 설계 2.0](docs/design/unified-design.md): 세 원문의 공통점·상충점 검토와 로컬 판단·문맥 조정기의 새 제품 범위
Python 서비스 0.2.0은 로컬·Modal 모델 판단과 후보 추천·검증 기록까지 연결한 개발 버전
[구현과 실행 안내](docs/implementation-v2.md)에서 실제 모델 준비·계약 2.0·검증 범위 확인

한국어 코딩 작업의 근거를 모델로 판단하고 도구·스킬 후보와 완료 검증 기록을 연결하는 문맥 조정기
Python·SQLite로 동작하며 Codex에는 MCP 표준 입출력과 Skill로 연결
원문·제약·반대 근거를 보호하고, 검증된 모델만 선택을 변경하도록 구성
실제 Laya CPU 추론과 MCP 10개 도구 호출, [OpenJev Modal의 작업별 세션과 실제 문맥 연결](docs/openjev-session.md) 확인
[판단 개선 후 비교](docs/judgment-revision.md)에서 기존 50문항 50개·새 44문항 39개 정답, 남은 오류와 실제 작업 효율 미검증으로 shadow 유지
[이전 Modal 구성의 MCP 직접 호출 검증](docs/native-mcp-validation.md)으로 저장·복원·검색·기록·Modal 판단까지 확인
후속 지시문 후보는 중요한 오판 회귀로 미반영, 독립 품질 평가와 실제 코딩 작업 효율은 남은 제품 완료 조건

예를 들어 “설계만 작성” 작업을 저장한 뒤 서버를 종료해도 같은 work ID로 목적과 제약 복원
“권한 refreshToken()”을 검색하면 등록된 문서의 원문·행·revision을 반환하고 알려진 충돌도 함께 보존
실행 가능한 예제는 아래 `demo` 명령

## 저장·검색 기반 확인

지원·검증 환경은 Windows x64와 Python 3.12.14
서비스 의존성은 `uv.lock`으로 고정, 아래 비교용 저장·검색 예제는 모델 없이 실행

프로젝트 폴더의 PowerShell에서 실행
이미 구성된 이 작업 폴더에서는 다음 명령으로 저장 → 서버 객체 재시작 → 한국어 검색 확인

```powershell
.\.venv\Scripts\python.exe -X utf8 -m jev_context demo
```

성공 결과의 `restored_goal`은 `설계 문서 완성`, `context.outcome`은 `ok`, `judgment.status`는 `skipped`
예제 데이터는 임시 폴더에서 생성·정리

새 환경에서는 Python 3.12.14와 uv 0.12.17 준비 후 실행

```powershell
uv sync --locked --python C:\Path\To\Python312\python.exe --no-python-downloads
uv lock --check --python C:\Path\To\Python312\python.exe --no-python-downloads
uv run --no-sync jev-context demo
```

위 Python 경로는 설치된 실행기의 실제 경로로 교체
개발용 공통 명령과 현재 환경의 예외는 [개발 환경](docs/development.md) 참조

## 프로젝트 연결

프로젝트 루트·저장 위치·수집 허용 경로를 직접 지정
다음 예시는 문서 폴더의 Markdown만 허용하는 설정 생성이며 자동 수집 없음

```powershell
.\.venv\Scripts\python.exe -m jev_context init --config .local\project.toml --project-root . --data-root .local\state --allow "docs/*.md"
.\.venv\Scripts\python.exe -m jev_context status --config .local\project.toml
.\.venv\Scripts\python.exe -m jev_context codex-config --config .local\project.toml
```

마지막 명령으로 생성한 설정과 [Codex 연결 안내](docs/codex-setup.md)를 사용해 연결
`skills/jev-context`에는 복원·검색·기록 흐름을 안내하는 설치용 Skill 포함
설정 경로를 지정하지 않은 홈 디렉터리나 대화 이력은 수집 대상에서 제외

## 제공 기능과 확인 범위

- 작업 생성·복원, 결정·범위·미해결 충돌·실행 보고 기록
- 한국어 부분 문자열·FTS5·경로 검색과 원문 revision 조회
- 필수 제약·충돌 보존, 바이트 예산 부족과 오래된 원문 표시
- 동시 수정 감지, mutation 중복 방지, 강제 종료 후 수집 재개
- 삭제 후 관련 원문·인덱스·기록 인용·문맥·replay 차단
- 근거별 로컬 판단·필수 자료 보호·현재 도구와 스킬 후보 추천
- 읽기 작업 전달 준비·revision별 완료 기준과 검증 기록 연결

현재 프로젝트는 계약 2.0과 로컬 SemIf OpenVINO shadow 구성, 새 `init`은 1.0 저장·검색 기본 설정 유지
Modal은 사용자가 시작한 작업 세션에서만 사용하고 준비된 모델 캐시 재사용
새 [0.2.0 검증 범위](docs/implementation-v2.md)와 과거 [0.1.0 검증 기록](docs/verification.md)을 구분

## 상세 자료

- [도구 계약](docs/design/tool-contracts.md)과 [canonical JSON Schema](src/jev_context/contracts.json)
- [명시적으로 선택하는 계약 2.0](src/jev_context/contracts_v2.py)
- [현재 구현의 설계 보완](docs/implementation-notes.md)
- [모델 준비와 제한](docs/models.md)
- [판단 모델 비교 진단](docs/model-comparison.md): 같은 한국어 30문항에서 OpenJev Modal·Laya·AgentJev-0.6B 비교
- [호스트 기능표(V0)](docs/host-capabilities.md): Desktop MCP·Skill·hooks의 문서·노출·시험 상태와 설계 hook 구성
- [원본 상세 설계](docs/design/detailed-design.md)
