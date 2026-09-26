# 프로젝트 이동 후 연결 장애: 2026-09-26 관측

실제 프로젝트를 옮긴 뒤 발생한 장애의 복구 전 기록이다. 이후 복구 결과와 구분한다.
현재 폴더는 `C:\Users\tttal\workspace\project\tttaliesin\jev-for-agent`다.
이전 폴더는 `C:\Users\tttal\workspace\jev-for-agent`이며 해당 경로를 열면 FileNotFoundError가 발생했다.
사용자는 연결 복구, 실제 작업의 저장·재개·검증, 기본 Codex/저장검색/판단 포함 비교를 진행하도록 요청했다.

# 프로젝트 이동과 Python·호스트 설정

복구 전 `.venv/Lib/site-packages/_editable_impl_jev_context.pth`는 이전 폴더의 `src`를 가리켰다.
`python -m jev_context schema`는 처음에 No module named jev_context로 실패했다.
PYTHONPATH를 현재 src로 지정하자 설정의 옛 project_root에서 FileNotFoundError가 발생했다.
`.codex/config.toml`의 Python과 config 인수, hooks.json의 두 명령에도 이전 폴더가 남아 있었다.
모듈 호출 성공과 현재 Desktop 대화의 MCP 도구 노출은 각각 확인해야 한다.

# 프로젝트 이동과 기존 작업 기록

DB에는 project_id `project-89095613680449da8ab8c438dfcf826a`, schema 2, policy_revision 1이 저장돼 있었다.
DB root는 이전 폴더이며 data_revision 42, source_set_revision 28이었다.
기존 작업은 `work-7024f3eb77ca4c3a83c04517d76249d6` revision 13이고 quick_check는 ok였다.
storage.py는 DB의 project_id 또는 root가 설정과 다르면 거절한다.
프로젝트·작업·원문 ID와 기존 이력은 보존해야 한다. 일반 sandbox 계정은 기존 DB 폴더에 접근하지 못했다.

# 프로젝트 이동과 판단 모델

프로젝트 설정은 shadow이며 profile_file도 이전 폴더를 가리켰다.
프로필 안의 model_path, python, lock_root에 이전 경로가 남아 있었다.
현재 폴더에 기존 모델 파일과 실행 환경이 있으며 OpenVINO는 CPU·GPU·NPU 장치를 확인했다.
모델 다운로드나 원격 GPU 생성은 이번 복구에 필요하다는 근거가 없다.
모델 프로세스 시작, 실제 observed 판단, 자동 선별 활성화는 다른 상태다.
