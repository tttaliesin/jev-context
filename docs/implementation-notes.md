# 첫 구현의 설계 보완

2026-09-22 · 구현 버전 0.1.0 · 원본 설계 1.0과 함께 읽는 변경 기록
초기 구현은 전달받은 설계 9개 파일을 복사해 참고했으며 원본 문서와 기존 잘못 생성된 프로그램은 수정·재사용하지 않은 범위
2026-09-22 재설계 이후 제품 방향은 [통합 설계 2.0](design/unified-design.md), 아래 내용은 구현 0.1.0의 변경 기록
현재 동작은 코드·canonical JSON Schema·이 문서에서 보완

## 계약의 모호함 해결

- `issue_opened`의 event 종류와 충돌 종류에 중복된 `kind`는 각각 `kind`와 `issue_kind`로 분리
- 목표 변경을 기록할 기존 필드가 없어 `scope_revised.goal`을 선택 필드로 추가
- 실행 보고를 원문 삭제에 연결할 수 있도록 `evidence_reported.source_refs` 선택 필드 추가
- 삭제 대상 형식은 `{ "kind": "source", "source_id": "…" }` 또는 `{ "kind": "work", "work_id": "…" }`로 고정
- byte budget은 scope·protected·evidence의 compact UTF-8 JSON 크기로 계산, 상태 메타데이터는 본문 예산과 별도
- 필수 본문이 예산을 넘으면 본문을 반환하지 않고 최소 필요 크기·누락 사유·work ID로 재조회 가능
- 고정된 acceptance criteria 등록 계약이 없어 완료 근거 충분성은 보수적으로 `incomplete` 유지
- 언어 의미 판단을 서비스의 키워드 규칙으로 추가하지 않고 원문 보존과 명시적 event 변경으로 처리
- 표준 입력 JSON frame과 업무 요청 전체에 512KiB 상한 적용, 중복 JSON key·잘못된 UTF-8·과도한 중첩 거절

## 저장과 삭제

SQLite WAL·foreign keys·FULL synchronous·secure_delete 적용
프로젝트의 전용 DB 디렉터리는 Windows 현재 사용자와 SYSTEM 접근으로 제한
Unix 계열에서는 디렉터리 권한 0700 사용, 전체 제품 지원 검증은 Windows에서 수행

설정 파일이 프로젝트 ID와 경로의 결합을 소유하므로 별도 registry.json 중복 상태는 생성하지 않는 첫 구현
source revision과 원문 바이트·chunk·FTS를 같은 DB에 저장
파일 경로는 열린 핸들의 실제 대상까지 확인하고 읽기 전후 핸들 상태 비교
Windows Python 3.12의 path stat과 handle fstat에서 ctime 의미 차이를 관찰해 핸들끼리 비교하도록 구현

수집은 항목별 커밋, OS 잠금은 프로세스 종료 시 해제
삭제와 수집은 프로젝트 수집 잠금으로 직렬화하고 삭제 후 중단된 수집의 과거 mutation 재개도 가림
mutation의 입력 원문은 저장하지 않고 입력 hash와 안전한 결과만 보존
30일은 최소 보존 기간으로 적용하며 첫 버전에서는 자동 만료 없음

삭제 시 연결된 기록 본문을 보수적으로 가리고 모든 packet·mutation replay 본문을 무효화
이 방식은 무관한 cached 응답도 가릴 수 있으므로 가림 이후 현재 상태 조회 필요
독립 출처의 같은 문장이나 출처 연결 없이 새로 붙여넣은 텍스트는 자동 삭제 범위에서 제외
work 전용 발췌 소유권 필드가 없는 1.0 계약에 따라 등록 source는 공유 자료로 취급하고 work 삭제 시 유지
백업·복원 명령과 기존 DB migration은 첫 버전에 미제공, 알 수 없는 schema에서 자동 새 DB 생성 금지

## 검색과 문맥

NFC 검색 키·FTS5 unicode61·trigram·경로 및 부분 문자열 채널을 reciprocal rank fusion으로 결합
최대 40개 후보와 필수 근거·채택 결정·미해결 항목을 별도로 구성
chunk는 4KiB 이하 UTF-8 경계를 유지하고 앞뒤 chunk ID를 반환
매우 긴 한 행의 chunk는 byte 위치와 `complete_lines=false`로 표시, source_read는 완전한 행만 반환

검색 범위는 허용된 등록 자료로 제한
선택된 파일의 hash를 갱신한 후 후보를 한 번 다시 계산하고 반환 전에 재검사
여러 파일의 동일 시점 snapshot을 보장하지 않고 `per_source_observation`으로 표시
모델이 없으면 검색 후보를 판정 점수로 제외하지 않는 동작

## 첫 버전의 남은 범위

실제 모델을 이용한 한국어 판단·자원 평가와 active 프로필 승격은 미완료
모델 score는 승인·실행 성공·정답 확률로 해석하지 않는 관찰 데이터
Desktop 세션의 실제 도구 호출과 Skill 선택, hooks, 실제 사용자 자료 30개 평가, 번역은 미검증 또는 후속 범위
환경 의존 검증을 합성 테스트 통과로 대신하지 않는 기준
