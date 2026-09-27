# Codex 연결

## 앱에서 연결하기

관리 앱 0.4.0의 **Codex 연결**에서 프로젝트 폴더 선택, 새 설정과 작업 DB 생성, MCP·Skill 미리보기와 설치, 통신 점검, 확인 문구 복사, 설치 되돌리기를 진행할 수 있다. 처음 사용하는 경우 [앱의 단계별 안내](desktop-manager.md#처음-연결하기--040)를 권장한다.

Codex에서 같은 프로젝트를 열고 신뢰 여부를 확인하는 단계와 실제 확인 문구 전달은 사용자가 한다. 앱이 Codex의 계정·신뢰·승인 정책을 바꾸거나 대화를 자동 전송하지 않는다. 서버 응답만으로 Codex 연결을 완료 처리하지 않는다.

## CLI로 연결하기

목표는 프로젝트 하나에 고정된 로컬 MCP 서버 연결
공식 근거는 [Codex MCP 설정](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)과 [Codex Skills](https://developers.openai.com/codex/skills)
실행기와 설정 파일의 절대 경로는 `jev-context codex-config` 결과로 생성

이 소스 프로젝트에서 검토된 설정과 Skill을 함께 설치할 때는 다음 명령 사용
기존의 다른 설정을 보존하고 같은 이름의 내용이 다르면 덮어쓰지 않고 중지
검토한 0.2.0 설정과 Skill로 갱신할 때는 `--update` 사용, 이전 두 파일은 `.local/install-backups`에 보관

```powershell
.\.venv\Scripts\python.exe scripts\install_codex.py --config .local\project.toml
```

## 연결 순서

1. [README의 프로젝트 설정](../README.md)으로 허용 경로와 저장 위치 지정
2. `jev-context codex-config --config .local/project.toml`로 MCP 설정 출력
3. 기존 항목을 보존한 채 Codex의 프로젝트 `.codex/config.toml` 또는 MCP 설정 화면에 출력 항목 추가
4. 프로젝트 Skill 위치 `.agents/skills/jev-context/SKILL.md`에 [제공 Skill](../skills/jev-context/SKILL.md) 설치
5. MCP 설정을 다시 읽는 새 세션에서 상태 조회와 작업 저장·복원 확인

새 프로젝트 설정 생성은 데이터 수집과 별개
`allowed_paths`가 빈 배열이면 파일 수집은 모두 거절하고 출처 있는 발췌만 허용
허용 경로 변경 시 `policy_revision`을 현재 값보다 올린 후 서버 재시작
기존 프로세스의 정책이 오래되면 업무 호출을 거절하는 동작

실행 프로세스에는 해당 프로젝트와 데이터 폴더의 읽기·쓰기 권한 필요
현재 대화의 도구 목록이 자동 갱신된다고 가정하지 않는 기준

현재 대화에 MCP 도구가 노출되지 않은 경우 이 소스 프로젝트에서 같은 계약의 `schema`·`call` CLI 사용 가능
입력 파일·판단 세션 연결·오류 코드와 실제 사용 결과는 [현재 대화 호출 경로](judgment-revision.md#현재-대화에서-호출) 참조
CLI 호출 성공과 Desktop 직접 MCP 도구 발견은 별개로 확인

## 첫 확인 요청

> jev_context 연결 상태를 확인하고, “설계만 작성”이라는 제약으로 내부 작업을 저장해줘. work ID를 알려줘.

다음 요청에서 저장된 ID로 복원

> 방금 저장한 work ID로 작업을 복원하고 제약을 알려줘.

확인 기준은 `work_open` 성공, 같은 work ID·revision·제약 반환, provenance `agent_reported`
도구 호출 없이 대화 문장만으로 기억 복원이 성공했다고 판단하지 않는 기준

## 연결 실패 시

`status` 명령 실패는 프로젝트 설정·DB 접근 오류부터 확인
MCP 실행기 경로가 `.venv/Scripts/python.exe`인지, 인자의 설정 경로가 절대 경로인지 확인
`busy`는 같은 mutation으로 제한 재시도, `revision_conflict`는 현재 상태 재조회 후 변경 재구성

기존 1.0 통합 테스트는 공식 MCP Python 클라이언트의 initialize·tools/list·8개 도구 호출·프로세스 재시작까지 실행
0.2.0은 별도 [실제 모델·MCP 10개 도구 통합 검사](implementation-v2.md) 제공
OpenJev Modal은 [작업 세션 시작·상태·종료 절차](openjev-session.md)를 사용하고 MCP 자체는 준비된 세션에만 연결
Desktop UI 세션의 도구 발견·Skill 자동 선택·실제 사용 요청 흐름은 별도 검증 항목

2026-09-22 후속 [현재 대화 MCP 검증](native-mcp-validation.md)에서 도구 발견·직접 작업 복원·검색·변경 기록 확인
Windows에서는 MCP와 DB를 여는 CLI의 OS 계정을 일치시키고, 기존 DB 소유자가 다르면 해당 DB 디렉터리만 명시적으로 이전
현재 연결된 프로젝트는 로그인 계정 소유 DB를 사용하며 샌드박스 CLI로 DB 권한을 다시 설정하지 않는 구성
