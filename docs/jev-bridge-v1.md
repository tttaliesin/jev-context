# Workroom ↔ Jev MCP bridge v1

계약 식별자: `workroom-jev/1`. Transport: 로컬 MCP stdio, 도구 응답 content의 단일 JSON text. 오류는 MCP isError로 반환한다. 계약 변경은 양쪽 저장소에서 검토하며 다른 major는 거부한다.

## 실행 설명 파일

사용자가 Jev에서 만든 JSON을 Workroom에서 선택한다.

```json
{"contract":"workroom-jev/1","command":"C:/.../python.exe","args":["-m","jev_context","serve","--config","C:/.../project.toml"],"cwd":"C:/.../project"}
```

실제 args는 Jev CLI 구현에 맞춰 생성한다. `command`와 `cwd`는 절대 경로, args는 문자열 배열. shell·env·자동 설치 없음. 파일 내용은 실행 명령이므로 앱에서 명령을 보여주고 사용자가 연결 검사를 누른 때만 실행한다. Workroom은 선택한 JSON의 내용을 저장하며 변경된 파일을 자동 재해석하지 않는다.

## bridge_status

입력 `{ "contract": "workroom-jev/1" }`.
출력 `{ "contract": "workroom-jev/1", "workspaceId": "stable-id", "workspaceRoot": "absolute project root", "capabilities": ["publish", "search"] }`.
Workroom은 등록 제품 폴더와 workspaceRoot가 일치해야 연결을 사용한다. 실행기는 각 호출 뒤 종료하며 timeout과 출력 크기 제한을 둔다. 모델을 시작하지 않는다.

## bridge_publish

입력 `{contract, originId, productId, reportId, revision, title, summary, contribution, limitations}`.

- originId: Workroom DB별 영속 UUID. productId/reportId: UUID. revision: 양의 정수.
- title 최대 200자, summary 8000자, contribution 3000자, limitations 4000자.
- Workroom은 kind=work 결과만 원본으로 삼고 전송 직전 revision과 설정 버전을 재확인한다.
- 사용자 확인 필드만 전송한다. evidence 원문, 파일 목록, 검사 로그, 비공개 포트폴리오 강조점, 계정 정보는 전송하지 않는다.
- 키는 `(originId, productId, reportId)`이며 동일 revision·동일 내용은 같은 결과 반환. 더 낮은 revision 또는 동일 revision·다른 내용은 오류. 높은 revision은 같은 기억 갱신. 외부 데이터는 reported이며 검증·승인 권한 없음.
- 출력 `{contract, status:"created"|"updated"|"unchanged", memoryId, revision}`.

## bridge_search

입력 `{contract, originId, productId, query, limit}`. query 최대 500자, limit 1~8.
출력 `{contract, items:[{memoryId, reportId, revision, title, summary, contribution, limitations}], truncated:boolean}`.

v1은 해당 originId/productId로 가져온 기억만 검색한다. 다른 제품이나 독립 Jev 작업은 섞지 않는다. 모델 없이 실행한다. 결과를 Workroom DB의 원본 작업이나 지식으로 역수입하지 않는다. 조회 시 revision·출처를 표시하며 실행 권한으로 사용하지 않는다.

## 실패·재시도

실패하면 성공 이력을 남기지 않는다. 응답 유실 후 재전송은 같은 원본 ID·revision으로 처리한다. 계약·프로젝트 불일치면 publish/search를 호출하지 않는다. 연결 해제와 설정 변경 후 오래된 미리보기는 전송할 수 없다. 자동 재시도·숨은 기존 검색 fallback 없음. 오류 시 독립 Workroom 기능은 유지한다.
