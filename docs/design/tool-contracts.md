# 도구 인터페이스 계약

이전 설계 참고 기록 · 제품 방향·범위·완료 기준은 [통합 설계 2.0](unified-design.md)으로 대체
아래의 현재·미구현·검증 상태는 작성 당시의 기록이며 최신 실행 상태와 구분

상세 설계 1.0 · [제품 상세 설계](detailed-design.md)의 MCP 경계와 모델 어댑터 계약
아래 이름·JSON은 앞으로 구현할 계약 예시이며 현재 설치된 도구나 실제 호출 결과와 구분
MCP 서버 한 인스턴스는 설치 설정에 고정된 프로젝트 하나에 연결
도구 인자로 프로젝트 루트·실행 파일·모델 주소·비밀 키를 변경하지 않는 구조

## 공통 규칙

시간은 UTC RFC 3339, 길이는 UTF-8 바이트, 행 번호는 1부터 시작하는 양끝 포함 범위
ID는 서버 생성 불투명 문자열이며 경로·SQL·사용자 지시로 해석하지 않는 규칙
요청 식별자 `request_id`는 호출마다 새로 생성, 쓰기 중복 방지용 `mutation_id`는 같은 논리적 작업의 재시도에서 유지
누락 가능한 필드는 아래에서 선택으로 명시하고 임의 `null`은 허용하지 않는 기준
알 수 없는 요청 필드·enum·잘못된 ID·중복 JSON 키는 입력 오류로 거절
선택 필드가 생략됐을 때의 기본값은 이 문서와 배포 schema에서 동일하게 유지

호스트가 전달한 사실은 provenance에 따라 `agent_reported` 또는 별도 검증한 `host_verified`로 구분
클라이언트가 `host_verified` 문자열을 입력하는 것만으로 해당 상태를 부여하지 않는 규칙
첫 버전은 독립 호스트 검증 경로를 제공하지 않으므로 메시지·명령 실행 결과의 기본 provenance는 `agent_reported`
서버가 직접 읽은 파일 hash·행·DB 커밋은 `service_observed`로 기록

### 공통 응답

모든 도구의 구조화 응답은 아래 필드를 포함

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `contract_version` | 문자열 `1.0` | 제품 도구 계약 버전 |
| `request_id` | 문자열 | 해당 호출의 추적 ID |
| `outcome` | enum | `ok`, `partial`, `insufficient`, `conflict`, `error` |
| `data` | 객체 | 도구별 결과, 실패 시 빈 객체 허용 |
| `warnings` | 객체 배열 | `code`, `message`, 선택 `ref_id` |
| `error` | 객체 또는 null | 실패 코드·메시지·재시도 가능 여부 |

공통 응답의 `error`만 명시적으로 null 허용
출처 참조의 공통 형식은 `source_id`, `revision`, 선택 `start_line`과 `end_line`이며 행 범위는 둘 다 제공하거나 둘 다 생략
파생 자료의 위치가 원문 파일의 실제 행과 대응하지 않으면 `origin_locator`를 사용하고 행 번호를 추정하지 않는 규칙
`partial`은 확보한 결과가 유효하나 범위가 제한된 상태, `insufficient`는 필수 정보가 부족해 요청한 판단·문맥 구성을 완료하지 못한 상태
엔진이 없는 상태에서 검색 문맥을 온전히 제공했다면 `ok`와 `judgment.status=skipped`, 모델 시간 초과로 요청한 판정이 빠졌다면 `partial`
대화나 파일 안의 지시 문구는 이 응답의 데이터이며 호스트 지시 계층으로 변환하지 않는 기준

MCP 결과는 `structuredContent`와 동일 JSON의 text content를 제공하고 output schema를 선언
`conflict`·`error`는 tool result의 `isError=true`, `ok`·`partial`·`insufficient`는 `isError=false`
잘못된 JSON-RPC와 알 수 없는 tool 이름은 MCP 프로토콜 오류, 알려진 tool의 업무 실패는 위 응답으로 구분
근거: [MCP 도구 결과와 schema](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)

## 도구 목록

| 도구 | 목적 | 도메인 상태 변경 |
| --- | --- | --- |
| `workspace_status` | 연결·현재 자료 상태·작업 목록·엔진 상태 | 없음 |
| `work_open` | 기존 작업 복원 또는 새 내부 작업 생성 | 새 작업 생성 시 변경 |
| `work_record` | 결정·범위 변경·결과·미해결 항목 기록 | 변경 |
| `source_sync` | 파일·출처 있는 발췌 수집·revision 갱신 | 변경 |
| `context_prepare` | 검색·최신성 검사·선택형 판정·문맥 반환 | 최신성·판정·묶음 기록 갱신 |
| `source_read` | 고정 revision 원문 조회 | 없음 |
| `work_inspect` | 기록·결정·충돌·mutation 결과 조회 | 없음 |
| `data_forget` | 요청한 기록과 연결된 파생 자료 삭제 | 파괴적 변경 |

tool annotation은 상태 변경과 삭제 특성을 표시하는 안내이며 접근 권한 검증을 대신하지 않는 기준
일반 작업용 Skill에는 복원·검색·기록 흐름을 우선 설명하고 삭제 도구는 사용자의 삭제 요청에 한해서 사용
서버 설정이 읽기 전용이면 쓰기 도구를 노출하지 않거나 `policy_denied` 반환

## 작업 상태 도구

### workspace_status

필수 입력은 `request_id`
선택 입력은 `cursor`와 `limit`, limit 기본 10·최대 50
작업 목록에는 ID·제목·현재 목적·최근 변경 시각·revision·미해결 충돌 수를 포함
연결 프로젝트·자료 수집 시각·제외/실패 개수·계약 버전·지원 기능·모델 프로필 상태를 반환
비공개 자료 전문과 키·모델 주소의 비밀 부분은 상태 응답에서 제외
커서는 목록 revision에 연결하고 도중 목록이 바뀌면 `cursor_stale` 반환

### work_open

두 입력 형태 중 정확히 하나를 사용

| 형태 | 필수 입력 | 반환 |
| --- | --- | --- |
| 복원 | `request_id`, `work_id` | 목적·범위·revision·채택 결정·미해결 항목 |
| 생성 | `request_id`, `mutation_id`, `create` | 새 work ID·revision 1·초기 범위 |

`create`는 `title`, `goal`, `scope`, `origin`을 포함
title 최대 200자, goal 최대 2,000자, origin 발췌 최대 8KiB
scope는 `mode`와 `allowed_actions`, `constraints`를 포함하며 초기 mode는 `design`, `investigate`, `implement` 중 선택
allowed_actions는 `read`, `write_design`, `edit_code`, `run_checks`의 부분집합
scope는 작업 의도의 기록이며 호스트가 부여한 도구 권한과 별개
design의 기본 allowed_actions는 `read`, `write_design`, 다른 모드에서도 host의 현재 승인 범위가 실제 행동을 제한
origin은 `quote`, 선택 `host_message_id`, 선택 `source_refs`로 구성
새 작업은 서버가 기존 대화를 자동 수집한 결과가 아닌 클라이언트가 제공한 범위 기록

### work_record

필수 입력은 `request_id`, `mutation_id`, `work_id`, `expected_revision`, `event`
한 호출은 event 하나를 저장하고 작업 revision을 1 증가
DB 커밋 전 전체 검증, 부분 event 저장 없음

| `event.kind` | 필수 내용 | 상태 변화 |
| --- | --- | --- |
| `decision_proposed` | `text`, `source_refs` | 제안 결정 생성 |
| `decision_adopted` | `decision_id`, `origin` | 사용자 결정으로 보고된 상태와 출처 기록 |
| `decision_superseded` | `decision_id`, `replacement_id`, `origin` | 이전 결정을 대체 상태로 전환 |
| `scope_revised` | `scope`, `origin`, `reason` | 작업 범위 revision 변경 |
| `issue_opened` | `text`, `source_refs`, `kind` | 충돌·질문·장애의 미해결 항목 생성 |
| `issue_resolved` | `issue_id`, `resolution`, `source_refs` | 해결 기록 연결 |
| `evidence_reported` | `claim`, `target_revision`, `observation` | 실행·검토 근거 추가 |
| `progress_reported` | `summary`, `next_actions`, `source_refs` | 현재 상태 요약 갱신 |
| `completion_reported` | `summary`, `evidence_ids`, `open_items` | 완료 보고 상태, 증거 커버리지 별도 계산 |
| `work_reopened` | `reason`, `origin` | 완료 보고 상태에서 활성 상태로 복귀 |

source_refs가 없는 순수 제안은 빈 배열 허용하되 근거 없음 표시
observation은 실행 보고라면 `command`, `exit_code`, `result_excerpt`, `target_hash` 포함
exit_code를 관찰하지 못한 경우 숫자를 추정하지 않고 별도 `outcome_unknown` 관찰 형식 사용
완료 보고 시 미해결 항목이 있거나 필수 기준의 근거가 없으면 `completion_coverage=incomplete`
completion_reported 자체를 실제 검증 성공으로 표시하지 않는 계약
자연어 목표 변경은 키워드 규칙이나 작은 모델만으로 자동 확정하지 않고 현재 사용자의 요청을 해석하는 호스트 책임 유지

### work_inspect

필수 입력은 `request_id`, `work_id`, `view`
view는 `events`, `decisions`, `issues`, `evidence`, `mutation`
목록 view는 선택 `cursor`, `limit` 기본 20·최대 100
mutation view는 `mutation_id` 필수, 원래 command 결과와 현재 삭제·무효화 상태를 반환
모든 ID 참조는 같은 프로젝트와 해당 work의 범위 안에서 검증

## 자료와 문맥 도구

### source_sync

필수 입력은 `request_id`, `mutation_id`, `items`
items는 1~32개이며 각각 파일 또는 발췌 형식 중 하나

| 형식 | 필수 필드 | 제한 |
| --- | --- | --- |
| 파일 | `kind=file`, `relative_path` | 설정 루트 안 UTF-8 파일, 최대 2MiB |
| 발췌 | `kind=excerpt`, `text`, `origin_label`, `external_key` | text 최대 8KiB, URL 자동 접속 없음 |

발췌의 선택 필드 `origin_url`과 `origin_locator`는 출처 표시용
같은 external_key의 새 내용은 같은 source의 새 revision, 다른 외부 문서의 ID 재사용은 호출자 오류
파일은 canonical relative path와 실제 경로 검사로 source ID를 재사용
삭제된 파일이 지정되면 source를 `missing`으로 갱신하고 예전 내용을 최신으로 반환하지 않는 처리
동기화는 항목별 커밋이며 5초 한도에 닿으면 처리된 목록·실패 사유·미처리 항목을 반환
완료·실패 항목과 미처리 항목의 합은 입력 items와 정확히 일치
새 mutation ID로 미처리 항목만 다시 요청하고 기존 mutation ID 재전송은 이전 결과를 복원
목록의 잠정 처리 상태는 DB에 남겨 프로세스 종료 뒤 미완료 항목만 재개 가능

### context_prepare

필수 입력은 `request_id`, `work_id`, `query`
선택 필드는 아래 기본값 사용

| 필드 | 형식·기본값 | 역할 |
| --- | --- | --- |
| `source_ids` | 문자열 배열, 생략 시 현재 등록 자료 | 검색 범위 제한 |
| `required_refs` | 참조 배열, 기본 빈 배열 | 호출자가 반드시 확인할 자료 |
| `budget_bytes` | 정수, 기본 16384·최대 65536 | 반환 본문 예산 |
| `judge_mode` | `off`, `auto`, `observe`, 기본 `auto` | 설치된 프로필 한도 안의 판정 선택 |
| `expected_work_revision` | 선택 정수 | 지정된 작업 상태와 다르면 conflict |

query는 1~8KiB, source_ids 최대 100개, required_refs 최대 32개
요청이 auto여도 설치 프로필이 disabled이면 모델 호출 없음
요청으로 disabled·shadow 프로필을 active로 승격할 수 없는 규칙
required_refs는 고정 revision·행 범위로 해석하고 최신본과 다르면 stale을 표시

출력 data의 필수 구조

| 필드 | 내용 |
| --- | --- |
| `packet_id` | 고정된 반환 묶음 ID |
| `work_revision` | 사용한 작업 상태 |
| `source_set_revision` | 검색에 사용한 자료 목록 버전 |
| `scope` | 목적·범위·provenance·origin 참조 |
| `protected` | 필수 제약·알려진 중요 충돌 |
| `evidence` | 원문 발췌·source/revision·행·역할·최신성 |
| `coverage` | 검색 범위·후보 수·실패·제외·미확인 범위 |
| `judgment` | `status`, 호출한 경우 프로필 fingerprint, 미수행·유보 이유 |
| `budget` | 상한·사용 바이트·누락 필수 항목 |

judgment.status는 `skipped`, `observed`, `applied`, `abstained`, `failed`
applied도 문맥 선택 보조를 뜻하며 원문의 참·거짓이나 승인 확정을 의미하지 않는 기준
필수 항목이 상한을 넘으면 outcome insufficient, evidence 자동 확정 없이 누락 항목과 필요한 크기 반환
source set 변경이 요청 도중 확인되면 partial 또는 제한된 재구성, 오래된 묶음을 최신으로 보고하지 않는 규칙

### source_read

필수 입력은 `request_id`, `source_id`, `revision`
선택 `start_line` 기본 1, `max_bytes` 기본 8192·최대 32768
고정 revision의 완전한 행만 반환하고 마지막 줄 위치·다음 start_line·EOF 여부 제공
단일 행이 최대 크기를 넘으면 `line_too_large`, 자동 잘림 없음
반환에는 `current_revision`, `freshness`, `origin_kind`를 함께 포함
current_revision은 마지막 수집에서 관찰한 현재 revision이며 새 파일 읽기를 하지 않은 이 도구의 freshness는 `last_observed` 또는 `historical`
반환 시점의 파일 최신성 검사가 필요하면 context_prepare 또는 source_sync를 사용
삭제되었거나 현재 반환 정책에서 금지된 revision은 전문을 반환하지 않는 계약

### data_forget

필수 입력은 `request_id`, `mutation_id`, `target`, `expected_data_revision`
target은 `source` 또는 `work`와 해당 ID, 프로젝트 전체 삭제·임의 파일 경로는 이 도구에서 지원하지 않는 범위
설치 정책과 호스트의 사용자 삭제 요청 범위 안에서만 사용
source 삭제 시 원문 revision·chunk·인덱스·판정·관련 packet 본문을 삭제하고 연결된 기록의 해당 인용을 제거 또는 가림
work 삭제 시 작업·event·전용 발췌를 삭제하되 다른 작업이 공유하는 source는 유지
응답은 삭제 개수·무효화 개수·유지된 공유 자료 수·물리 소거 미보장 범위를 포함
현재 자료 revision이 expected와 다르면 conflict로 중지하고 최신 범위를 확인한 뒤 재요청
원본 프로젝트 파일은 이 도구의 삭제 대상에서 제외

## 중복·동시성·오류

mutation 중복 키의 범위는 프로젝트와 tool 이름과 mutation_id의 조합
동일 키·동일 정규화 입력은 기존 결과 반환, 동일 키·다른 입력은 `idempotency_mismatch`
request_id와 전송 시각은 입력 hash에서 제외하고 나머지 의미 필드 포함
진행 중 같은 키가 도착하면 `busy`, 성공 여부가 불명확하면 mutation view로 상태 조회
중복 기록은 30일 유지하며 기간 만료 후 과거 ID를 새 작업처럼 재사용하지 않도록 클라이언트에 명시
삭제 후 replay는 삭제된 원문을 포함하지 않는 현재 시점의 가린 결과를 반환
work_record의 expected_revision과 수정은 같은 트랜잭션에서 검사

| 오류 코드 | 의미 | 재시도 |
| --- | --- | --- |
| `invalid_argument` | 형식·필드·범위 오류 | 입력 수정 후 새 호출 |
| `policy_denied` | 설치 정책 밖의 자료·행위 | 동일 요청 반복 금지 |
| `not_found` | 해당 프로젝트에 ID 없음 | 상태 조회 |
| `revision_conflict` | 기대 revision 불일치 | 최신 상태 검토 후 새 mutation |
| `idempotency_mismatch` | 중복 키에 다른 의미 입력 | 새 작업이면 새 mutation |
| `busy` | DB·작업 소유권 경쟁 | 같은 mutation으로 제한 재시도 |
| `source_changed` | 읽는 중 원문 변경 | 다시 수집 또는 partial |
| `input_incomplete` | 모델 입력·필수 자료 부족 | 범위 조정 |
| `engine_unavailable` | 모델 준비·연결·지원 부족 | 검색 결과 유지 |
| `deadline_exceeded` | 시간 예산 초과 | 결과 유무 확인 후 결정 |
| `storage_limit` | 저장 예산 초과 | 보존 정책 조정 |
| `storage_failed` | 커밋·무결성·접근 실패 | 저장 성공 주장 금지 |

## 요청·응답 예시

설계 작업 생성 예시

```json
{
  "request_id": "req-demo-01",
  "mutation_id": "mut-demo-01",
  "create": {
    "title": "로컬 문맥 도구 설계",
    "goal": "구현 전에 상세 설계 문서 완성",
    "scope": {
      "mode": "design",
      "allowed_actions": ["read", "write_design"],
      "constraints": ["프로그램 구현은 별도 요청 이후"]
    },
    "origin": {"quote": "이제 진짜 설계 하자"}
  }
}
```

문맥 준비 예시

```json
{
  "request_id": "req-demo-02",
  "work_id": "work-demo-01",
  "query": "한국어 입력의 판단 실패를 어떻게 처리할까",
  "budget_bytes": 16384,
  "judge_mode": "auto",
  "expected_work_revision": 3
}
```

모델 없이 검색 문맥을 반환하는 최소 예시

```json
{
  "contract_version": "1.0",
  "request_id": "req-demo-02",
  "outcome": "ok",
  "data": {
    "packet_id": "packet-demo-01",
    "work_revision": 3,
    "source_set_revision": 7,
    "scope": {"goal": "상세 설계 문서 완성", "mode": "design", "allowed_actions": ["read", "write_design"], "constraints": ["구현은 별도 요청 이후"], "provenance": "agent_reported", "origin_refs": ["event-demo-01"]},
    "protected": [{"text": "구현은 별도 요청 이후", "origin_ref": "event-demo-01"}],
    "evidence": [{"source_id": "source-demo-01", "revision": "rev-demo-02", "content_hash": "sha256-demo-placeholder", "start_line": 12, "end_line": 12, "text": "한국어 원문을 보존하고 번역 손실 시 판정 유보", "role": "support_candidate", "freshness": "verified_at_read"}],
    "coverage": {"scope": "registered_sources", "candidates": 1, "excluded": 0, "unsearched": [], "claim": "범위 밖 자료는 미확인"},
    "judgment": {"status": "skipped", "reason": "profile_disabled"},
    "budget": {"limit_bytes": 16384, "used_bytes": 512, "missing_required": []}
  },
  "warnings": [],
  "error": null
}
```

used_bytes와 content_hash는 설명용 수치·자리표시자이며 실제 구현에서는 직렬화 규칙과 원문 hash로 계산
모델 미사용이 모든 관련 자료의 완전한 검색이나 사실 검증 완료를 의미하지 않는 예시

## 모델 어댑터 경계

입력은 `evaluation_id`, `profile_fingerprint`, `source_refs`, `state`, `questions`, `deadline_ms`
question은 `id`, `purpose`, `instructions`, `options`, `language`를 포함하고 임의 실행 지시 대신 사전 정의한 목적만 허용
purpose는 `relevance`, `evidence_relation`, `skill_fit`
각 options에 해당 없음 또는 근거 부족을 표현할 수 있는 항목을 포함

출력은 `evaluation_id`, `status`, `answers`, `usage`, `latency_ms`, `profile_fingerprint`
answer는 선택·원래 분포·점수 정의·유보 이유를 포함하며 모든 모델의 raw confidence를 같은 확률로 정규화하지 않는 계약
OpenJev는 `/v1/systemone`의 실제 답변을 변환, Laya는 공식 predict 결과를 변환
질문 ID 대응·허용 라벨·수치 범위·NaN/무한대·분포합·최대 응답 크기를 검증하고 위반 시 전체 관련 질문을 failed 처리
점수 종류·질문 템플릿·샘플링·정밀도·언어별 보정이 profile fingerprint에 포함
언어별 평가 미통과·원문 잘림·분포 의미 불명은 높은 점수와 무관하게 abstained

## 호환성 관리

canonical schema는 구현 시 이 문서에서 도출한 JSON Schema 파일 하나로 관리하고 클라이언트·서버·예제 검사에서 공유
새로운 선택 응답 필드 추가는 minor 변경, 기존 필수 필드·의미·enum 변경은 major 변경
요청 schema는 서버가 tools/list에 제공한 버전을 기준으로 생성하고 새 필드를 구형 서버에 전송하지 않는 규칙
클라이언트는 알 수 없는 선택 응답 필드를 무시하되 알 수 없는 outcome·상태는 성공으로 해석하지 않는 기준
DB schema 버전·제품 도구 버전·MCP 협상 버전·엔진 버전은 서로 다른 식별자
예시는 현재 실행되지 않은 계약 자료이며 구현 시 양쪽 경계의 직렬화·오류·재전송 동작을 같은 fixtures로 검증
