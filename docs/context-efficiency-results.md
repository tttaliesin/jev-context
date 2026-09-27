# 문맥 전달 개선 적용 결과

2026-09-27. [실행 계획](context-efficiency-implementation-plan.md)에 따라 계약 2.0의 문맥 전달을 수정했다. 원문·이벤트를 삭제하지 않고 반환 표현을 바꿨다.

## 적용 내용

- `work_open`과 `context_prepare.scope.checkpoint`는 완료 상태의 최신 요약·남은 항목을 보여 준다. 완료 이전의 다음 행동은 현재 계획으로 노출하지 않는다. 새 완료/재개 이벤트도 진행 상태를 일관되게 기록한다. 과거 이벤트 조회는 유지한다.
- 대체된 완료 기준은 식별자·이유·대상 revision으로 표현한다. 전체 설명은 `work_inspect(view=criteria)`에 남는다. 현재 유효한 기준은 그대로 전달한다.
- 실패·반대 근거의 claim, 역할, 대상 revision, 명령·종료 코드·대상 hash는 보존한다. 상세 로그 등은 `work_inspect(view=evidence)`에서 읽으며 `details_omitted`로 분리 여부를 표시한다. 유효 제약·미해결 문제·채택된 결정·필수 원문은 유지한다.
- 저장 가능한 판단 상세는 `evaluation_count`와 조회 참조로 제공한다. `work_inspect(view=judgments, packet_id=...)`에 같은 상세가 남는다. read-only에서는 저장되지 않은 조회 참조를 내보내지 않는다.
- 같은 source/revision의 필수 원문에 완전히 포함된 검색 청크를 중복 전달하지 않는다. 서로 다른 revision과 긴 한 행의 서로 다른 바이트 청크는 구분한다. 근거에 `locator`를 추가했다.
- 예산으로 자료가 빠지면 `partial`, `coverage.budget_omitted_candidates`, `coverage.retrieval_status=budget_limited`를 반환한다. 최종 근거 수와 실제 본문 크기를 다시 계산한다. 필수 항목을 보낼 수 없으면 기존처럼 `insufficient`다.

## 실제 저장 작업의 전후 비교

기존 작업 `work-f53e94c849424d43bb42783a2a9bb5f6`, revision 36을 그대로 사용했다. query `문맥 효율 비교 한계 shadow`, 결과 문서 source 하나, `judge_mode=off`, 전송 예산 40,000바이트를 고정했다. 수정 전 응답을 저장한 뒤 공식 Python SDK로 **새 MCP 프로세스 두 개**를 순서대로 열어 수정 후 결과를 확인했다.

| 지표 | 수정 전 | 수정 후 |
| --- | ---: | ---: |
| 필수 본문의 최소 크기 | 14,885 B | 11,590 B |
| 실제 반환 검색 근거 | 1개 | 2개 |
| 예산으로 빠진 검색 근거 | 5개 | 4개 |
| 최종 MCP 전송 크기 | 39,993 B | 38,525 B |
| 결과 상태 | ok | partial |

필수 본문이 약 22.1% 작아져 같은 전송 예산에서 근거 하나를 더 제공했다. 여전히 전체 6개 중 4개는 예산 밖이다. 이를 숨기지 않고 partial로 알린다. 최종 전송량은 근거 구성도 달라졌으므로 순수 압축률로 해석하지 않는다. 바이트는 토큰·청구 비용이 아니다.

이전 외부 조사 당시에는 같은 크기 예산에서 근거 0개가 관측됐다. 이번 직전 기준은 1개다. 새 비교에는 이번에 캡처한 기준만 사용했고, 전후 반환 근거의 source hash 일치를 확인했다. 전체 source 목록 revision은 새 문서 등록으로 바뀌었지만 검색 범위의 원문 hash와 작업 revision은 같았다.

두 새 서버에서 현재 상태와 반환 근거가 같았다. 기존 work·events·evidence·issues·decisions의 전후 hash도 같았다. 전체 기준 12개, 실패·반대 근거의 claim과 상세 observation을 다시 조회할 수 있었다. 이 검사는 문맥 전달·기록 보존 검증이며 코딩 속도나 모델 정확도 개선 검증은 아니다.

## 검사와 근거

- 기능 회귀: 완료/재개, 기존 완료 작업의 읽기 투영, 이력 상세, 원문 revision, 긴 행 청크, 예산 누락, shadow 상세, read-only, 모델 off, 부정 제약·충돌·검색 결과 없음.
- 관련 초기 검사: 31개 통과. 첫 실행의 테스트 기대값 두 개(Windows 줄바꿈, 예산 누락과 판단 누락을 혼합한 기존 검사)를 수정한 뒤 통과했다.
- 첫 전체 검사에서 기존 예산 벤치마크가 선택 근거 누락을 `ok`로 기대해 1개 실패했다. 새 `partial` 계약으로 기대값을 변경한 뒤 해당 벤치마크 검사 2개가 통과했다. 새 suite 생성에 쓰는 검사만 갱신했고 과거 `.local`의 동결 입력·결과는 수정하지 않았다.
- 새 MCP 전후 검사: `scripts/probe_context_delivery.py`, exit 0. 원본은 `.local/context-efficiency/before.json`, `.local/context-efficiency/live.json`.
- 최종 `.venv/Scripts/python.exe -X utf8 scripts/check.py`: lint 통과, 84개 파일 format 확인, **270 passed, 1 skipped**, exit 0, pytest 79.00초. 제외 1개는 기존 Windows symlink 권한 조건이다. `PYTEST_ADDOPTS=--basetemp=.t/ce-final -p no:cacheprovider`로 실행했다. 로그: `.local/context-efficiency/check.log`.
- 최종 소스·검사·문서·측정 파일 hash: `.local/context-efficiency/target.json`. 이 변경에 대한 신규 회귀는 8개이며 전체 통과는 모델 정확도 평가나 호스트 독립 검증을 뜻하지 않는다.

## 사용 및 남은 범위

새로 시작한 MCP 서버에서 변경이 적용된다. 이미 연결된 호스트의 서버 프로세스를 이 검사로 교체하지 않았다. 기존 연결은 재연결 후 새 코드를 읽는다. Electron UI 변경이나 모델 승격은 없다.

로컬 모델은 기존 shadow 승격 정책을 유지한다. 이번 기능 회귀와 새 MCP 검사는 모델 없이 수행했으며, 가짜 모델을 사용하는 기존 통합 검사에서는 판단 상세 전달 계약을 검증했다. 실제 한국어 분류 정확도·잘못된 제외율·보정/보류 정책 및 일반 코딩 효율은 독립 평가가 필요하다. 현재 데이터로 active를 활성화하지 않는다.
