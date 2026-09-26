# Jev Context

Codex로 한국어 코딩 작업을 이어 갈 때 **작업의 목표·제약·근거를 잃지 않게** 하는 로컬 문맥 조정기입니다.

- 작업을 저장하고, 서버를 다시 시작해도 같은 work ID로 목표와 제약을 복원합니다.
- 등록한 문서를 한국어로 검색해 원문·행 위치·revision을 그대로 돌려줍니다.
- 필수 제약, 알려진 충돌, 실패와 반대 근거는 예산이 부족해도 버리지 않습니다.
- 선택적으로 로컬 모델이 근거의 관련성과 도구·스킬 후보를 판단합니다.

Python·SQLite로 동작하며 Codex에는 MCP 표준 입출력 서버와 Skill로 연결합니다.

## 현재 상태

| 구성 요소 | 버전 | 상태 |
|---|---|---|
| Python 서비스와 MCP 서버 | 0.2.0 | 개발 버전. 계약 1.0(기본)과 2.0(명시 선택) 제공 |
| 데스크톱 관리 앱 (Electron) | 0.3.4 | 이 PC의 프로젝트 설정과 모델을 사용하는 관리 도구 |
| 모델 판단 | — | **shadow**: 판단을 기록하지만 근거 선택은 바꾸지 않음 |

모델 판단을 선택에 반영(active)하려면 사람이 검토한 heldout 평가를 통과해야 하며, 아직 통과한 프로필은 없습니다. 현재 구성은 SemIf OpenVINO(Qwen3.5-4B INT8, 로컬 GPU)이고, 캐시가 있으면 약 20초 안에 준비됩니다 ([시작 지연 개선](docs/model-startup-performance.md)). 이전 OpenJev Modal 구성의 [판단 개선 후 비교](docs/judgment-revision.md)에서는 기존 50문항 중 50개, 새 44문항 중 39개가 정답이었습니다. 독립 품질 평가와 실제 코딩 작업 효율은 아직 검증하지 않았습니다. 자세한 내용은 [모델 준비와 제한](docs/models.md)에 있습니다.

## 빠른 시작

지원·검증 환경은 **Windows x64, Python 3.12.14, uv 0.12.17**입니다. 의존성은 `uv.lock`으로 고정돼 있습니다.

```powershell
uv sync --locked --python C:\Path\To\Python312\python.exe --no-python-downloads
uv run --no-sync jev-context demo
```

`demo`는 모델 없이 임시 폴더에서 작업 저장 → 서버 재시작 → 한국어 검색을 실행합니다. 성공하면 `restored_goal`이 `설계 문서 완성`, `context.outcome`이 `ok`, `judgment.status`가 `skipped`입니다.

### 프로젝트에 연결하기

프로젝트 루트, 저장 위치, 수집을 허용할 경로를 직접 지정합니다. 아래 예시는 `docs`의 Markdown만 허용하며, 설정만 만들고 자동으로 수집하지는 않습니다.

```powershell
.\.venv\Scripts\python.exe -m jev_context init --config .local\project.toml --project-root . --data-root .local\state --allow "docs/*.md"
.\.venv\Scripts\python.exe -m jev_context status --config .local\project.toml
.\.venv\Scripts\python.exe -m jev_context codex-config --config .local\project.toml
```

마지막 명령이 출력한 설정으로 Codex에 MCP 서버를 등록합니다. 절차는 [Codex 연결 안내](docs/codex-setup.md)에 있습니다. 허용 경로에 없는 파일, 홈 디렉터리, 대화 이력은 수집하지 않습니다.

## MCP 도구

| 도구 | 하는 일 |
|---|---|
| `workspace_status` | 프로젝트와 작업 목록, 수집 및 모델 상태 조회 |
| `work_open` | 저장한 작업 복원 또는 새 작업 생성 |
| `work_record` | 목표 범위·결정·근거·진행 기록 (revision 충돌 감지) |
| `work_inspect` | 작업의 이력·결정·충돌·근거·중복 요청 기록 조회 |
| `source_sync` | 허용된 파일 또는 출처가 있는 발췌 등록 |
| `source_read` | 고정 revision의 원문을 행 단위로 조회 |
| `context_prepare` | 한국어 근거 검색과 제약·충돌을 보존하는 문맥 구성 |
| `data_forget` | 요청한 source 또는 work를 논리 삭제 (원본 파일은 유지) |
| `capability_recommend` | 도구·스킬·worker 후보 추천, 계약 2.0 전용. 권한 부여나 실행은 하지 않음 |
| `handoff_prepare` | 읽기 작업 전달 묶음 준비, 계약 2.0 전용. 실행은 호스트가 담당 |

입력·출력 계약은 [contracts.json](src/jev_context/contracts.json)(1.0)과 [contracts_v2.py](src/jev_context/contracts_v2.py)(2.0)가 기준입니다. MCP가 노출되지 않은 환경에서는 같은 계약을 CLI로 호출할 수 있습니다 (`jev-context schema`, `jev-context call`). 사용 흐름은 [Skill](skills/jev-context/SKILL.md)에 정리돼 있습니다.

## 데스크톱 관리 앱

`dist\JevContext\JevContext.exe`를 실행하면 로컬 모델 준비·종료, 연결 점검, 저장된 작업 조회를 할 수 있습니다. 앱을 열거나 작업을 찾는 것만으로는 모델을 시작하지 않습니다. 코딩 대화는 Codex에서 계속합니다.

| 단축키 | 기능 |
|---|---|
| `Ctrl+K` | 빠른 실행: 명령과 불러온 작업 찾기 |
| `Ctrl+F` | 작업 목록의 제목·목표 필터 |
| `Ctrl+B` | 작업 목록 숨기기 |
| `F1` | 단축키 안내 |

앱은 이 프로젝트의 `.venv`, `.local`의 모델과 작업 DB를 사용하므로, 앱 폴더만 다른 PC로 복사해서는 실행되지 않습니다. 빌드는 `powershell.exe -NoProfile -File scripts/build_desktop.ps1`이며, 자세한 내용은 [데스크톱 앱 실행 안내](docs/desktop-manager.md)와 [디자인 기준](DESIGN.md)에 있습니다.

## 개발

```powershell
.\.venv\Scripts\python.exe scripts\check.py
```

Ruff lint·format 검사와 pytest를 순서대로 실행하고 첫 실패의 종료 코드를 돌려줍니다. `mise run check`는 여기에 lock 확인과 build를 더합니다. 모델 어댑터 테스트는 모의 worker로 계약을 검사하며 모델 정확도와는 별개입니다. 환경 구성은 [개발 환경](docs/development.md)에 있습니다.

## 문서

**현재 기준**

- [통합 설계 2.0](docs/design/unified-design.md): 제품 방향·범위·완료 기준의 단일 기준
- [구현 0.2.0과 검증 범위](docs/implementation-v2.md)
- [모델 준비와 제한](docs/models.md)
- [여러 MCP 연결에서의 모델 수명](docs/model-lifecycle-design.md): 연결만으로 모델을 시작하지 않고, 여러 MCP가 실행기 하나를 공유하며, 유휴 시 해제
- [호스트 기능표](docs/host-capabilities.md): Codex Desktop의 MCP·Skill·hooks 지원 상태
- [Codex 연결 안내](docs/codex-setup.md), [개발 환경](docs/development.md), [데스크톱 앱 실행 안내](docs/desktop-manager.md)

<details>
<summary><b>검증·실험 기록</b> (날짜별, 작성 당시 기준)</summary>

- 2026-09-26: [프로젝트 이동 복구와 실사용 검증](docs/recovery-validation.md) ([장애 기록](docs/recovery-case.md)), [모델 수명 적용 검증](docs/model-lifecycle-validation.md) ([원문](docs/model-lifecycle-case.md)), [모델 시작 지연 개선](docs/model-startup-performance.md), [작업 재개 비교 계획](docs/resume-evaluation-plan.md)·[결과](docs/resume-evaluation-results.md)
- 2026-09-26 데스크톱 UX: [디자인 레퍼런스 조사](docs/design-reference-research.md), [브랜드 사례 조사](docs/desktop-ux-references.md), [재적용 계획](docs/desktop-ux-plan.md)
- 2026-09-24: [PostToolUse hook 비교 계획](docs/hook-benchmark-plan.md) (중단된 실험)
- 2026-09-23: [판단 모델 비교 진단](docs/model-comparison.md)
- 2026-09-22: [판단 개선 후 비교](docs/judgment-revision.md), [현재 대화의 MCP 연결 검증](docs/native-mcp-validation.md), [OpenJev 작업별 Modal 연결](docs/openjev-session.md), [OpenJev Modal 진단](docs/openjev-modal-evaluation.md), [영어 번역 입력 비교](docs/translation-evaluation.md), [독립 코드 수정 비교 계획](docs/coding-benchmark-plan.md)·[결과](docs/coding-benchmark-results.md), [0.2.0 검증 기록](docs/verification-v2.md), [0.1.0 검증 기록](docs/verification.md), [0.1.0 설계 보완](docs/implementation-notes.md)

</details>

<details>
<summary><b>이전 설계 1.0</b> (통합 설계 2.0으로 대체됨)</summary>

- [설계안 0.4](docs/design/proposal.md), [상세 설계 1.0](docs/design/detailed-design.md), [설계 검토](docs/design/design-review.md)
- [도구 인터페이스 계약](docs/design/tool-contracts.md), [저장 구조와 처리 순서](docs/design/storage-and-flows.md)
- [검증 설계와 합격 기준](docs/design/acceptance.md), [평가 사례 30개](docs/design/pilot-cases.md)
- [로컬 판단 엔진 비교](docs/design/local-models.md), [PDF와 X 글 비교](docs/design/source-comparison.md)

</details>

## 라이선스

[MIT](LICENSE). 모델 가중치와 Electron 런타임은 저장소에 포함되지 않으며 각자의 라이선스를 따릅니다.
