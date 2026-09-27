# 에이전트 기억·문맥 복원 제품 비교 연구

조사일: **2026-09-27**. Jev 비교 기준: `310456ff1d2b5455f6851f5c5e69555e0dfba212`의 소스와 기존 검증 기록, Electron 0.7.0 / 서비스 0.2.0.

이 문서는 대화가 끊겨도 목표·제약·근거를 회복하는 **Jev의 본래 목적**과 겹치는 제품을 조사한다. [운영 경험 연구](desktop-operations-research.md)의 Docker Desktop·VS Code 사례와는 별도다. 공식 문서·공개 저장소·연구 논문을 읽었으며, 타사 제품 설치나 동일 조건 성능 실험은 하지 않았다. 아래 권고는 조사 결과에 따른 설계 제안이며 구현 완료 목록이 아니다.

## 1. 판단

**Jev를 계속 개발할 근거는 ‘기억을 저장한다’는 기능 자체가 아니다. 한국어 코딩 작업의 현재 목표, 하지 말아야 할 일, 변경된 결정과 검증 근거를 작은 문맥으로 정확히 복원하는 데서 실익을 보여야 한다.**

저장·검색·MCP 연동·로컬 실행은 이미 여러 비교 제품이 제공한다. Jev에는 작업 revision, 채택·대체 결정, 고정 원문 참조, 실패·반대 근거 보호, 완료 기준이 구현돼 있다. 그러나 이것이 사용자 실수나 총 작업 시간을 줄이는지는 별도 문제다. 기존 재개 비교에서는 기본 조건 3/3, 저장·검색 조건 2/3, shadow 판단 추가 조건 0/3이 제한 시간 내 정상 완료했다. 단일 과제이므로 일반적인 열세도 우세도 확정할 수 없지만, 현재 성능 우위를 주장할 근거는 없다. [재개 비교 결과](resume-evaluation-results.md)

권고 순서는 다음과 같다.

1. **실제 호스트가 어떤 서버와 기억을 사용했는지 확인할 수 있게 한다.** 연결됨, 저장됨, 복원 응답을 받음, 답변에 사용함을 구분한다.
2. **현재 작업의 짧은 복원을 완성한다.** 이미 구현한 checkpoint·상세 분리를 출발점으로, 반복 전달량과 수동 재설명 횟수를 측정한다.
3. **변경·폐기·삭제·수정의 사용자 흐름을 검증한다.** 오래된 지시를 현재 지시처럼 다시 살리지 않아야 한다.
4. **단순 기준선과 비교한다.** 수동 Markdown 메모와 Basic Memory보다 나은 지점이 확인되기 전에는 그래프 DB·자동 추출·모델 판단을 일괄 추가하지 않는다.

## 2. 조사 방식과 해석 규칙

- **외부 문서 확인:** 조사 당일 공식 페이지가 설명한 기능이다. 실제 성공률·지연·품질을 재현했다는 뜻은 아니다.
- **로컬 코드 확인:** Jev 구현에서 읽은 동작이다. 현재 연결된 모든 프로세스에 같은 코드가 올라갔다는 뜻은 아니다.
- **기존 실측:** 저장소의 날짜별 검증 결과다. 이번 조사에서 새로 실행한 벤치마크로 표시하지 않는다.
- **제안:** 이 문서가 도출한 우선순위와 합격 기준이다. 타사에서 검증된 수치를 그대로 옮긴 것이 아니다.
- 문서에서 발견하지 못한 기능은 **미확인**으로 남긴다. 제품에 없다고 단정하지 않는다. 저장소 라이선스와 관리형 서비스의 제공 범위도 구분한다.

웹 문서와 저장소 `main`은 변경 가능하다. 아래 링크는 2026-09-27 열람 기준이며 외부 저장소 commit을 동결하지 않았다. 도입 실험 때에는 패키지 버전·commit·모델·설정을 별도로 고정해야 한다. 별 수나 업체의 ‘최고 성능’ 표현을 선택 기준으로 사용하지 않았다.

## 3. 비교 대상과 겹치는 영역

| 제품군 | 형태와 목적 | Jev와 겹치는 부분 | 비교할 때의 경계 |
|---|---|---|---|
| Mem0 | 관리형 기억 API + OSS SDK | 대화에서 기억 추출, 사용자·세션 범위 검색·수정 | 앱 사용자용 기억 계층이며 Jev의 작업 완료 계약과 동일하지 않음 |
| Zep / Graphiti | 상용 문맥 서비스 / OSS 시간 그래프 프레임워크 | 바뀐 사실, 과거 시점, 근거 연결 | Zep 운영 기능을 Graphiti 단독 기능으로 계산하지 않음 |
| Supermemory | 관리형 기억·문서 검색·MCP, 로컬 배포도 안내 | 여러 도구의 기억 재사용, 프로필, 문서 검색 | 공개 repo와 로컬 바이너리·관리형 전체의 동등성은 미검증 |
| Letta | 상태를 유지하는 에이전트 런타임·SDK | 세션을 넘는 기억, 상시 문맥과 참조 기억 분리 | 기존 Codex에 붙는 기억 서버와 런타임 교체는 다른 선택 |
| Basic Memory | 로컬 Markdown 지식 저장소·MCP + 선택적 Cloud | 사람이 읽고 고치는 기억, 프로젝트별 복원 | Jev와 가장 먼저 직접 비교할 후보라는 판단 |
| LangMem | 기억 추출·정리·검색 도구 라이브러리 | 대화 중 기록과 배경 정리 분리 | 설치형 완제품이 아니며 저장소·호스트 통합은 별도 |
| MCP Memory reference server | 최소 로컬 그래프 기억 서버 | MCP 기억 쓰기·읽기·삭제 | 복잡한 Jev가 최소 구현보다 나은지 확인할 기준선 |

각 행의 기능 근거는 다음 제품별 절에 연결했다. 상용/오픈소스를 양자택일로 나누지 않았다. 같은 제품군 안에 양쪽이 공존하기 때문이다.

## 4. 제품별 연구

### 4.1 Mem0 — 기억 쓰기·검색 API의 명확한 경계

공식 문서는 관리형 Platform과 직접 운영하는 OSS SDK를 구분한다. `add`는 전달한 메시지에서 LLM으로 사실 등을 추출하며 `infer=False`로 원문을 저장할 수도 있다. `user_id`, `agent_id`, `run_id` 등의 식별자가 검색 범위를 나눈다. 현재 add 문서는 ADD-only 동작을 설명하므로, 과거 소개 글만 보고 ‘추가하면 모순이 자동으로 모두 해결된다’고 전제하면 안 된다. [Add Memory](https://docs.mem0.ai/core-concepts/memory-operations/add)

수정 API는 기억 ID의 내용을 교체하고 인덱스를 갱신한다. 삭제도 ID·묶음·필터 단위로 제공한다. API 호출 후 다시 조회하는 확인 절차가 문서에 있다. 기억 수정·삭제와 원본 대화·외부 백업의 물리적 소거를 같은 보장으로 보지는 않았다. [Update](https://docs.mem0.ai/core-concepts/memory-operations/update) · [Delete](https://docs.mem0.ai/core-concepts/memory-operations/delete)

**Jev에 대한 판단:** 쓰기와 복원 실패를 사용자가 구분할 수 있어야 한다. 추출된 문장을 즉시 확정 지시로 채택하기보다 원문 참조가 있는 후보로 저장하는 것이 현재 작업 계약에 맞다. 자동 추출 정확도, 한국어 예외 조건 보존, 원문 행 단위 인용의 동등성은 이번에 검증하지 않았다.

배포: OSS 저장소는 [Apache-2.0](https://github.com/mem0ai/mem0/blob/main/LICENSE). 직접 운영에는 모델·저장소 비용과 관리가 남는다. 관리형 API의 UI·운영 기능까지 OSS 라이선스로 제공된다고 해석하지 않는다.

### 4.2 Zep / Graphiti — 현재 사실과 과거 사실의 분리

Graphiti는 entity·관계/fact·입력 episode를 연결하고, 사실이 바뀌면 과거 유효성을 남기는 시간 그래프를 설명한다. 검색은 의미·키워드·그래프 탐색을 조합한다. episode를 통한 계보는 ‘왜 이 사실을 기억하는가’를 보여 주는 데 참고할 만하다. 다만 추출된 fact와 원문 자체는 다른 층이다. [Graphiti README](https://github.com/getzep/graphiti)

같은 README는 Zep을 관리형 문맥 인프라, Graphiti를 직접 운영하는 프레임워크로 구분한다. Zep은 독자 Context Graph Engine을 사용하고, Graphiti는 별도 그래프 저장소와 LLM/embedding 구성이 필요하다. Zep의 사용자·대화 관리·운영 도구를 OSS 단독의 완성 기능으로 계산하지 않았다. [공식 비교](https://github.com/getzep/graphiti#zep-vs-graphiti) · [Zep v2 개념](https://help.getzep.com/v2/concepts)

**Jev에 대한 판단:** 우선 필요한 것은 그래프 DB가 아니라 ‘어떤 결정이 무엇으로 대체됐는가’를 명확히 보여 주는 흐름이다. Jev의 명시적 `decision_superseded`와 source revision을 활용하고, 자동 추론된 관계는 별도 후보로 취급한다. 시간 추론·다중 근거 연결이 실제 실패 원인으로 확인될 때 그래프 검색을 실험한다.

배포: Graphiti [Apache-2.0](https://github.com/getzep/graphiti/blob/main/LICENSE). Zep 구독·배포 조건과 Graphiti 라이선스는 별개다. 자동 모순 해결의 오류율, 한국어 성능, Jev 장비에서의 전체 수집 비용은 미확인이다.

### 4.3 Supermemory — 기억과 원문 검색, 호스트 연결의 결합

제품 문서는 입력 document와 추출된 memory를 구분하고, 사실의 갱신·확장·추론·만료를 설명한다. 만료로 검색에서 빠지는 것과 이력이 지워지는 것은 다르다. 사용자 프로필과 관련 검색을 함께 쓰는 구성이 특징이다. [제품 구조](https://supermemory.ai/product/)

현재 MCP 페이지는 OAuth로 접근 가능한 space를 선택하고 검색·기억 저장·문서 읽기 도구를 제공한다고 설명한다. 별도 코딩 플러그인의 자동 recall/capture와 일반 MCP 도구 호출을 구분한다. **MCP 주소 등록만으로 모든 호스트가 매번 자동 기록·복원한다고 해석하지 않는다.** [MCP](https://supermemory.ai/mcp/)

공식 repo는 로컬 서버, 기본 로컬 embedding, Ollama를 사용하는 오프라인 구성도 안내한다. 따라서 Supermemory를 클라우드 전용이라고 분류하지 않았다. 공개 저장소 [MIT](https://github.com/supermemoryai/supermemory/blob/main/LICENSE)와 전체 배포물의 범위·기능 동등성은 구분하며, 이번에는 로컬 바이너리를 설치하지 않았다. [Local 안내](https://github.com/supermemoryai/supermemory#supermemory-local--run-it-yourself)

**Jev에 대한 판단:** 사용자는 ‘서버 연결’보다 ‘현재 작업의 기억이 실제 전달됐는가’를 알아야 한다. 프로젝트·작업 범위, 마지막 복원 시각과 전달 packet을 표시하는 데 참고한다. 과거 `docs.supermemory.ai`의 URL 기반 인증 안내보다 조사 당일 현재 MCP 페이지를 우선했다. 문서와 README의 도구명도 달라, 실제 어댑터는 실행 시 도구 목록으로 검증해야 한다.

### 4.4 Letta — 항상 필요한 기억과 필요할 때 읽는 기억

현재 MemFS 문서는 기억을 agent 소유 Git 저장소에 두고 Markdown으로 투영한다고 설명한다. `system/` 파일은 매 turn 문맥에 들어가고 나머지는 파일 트리에서 찾아 읽는다. 수정은 commit·push 후 다른 실행 환경에도 반영된다. 기본 MemFS에는 의미/벡터 인덱스가 없고 선택적 검색 모듈이 있다. 오래된 memory-block 설명만으로 현재 구조를 대표하지 않았다. [MemFS](https://docs.letta.com/concepts/memfs)

SDK 문서는 일정 step 수나 compaction을 계기로 배경 정리를 실행하는 dreaming 설정을 제공한다. 이는 Letta 런타임의 기능이며 Codex에 MCP 서버를 추가한 것과 동일하지 않다. [SDK Memory](https://docs.letta.com/agent-sdk/memory)

**Jev에 대한 판단:** 목표·유효 제약·미해결 충돌은 짧은 필수 문맥에 두고, 과거 로그와 전체 근거는 필요할 때 읽는 방향이 맞다. Jev의 기존 checkpoint·상세 조회를 발전시키되 이미 적용한 기능을 새 기능으로 다시 만들지 않는다. 배경 정리는 비용과 오래된 revision의 덮어쓰기를 제어할 때만 후보가 된다.

배포: `letta-ai/letta` repo는 [Apache-2.0](https://github.com/letta-ai/letta/blob/main/LICENSE). 이 라이선스 확인만으로 최신 Cloud·SDK·MemFS의 모든 운영 기능이 해당 repo에서 동일하게 자가 호스팅된다고 결론 내리지 않았다. Jev를 Letta 런타임으로 교체하는 제안도 아니다.

### 4.5 Basic Memory — 가장 가까운 직접 비교 후보

Markdown 원문에 관찰 항목과 wiki-link 관계를 기록하고 MCP로 읽고 쓴다. 관찰을 개별 인덱싱하고 `build_context`로 연결된 노트를 탐색한다. 기억을 사용자가 직접 읽고 수정하기 쉬운 형태다. [Knowledge Format](https://docs.basicmemory.com/concepts/knowledge-format)

현재 검색 문서는 text·vector·hybrid와 선택적 reranker를 설명한다. 로컬 FastEmbed와 API provider 경로가 있으며, reranker는 추가 지연을 발생시킨다. ‘로컬 MCP 노트 도구이므로 단순 키워드 검색뿐’이라는 평가는 현재 자료와 맞지 않는다. 기본 모델의 한국어 적합성은 별도 평가가 필요하다. [Semantic Search](https://docs.basicmemory.com/concepts/semantic-search)

공식 repo는 선택적 Cloud와 로컬 설치를 구분한다. Claude Code 플러그인에는 session 시작 briefing·compaction 전 checkpoint·선택적 capture가 설명돼 있다. 이 hook 동작을 Codex의 동일 기능 보장으로 옮기지 않았다. 조사 당일 repo의 라이선스 표기는 **AGPL-3.0**이다. [README와 배포 구분](https://github.com/basicmachines-co/basic-memory)

**Jev에 대한 판단:** 가장 먼저 같은 한국어 작업 재개 사례로 비교한다. Jev가 더 복잡한 상태 계약을 유지하는 비용을 감수하려면, 단순 노트로 놓치는 변경 지시·실패 근거·허위 완료를 실제로 줄여야 한다. Markdown 내보내기와 원문을 열어 수정하는 흐름은 별도 보완 후보다.

### 4.6 LangMem — 기억 정리가 응답을 막지 않도록 설계

LangMem은 사실·경험·절차 기억을 나누고, 대화 중 쓰기와 대화 이후 정리를 구분한다. 핵심 변환은 특정 DB에 종속되지 않고, 상태 저장 통합은 LangGraph Store를 활용한다. namespace로 기억 범위를 나눌 수 있다. [Core Concepts](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)

README의 `InMemoryStore` 예제는 프로세스가 끝나면 기억이 사라진다고 명시한다. 지속성을 얻으려면 별도 DB-backed store가 필요하다. 라이브러리가 있다는 것과 운영 가능한 저장·복원 제품이 있다는 것은 다르다. [README](https://github.com/langchain-ai/langmem) · [MIT](https://github.com/langchain-ai/langmem/blob/main/LICENSE)

**Jev에 대한 판단:** 모든 검색에서 모델을 실행하지 않고, 즉시 필요한 사용자 변경은 먼저 기록하며 정리는 선택적으로 분리한다. 배경 처리도 자원 사용과 반영 지연은 있으므로 ‘공짜’로 계산하지 않는다. 정리 결과는 관찰 가능한 지시·행동·결과와 원문 참조를 중심으로 저장한다. 모델이 생성한 절차를 사용자 지시보다 높은 권한으로 만들지 않는다.

### 4.7 MCP Memory reference server — 최소 기준선

공식 서버는 entity·relation·문자열 observation을 관리하고 생성·조회·검색·삭제 도구를 제공한다. 검색은 이름·유형·관찰 내용을 대상으로 한다. 저장과 재조회라는 최소 목적을 적은 구성 요소로 구현한 비교 대상이다. [공식 README](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)

**Jev에 대한 판단:** 이 서버와 수동 메모만으로 해결되는 과제에서는 Jev의 추가 호출·설정·모델 준비가 오히려 비용일 수 있다. 반면 이 README에서 Jev와 같은 고정 원문 revision, 완료 조건-실행 증거 계약은 확인하지 못했다. ‘기능 없음’이나 ‘Jev가 더 정확함’으로 단정하지 않고, 그 차이가 필요한 테스트를 만든다.

## 5. 핵심 요구별 비교

아래 표는 앞 절의 공식 자료를 요약한다. 범위 식별자·namespace가 있다는 사실만으로 인증·접근 통제 전체가 검증된 것은 아니다.

| 요구 | 참고할 외부 방식 | Jev의 현재 근거 | 남은 판단 |
|---|---|---|---|
| 사람이 기억을 확인·수정 | Basic Memory Markdown, Letta MemFS | work/event/source 조회와 Electron 기록 UI | 현재 기억의 수정·대체·내보내기 왕복 흐름을 사용자 관점에서 검증 |
| 바뀐 사실·지시 구분 | Graphiti 시간 유효성, Supermemory 갱신 | 명시적 결정 대체·범위 변경, source revision | 자동 추출보다 ‘이전 결정이 재등장하지 않음’ 우선 |
| 근거 추적 | Graphiti episode, Basic Memory 원문 note | source ID·revision·행 범위·hash | 사용자에게 현재/과거/누락 구분을 이해 가능하게 전달 |
| 프로젝트 범위 | Mem0 식별자, LangMem namespace, Supermemory space/container | 프로젝트 저장소·허용 source·work scope | 다른 프로젝트·worktree의 유사한 이름이 섞이지 않는 시험 필요 |
| 짧은 필수 문맥 | Letta 상시/참조 기억 분리, Supermemory profile | checkpoint·protected·budget·상세 조회 | 반복 전달 비용과 실제 사용 근거를 함께 측정 |
| 뜻이 같은 표현 검색 | Basic Memory hybrid, Graphiti hybrid | FTS/BM25·trigram·한국어 부분문자열·순위 결합 | 의미 검색은 후보. 현재 한국어 실패 집합에서 실익 확인 후 채택 |
| 자동 복원 | Basic Memory의 특정 호스트 hook, Letta 런타임 | Jev skill·session hook·MCP 검증 기록 | 설치 가능과 현재 세션에서 실제 실행됐는지 분리 |
| 삭제·무효화 | Mem0 명시 삭제, Graphiti/Supermemory의 과거 유효성 | `data_forget`의 인덱스 제거·연관 내용 가림·packet/replay 무효화 | 만료, 논리 삭제, 원본/백업 물리 소거를 별도 설명 |
| 검증 가능한 완료 | 일반 기억 제품과 다른 Jev의 설계 초점 | criterion·target revision·agent_reported evidence | 저장된 성공 보고와 독립 실행 검증을 혼동하지 않게 유지 |

로컬 코드 근거: [검색](../src/jev_context/sources.py), [문맥 구성](../src/jev_context/context.py), [투영](../src/jev_context/projection.py), [작업 기록·삭제](../src/jev_context/service.py), [완료 판정](../src/jev_context/completion.py), [계약 2.0](../src/jev_context/contracts_v2.py).

`data_forget`은 `physical_erasure_guaranteed=False`, `original_files_unchanged=True`를 명시한다. 이것을 보안 삭제로 소개하면 안 된다. 현재 검색 코드에는 embedding 검색 경로가 없으며 로컬 판단 모델의 shadow 점수를 의미 검색 구현으로 계산하지 않았다.

## 6. Jev에서 이미 한 일과 아직 부족한 일

**이미 한 일:** 최신 checkpoint 전달, 대체된 완료 기준의 간략 표현, 실패·반대 근거의 핵심 보존과 상세 분리, 중복 원문 제거, 예산 부족의 partial/insufficient 표시. 새 MCP 프로세스 비교에서 필수 본문은 14,885 B → 11,590 B였고 같은 예산에서 검색 근거가 1개 → 2개로 늘었다. 이는 바이트·전달 계약의 개선이며 코딩 속도 향상 증거는 아니다. [문맥 전달 개선 결과](context-efficiency-results.md)

**부족한 일:** 설치된 소스, Electron bridge, 이미 연결된 호스트 MCP가 같은 코드인지 자동 식별하는 제품 흐름이다. 이번 조사에서 현재 호스트의 `context_prepare`는 근거가 최종 응답에서 빠졌는데도 `outcome=ok`를 반환했다. 최신 소스의 위 결과와 다르므로, 현재 연결이 최신 동작이라는 근거로 사용할 수 없다. 오래된 프로세스라는 원인은 실행 식별 정보로 확정하지 못했다. 별도 새 서버의 통과를 현재 호스트 성공으로 대체해서도 안 된다.

`workspace_status`의 `hooks=not_installed`, `desktop_current_session=not_observed`와 과거 [호스트 검증 기록](host-capabilities.md)의 성공도 구분해야 한다. 상태 필드만으로 과거 hook이 없었다고 결론 내리지 않고, 기록된 성공만으로 현재 세션의 자동 복원을 보장하지 않는다.

**효과 증거 부족:** 9월 22일 소규모 코딩 비교에는 누적 입력 감소와 캐시 제외 입력 증가가 함께 있었다. 9월 27일 재개 비교는 효율 이점을 확인하지 못했다. 유리한 숫자만 골라 ‘토큰 절약 제품’으로 소개할 수 없다. [코딩 비교](coding-benchmark-results.md) · [재개 비교](resume-evaluation-results.md)

## 7. 보완 우선순위와 합격 기준 제안

| 우선순위 | 작업과 참고점 | 구현 시작점 | 제안하는 합격 기준 |
|---|---|---|---|
| P0 | 실제 연결·복원 식별: MCP/플러그인의 경계 명확화 | server/bridge handshake, host 상태, packet 기록 | 현재 서버의 build/instance/contract 식별. 저장된 source revision과 마지막 전달 packet 구분. 오래된 호스트·연결 실패·앱만 재연결한 사례를 성공으로 표시하지 않음 |
| P0 | 짧은 작업 복원: Letta의 계층화 참고 | 기존 projection/context/budget 개선 위에 계측 | 유효 목표·제약·미해결 충돌·다음 행동 보존. 필수 근거 누락 시 insufficient. 선택 자료 누락 시 partial. 전체 도구 응답 바이트와 실제 토큰을 분리 기록 |
| P0 | 비교 평가를 먼저 고정 | 기존 resume 평가 runner + 아래 사례 | 실패·시간 초과까지 결과표에 포함. 수동 메모 대비 실익 없으면 기본 모델 비용을 추가하지 않음 |
| P1 | 현재 기억의 수정·대체·출처 열기·내보내기 | work/source 조회·기존 결정 이벤트·Desktop 상세 | 수정 전후 연결과 원문을 확인 가능. export→별도 저장소 복원에서 출처·revision·현재 상태 보존. 원문 파일 편집과 파생 기억 수정 구분 |
| P1 | 선택적 수집과 복원 연결 | 이미 있는 skill/session hook 재사용 | 허용 자료만 수집, 중복 event 억제, 프로젝트 격리, 저장/전달 실패 표시. 호스트별 실제 지원·시험 결과 표시 |
| P2 | 한국어 의미 검색·재정렬 실험 | lexical baseline에 선택적 후보 채널 | 동의 표현 recall 개선과 부정 제약 보존을 함께 통과. cold start·메모리·p95 비용 공개. 부족하면 lexical 유지 |
| P2 | 자동 기억 정리·시간 관계 | revision에 묶인 후보 기록 | 오래된 입력 결과 반영 거부, 사용자 원문과 추론 분리, 잘못된 갱신 복구 가능. 명시 결정의 자동 덮어쓰기 금지 |

P0는 출시 전 우선 확인할 항목, P1은 사용성 보완, P2는 효과가 확인될 때만 확장할 항목이라는 뜻이다. 이 표로 새로운 기본값을 활성화하지 않았다. 기존 shadow 판단도 승격하지 않는다.

Jev가 채택할 차별점의 후보는 **‘현재 유효한 코딩 작업 계약을 출처와 함께 복원’**이다. 이를 구현 구조의 차이로 설명할 수는 있지만 경쟁 우위라는 표현은 아래 비교를 통과한 뒤에 사용한다.

## 8. 성능 주장 검토와 비교 실험안

### 공개 평가를 해석하는 방법

Mem0 논문은 LOCOMO의 대화 기억 질의와 여러 기준선을, Zep 논문은 DMR·LongMemEval을 다룬다. 서로 다른 모델·질문·검색 예산·판정 방식의 수치를 한 순위표로 합치지 않았다. 2025년 논문의 결과를 2026년 현재 제품 버전의 성능으로 간주하지도 않는다. [Mem0 논문 v1](https://arxiv.org/abs/2504.19413v1) · [Zep 논문 v1](https://arxiv.org/abs/2501.13956v1)

LongMemEval은 정보 추출, 여러 세션의 추론, 지식 갱신, 시간 추론, 답변 보류를 평가한다. 이 분류는 Jev 사례 설계에 유용하지만 한국어 코딩 제약 준수나 실제 수정 성공까지 대신 검증하지 않는다. [LongMemEval 원저자 저장소](https://github.com/xiaowu0162/LongMemEval)

이 연구는 논문 초록·공식 평가 설명을 검토한 문헌 조사다. 논문 전체 실험의 재현성 감사나 중립적인 제품 순위 결정은 하지 않았다. Supermemory 등의 자사 성능 홍보 수치도 Jev와의 직접 비교 숫자로 옮기지 않았다.

### 먼저 실행할 범위

**1단계 — 기억 복원 평가:** 사람이 검토한 한국어 사례 30개를 별도 개발/heldout 집합으로 나눈다. 이 30개는 기존 평가 사례와 별도로 동결할 신규 제안이다. 개발 사례로 설정을 조정한 뒤 heldout 정답을 보고 재튜닝하지 않는다.

| 사례군 | 예시 | 확인할 결과 |
|---|---|---|
| 변경·예외·부정 지시 | ‘설계만’ → ‘문서 A만 구현’, 배포는 계속 금지 | 허용 변경은 반영하고 남은 금지는 보존 |
| 과거와 현재 | 이전 모델 선택 폐기, 새 결정 채택 | 과거 결정을 현재로 답하지 않음 |
| 근거 갱신·누락·복원 | 같은 경로 파일 수정·삭제·재등장 | 현재/과거/missing과 revision 정확성 |
| 실패와 완료 | 테스트 실패 후 코드 변경, 과거 성공 기록 존재 | 오래된 성공을 새 revision 완료 증거로 쓰지 않음 |
| 한국어 표현과 경계 | 동의 표현, 짧은 한국어 단어, 같은 이름의 다른 프로젝트 | recall과 프로젝트 격리 동시 평가 |
| 불충분·삭제·재시작 | 예산 부족, 삭제 기억 재조회, 새 프로세스 복원 | 모르는 내용 보류, 삭제 내용 재노출 방지, 복원 상태 일치 |

각 군 5개로 시작한다. 파생 변형은 독립 표본처럼 세지 않는다. 초기 규모는 결함 발견용이며 일반 성능 우위를 입증하는 표본 크기로 주장하지 않는다.

**기준선:** A 기억 없음, B 동일 원문에서 작성한 수동 Markdown checkpoint, C MCP memory reference server, D Basic Memory, E Jev `judge_mode=off`. 첫 비교는 이 로컬 후보로 제한한다. Mem0 OSS·Graphiti·Supermemory local은 추가 운영 비용을 측정할 2차 후보이며, 관리형 서비스 실험은 비용·공개 시험 데이터 전송 범위를 먼저 확정한다. Letta는 런타임까지 바뀌므로 동일 호스트 비교와 별도 트랙으로 둔다.

모든 조건에 같은 원본 이력을 제공한다. 제품별 전처리·수집·색인 비용도 기록한다. 수동 메모는 heldout 질문/정답을 보지 않고 작성한다. 도구 호출을 자유롭게 맡기는 실제 사용 트랙과, 동일 검색 질의를 강제하는 검색 품질 트랙을 구분해야 도구 선택 실패와 검색 실패를 혼동하지 않는다.

**2단계 — 실제 작업 평가:** 1단계의 심각한 제약·삭제·격리 오류를 수정한 뒤, 최소 6종의 코딩 재개 과제를 조건별 3회 교차 순서로 시행한다. 이는 시작 규모 제안이다. 성공률의 불확실성이 크면 우열을 단정하지 않고 표본 확대 여부를 결정한다. 생성 모델·추론 강도·호스트·코드·필수 정책·시간 상한을 고정하고 모델 응답 및 캐시 변동도 남긴다.

### 기록할 지표와 진행 조건

- 정확성: 필수 목표·제약 보존, 정답 근거 recall, 잘못 채택한 과거 기억, 근거 없는 단정, 수정된 코드의 숨은 검사 통과.
- 사용 경험: 사람이 다시 설명한 횟수, 기억 수정/대체까지의 조작 수, 자동 복원 성공/실패와 실제 packet 전달 여부.
- 비용: 수집·정리·색인·모델 준비·검색·생성·검사 시간을 분리. cold/warm, p50/p95, 입력/캐시 제외 입력/출력 토큰, 로컬 메모리 사용을 기록.
- 실패: 시작 실패·한도 오류·시간 초과·최종 답변 부재를 전체 시도 분모에 포함. 성공 시행의 시간 중앙값은 별도 설명치이며 실패 비용을 숨기는 총효율 지표로 사용하지 않음.
- 재현성: 사례/정답/소스 hash, 패키지와 모델 버전, prompt, 검색 예산, OS·장비, 원시 결과, 실행 순서 보존. LLM 판정만으로 정답을 확정하지 않고 부정·예외 사례를 사람이 검토.

**초기 진입 기준 제안:** 동결 사례에서 필수 제약 훼손·프로젝트 혼입·삭제 내용 재노출이 0건이어야 다음 단계로 간다. 작은 집합의 0건은 일반적인 무오류 보장이 아니다. Jev를 더 복잡하게 만드는 변경은 수동 checkpoint 대비 정확성 저하 없이 재설명·완료율·총비용 중 사전 지정한 목표가 개선될 때 채택한다. 현재까지 이 경쟁 비교를 실행한 결과는 없다.

## 9. 후속 결정과 문서의 범위

지금은 Jev의 저장 계층을 다른 제품으로 교체할 근거가 부족하다. 대신 Basic Memory를 첫 직접 비교 대상으로 삼고, 연결 실체 확인과 짧은 복원 전달을 P0로 유지한다. 이 비교에서 추가 계약의 실익이 보이지 않으면 단순 메모 방식으로 기능을 줄이는 선택도 포함한다.

이번 산출물은 **목적이 겹치는 7개 제품군의 출처 있는 연구, Jev 코드·기존 실측과의 비교, 보완 순서, 실행 전 평가안**이다. 새 제품 기능·벤치마크 결과·모델 승격을 만들어 냈다고 보고하지 않는다. 구매 가격 비교, 전체 시장 목록, 모든 타사 코드의 보안/정확성 감사는 포함하지 않는다.
