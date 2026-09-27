# 개발 환경

Python 패키지·SQLite·MCP 서버로 된 단일 로컬 구현입니다. 저장소는 [tttaliesin/jev-context](https://github.com/tttaliesin/jev-context)입니다.

## 도구와 버전

| 항목 | 기준 |
|---|---|
| 지원 환경 | Windows x64 |
| Python | 3.12.14 (`mise.toml`) |
| 의존성·venv | uv 0.12.17, `pyproject.toml`, `uv.lock` |
| 검사 | Ruff(lint·format), pytest, 순차 실행 스크립트 `scripts/check.py` |
| 데스크톱 앱 | Electron 44.4.5 (`desktop/runtime.json`에 SHA-256 고정) |

`mise.toml`은 development-tooling 템플릿 1.0.0의 Python overlay를 Windows용으로 바꾼 것입니다. mise가 Python·uv 버전을 고르고, 의존성 설치와 lock은 uv가 관리합니다.

## 설치

```powershell
uv sync --locked
```

mise 없이 설치할 때는 `--python`으로 Python 3.12 실행 파일을 지정하고 `--no-python-downloads`를 붙입니다. 명령은 [README](../README.md#빠른-시작)에 있습니다.

## 검사

| 명령 | 하는 일 |
|---|---|
| `mise run check` | lock 확인 → lint·format·pytest → offline build |
| `mise run lint` / `mise run test` | lock 확인 후 lint 또는 pytest만 실행 |
| `.\.venv\Scripts\python.exe scripts\check.py` | mise·uv 없이 lint·format·pytest 실행 |

검사 명령은 의존성을 설치하거나 lock을 바꾸지 않고, 첫 실패의 종료 코드를 그대로 돌려줍니다. build 도구(hatchling)는 dev 의존성에 고정돼 있어 offline build 중 추가로 내려받지 않습니다.

GitHub Actions([check.yml](../.github/workflows/check.yml))는 `master` push와 pull request마다 Windows 러너에서 같은 순서로 검사하고, 창 상태·언어·프로젝트 전환·bridge 복구 Node 테스트도 실행합니다.

pytest는 약 250개 테스트를 1분 20초 정도에 실행합니다. `test_coding_benchmark.py`는 격리된 pytest를 프로젝트의 `.t/` 아래에서 실행하고, 끝나면 그 임시 폴더를 지웁니다.

샌드박스 계정 등 **다른 Windows 계정으로 테스트를 실행하면**, 저장 폴더에 거는 보호 권한(`storage.protect_directory`) 때문에 원래 사용자가 지울 수 없는 임시 폴더가 남을 수 있습니다. 이런 폴더는 관리자 권한으로 소유권을 가져온 뒤 지워야 합니다. pytest의 기본 임시 폴더 `%TEMP%\pytest-of-<사용자>`가 이렇게 잠기면 모든 테스트가 `PermissionError`로 실패하므로, 그 폴더를 지우거나 `--basetemp`로 다른 위치를 지정합니다.

### 데스크톱 앱 검사

Python 검사에는 포함되지 않습니다. Node가 필요합니다.

- `node --test scripts\test_window_state.cjs`: 창 위치·크기 저장 로직. Node만 있으면 실행됩니다.
- `node --test scripts\test_locales.cjs scripts\test_project_switch.cjs scripts\test_bridge_client.cjs scripts\test_diagnostics.cjs`: 번역, 설정 쓰기/rename 실패, Python 시작 실패와 재시도, 이전 프로세스의 늦은 이벤트, 재연결과 진단 정보 필드 제한을 검사합니다. Node만 있으면 실행됩니다.
- `node scripts\test_desktop_language.cjs`: 실제 Electron에서 한영 전환·연결 안내·작업 원문 보존·진단 복사·앱 재연결·재실행 후 설정 유지를 검사합니다. 임시 프로젝트를 쓰고 모델은 시작하지 않습니다.
- `node scripts\test_desktop_recovery.cjs`: 격리 프로젝트와 userData로 실제 Electron의 프로젝트 선택·온보딩 저장 실패, 재시도, 재실행 후 선택 유지, 실제 Python 연결 복구를 검사합니다. 모델을 시작하지 않습니다.
- `scripts\test_desktop.cjs`, `test_desktop_ux.cjs`: 빌드된 `dist\JevContext`를 Playwright로 실제 실행하는 화면 검사입니다.
- `scripts\test_desktop_states.cjs`: 실제 렌더러를 fixture로 띄워 상태별 화면을 검사합니다. Electron main, Python, DB, 모델은 쓰지 않습니다.

Playwright는 저장소 의존성이 아니므로 `NODE_PATH`로 설치 위치를 지정합니다. 빌드는 `powershell.exe -NoProfile -File scripts\build_desktop.ps1`입니다 ([데스크톱 앱 실행 안내](desktop-manager.md)).

## 모델 환경

핵심 서비스는 모델 라이브러리에 의존하지 않습니다. 판단 엔진은 각각 별도 Python 환경에서 실행합니다. 로컬 엔진은 프로필 JSON의 `python`·`model_path`로 worker 프로세스를 띄웁니다 ([모델 준비와 제한](models.md)).

| 엔진 | 고정 의존성 | 비고 |
|---|---|---|
| SemIf OpenVINO (현재 구성) | `models/semif-requirements.txt` | 로컬 GPU. 실행과 IR 변환을 같은 환경에서 수행 |
| Laya | `models/laya-requirements.txt` | CPU 추론 |
| OpenJev Modal | `models/modal-requirements.txt` | 원격 GPU, 명시적 세션에서만 사용 |

SemIf 환경은 다음과 같이 만듭니다. 모델 가중치와 OpenVINO IR 변환은 별도 단계입니다.

```powershell
uv venv .local\bake-venv --python 3.12
uv pip sync --python .local\bake-venv\Scripts\python.exe --require-hashes --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cpu models\semif-requirements.txt
```

모델 어댑터 테스트는 모의 worker와 모의 서버로 프로세스·통신 계약을 검사합니다. 모델의 판단 정확도와는 별개입니다.

## 로컬 전용 파일

아래 파일과 폴더는 Git에 포함되지 않습니다 (`.gitignore`).

- `.local/`: 프로젝트 설정(`project.toml`), 작업 DB, 모델 가중치와 캐시, 모델용 venv, 벤치마크 결과
- `.t/`: 테스트와 벤치마크의 임시 폴더
- `dist/`: 데스크톱 앱 빌드 결과
- `.codex/config.toml`, `.codex/hooks.json`: 이 PC의 절대 경로가 들어간 Codex 설정
