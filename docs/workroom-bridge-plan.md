# Jev의 독립 제품 연동 적용 계획

2026-09-27. [독립 제품 원칙](independent-products.md)과 [workroom-jev/1](jev-bridge-v1.md)을 따른다. Workroom 코드는 읽기만 하고 Jev 저장소만 수정한다.

1. 계약을 그대로 구현한다. 별도 입력 schema와 단일 JSON text 응답, MCP isError 오류를 사용한다. 기존 Jev 계약 1.0/2.0 응답 형식은 바꾸지 않는다.
2. 프로젝트별 별도 `workroom-bridge.sqlite`에 reported 파생 기억을 저장한다. origin/product/report UUID 키와 revision, 내용 hash로 원자적 upsert한다. 역순 및 동일 revision의 내용 변경을 거부한다. 원래 작업 DB·검색·승인 상태와 교차 갱신하지 않는다.
3. status와 search는 모델 없이 읽기만 한다. 검색은 origin/product 범위의 title/summary/contribution/limitations를 대상으로 한다. 검색 결과의 값은 사용자 자료로 취급하고 실행하지 않는다.
4. `bridge-config --config ...`로 env 없는 절대 실행 설명 JSON을 생성한다. 격리된 bridge 전용 진입점은 모델 옵션 없이 3개 도구만 제공한다. 앱에도 파일 내보내기를 추가하고 한영 UI를 유지한다.
5. schema, UUID/크기 검증, 중복·역순·내용 충돌, 동시에 같은 ID 전송, 범위 격리, read-only, 재시작, 모델 미기동, 일반 Jev 작업 불변을 검사한다. 실제 MCP 클라이언트로 env 없는 설명 파일을 실행한다.
6. Workroom이 실제 소비자로 검사할 수 있는 격리 예제 생성 명령과 결과를 [진행·결과](workroom-bridge-results.md)에 기록한다. 전체 회귀·패키징 후 커밋하고 Jev 원격에만 푸시한다.

초기 계약은 동기 삭제·자동 전송·자동 문맥 공급을 포함하지 않는다. 연결 해제는 삭제가 아니며 외부 자료의 저장이 완료·승인을 의미하지 않는다. 프로젝트 경로 일치는 소비자가 status를 확인한다. UUID 범위는 데이터 분리 기준이며 악의적인 로컬 프로세스에 대한 신원 인증은 아니다.
