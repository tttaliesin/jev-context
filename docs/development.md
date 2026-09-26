# 개발 환경

Python 패키지·SQLite·MCP 서버의 단일 로컬 구현
최종 납품 위치는 현재 프로젝트 폴더, GitHub push·PR·Issue는 요청 범위에 미포함
작업 시작 시 기존 코드·Git 이력·AGENTS.md가 없는 빈 폴더로 확인

## 도구 소유권

- runtime 선택: `mise.toml`의 Python 3.12.14
- 의존성·venv: uv 0.12.17, `pyproject.toml`, `uv.lock`
- 공통 진입점: `mise run sync`, `mise run check`, `mise run demo`
- 검사 구현: Ruff·pytest와 순차 실행 `scripts/check.py`

development-tooling 템플릿 1.0.0의 Python overlay를 렌더한 뒤 Windows용으로 POSIX 셸 검사를 교체
현재 호스트에는 Python·uv·mise가 PATH에 없어 Codex 번들 Python과 프로젝트 내부 uv 실행기로 bootstrap
초기 venv 생성 이후 의존성의 실제 설치·lock은 uv로 관리
별도 Python runtime 다운로드나 전역 도구 변경 없음
mise 선언은 정적 검사, Windows의 실제 검사·build는 아래 동등 명령으로 실행

## 현재 작업 폴더에서 검증

```powershell
.\.local\tools\uv.exe lock --check --python .venv\Scripts\python.exe --no-python-downloads --cache-dir .uv-cache
.\.local\tools\uv.exe run --no-sync python scripts/check.py
.\.local\tools\uv.exe build --no-build-isolation --offline
```

검사 진입점은 의존성을 설치하거나 lock을 변경하지 않고 첫 실패의 종료 코드를 전달
build 도구는 dev 의존성에 고정해 offline build에서 임의 추가 다운로드를 막는 구성
`mise.lock`과 mise runtime 설치 경로는 현재 호스트에서 미검증이므로 완전한 mise 채택 완료로 보고하지 않는 범위

## 재현 가능한 경계

공식 MCP SDK 1.30.0과 JSON Schema validator 사용
핵심 코드의 모델 의존성은 없으며 Laya SDK·PyTorch·transformers는 별도 환경에서만 준비
모델 어댑터 테스트는 모의 서버 응답과 자식 프로세스 계약을 검사하며 모델 정확도와 구분

명령 실행 후 [검증 기록](verification.md)의 실제 결과와 미검증 범위 확인
새로운 오류가 없는 동일 검사의 반복 대신 변경한 경계의 회귀 사례부터 실행
