# 기본 UX 재설계: 브랜드 사례 조사

조사일: 2026-09-26. 대상: 관리 앱 0.3.3의 탐색·사이드바·검색·단축키.

이번 변경은 아래 공식 도움말, 제품 변경 기록, 디자인 시스템 문서를 먼저 확인한 뒤 계획했다. 로그인한 타사 앱을 직접 사용한 실험이나 사용자 연구는 아니다. 공식 문서에 없는 hover 동작이나 효과를 확인한 것처럼 설명하지 않는다. 문서의 동작을 참고하며 브랜드 자산·화면·코드를 복제하지 않는다.

## 확인한 사례와 적용 판단

| 참조 | 공식 자료와 확인한 동작 | 현재 JEV의 차이 | 적용 판단 |
| --- | --- | --- | --- |
| R1 · Notion | [Navigate with the sidebar](https://www.notion.com/en-gb/help/navigate-with-the-sidebar): 오른쪽 경계를 드래그해 폭을 바꾸고 접기 버튼으로 숨긴다 | 220px 고정 폭이라 긴 한국어 작업명이 계속 잘린다 | 드래그·키보드 폭 조절과 폭 기억을 추가한다. 수치는 JEV의 본문 최소 공간을 기준으로 정한다 |
| R2 · Linear | [Collapsible sidebar](https://linear.app/changelog/unpublished-collapsible-sidebar), 2023-01-12: 완전히 접고, 상단 왼쪽 버튼·단축키·명령 메뉴로 다시 연다 | 접어도 거의 빈 56px 레일이 남는다 | 완전히 숨기고 상단 토글·빠른 실행을 항상 남긴다. 기록 본문의 가용 폭을 되돌려 준다 |
| R3 · Linear | [Search](https://linear.app/docs/search): 워크스페이스 검색과 현재 뷰의 제목 검색은 별도 경로다 | 현재 불러온 제목·목표만 필터링하면서 ‘작업 검색’으로 표시한다 | 목록은 ‘불러온 작업 필터’와 Ctrl+F로 명시한다. 개수·다음 페이지 여부를 표시하고 빈 결과에서 초기화·추가 불러오기를 제공한다 |
| R4 · VS Code | [User interface](https://code.visualstudio.com/docs/editing/getting-started/userinterface), [Command Palette](https://code.visualstudio.com/api/ux-guidelines/command-palette): Ctrl+B 사이드바, 검색 가능한 명령, 명확한 이름과 관련 있는 액션 | 조작을 아이콘·F1 도움말에서 따로 찾아야 한다 | Ctrl+B는 유지한다. 상단 ‘빠른 실행’과 Ctrl+K에 화면 조작·기록 열기를 모은다. 현재 가능한 동작과 비활성 이유를 표시한다 |
| R5 · Raycast | [Action Panel](https://manual.raycast.com/action-panel): 선택과 관련된 액션을 그룹으로 보여주며 이름으로 검색하고 오른쪽에 단축키를 둔다. 화살표·Enter·Esc로 조작한다 | 도움말은 키 목록만 제공하고 그 자리에서 실행할 수 없다 | 빠른 실행을 ‘화면과 연결’·‘모델’·‘불러온 작업’으로 구성하고 키 힌트를 함께 둔다. 타이핑만으로 모델을 실행하지 않는다 |
| R6 · Linear | [A calmer interface for a product in motion](https://linear.app/now/behind-the-latest-design-refresh), 2026-03-12: 본문보다 탐색의 시각적 비중을 낮추고 위치·동작을 일관되게 배치한다 | 상단 조회 시간이 주기적으로 바뀌고, 자동 조회마다 목록 로딩이 생긴다. 접힌 상태에 현재 기록의 맥락도 약하다 | 상단에는 프로젝트·현재 기록과 고정 조작을 둔다. 조회 시각은 하단으로 이동하고 백그라운드 갱신의 목록 스피너는 억제한다. 수동 요청·오류는 계속 표시한다 |

R2는 과거 출시 기록으로, 그 시점의 명시적인 동작 근거다. 현재 버전 전체가 동일하다는 주장은 하지 않는다. R4·R5를 결합한 JEV 빠른 실행은 자체 설계이며 어느 제품의 검색 범위나 단축키를 그대로 복제한 것은 아니다.

R5의 방향키 이동과 Enter·Esc 세부 동작은 [Raycast Keyboard Shortcuts](https://manual.raycast.com/keyboard-shortcuts)에서도 대조했다.

## 구현을 보완하는 접근성 자료

[WAI-ARIA Window Splitter](https://www.w3.org/WAI/ARIA/apg/patterns/windowsplitter/)의 방향키·범위·separator 이름/값을 적용 기준으로 사용한다. 이 패턴 문서 자체에 예제와 검토가 진행 중이라는 주석이 있으므로 완성된 인증 기준으로 표현하지 않는다. JEV에서는 Tab으로 경계에 접근하고 좌우로 조절하며 Home/End로 한계 폭, Enter로 접기를 제공한다. 접은 뒤 상단 토글로 포커스를 이동한다.

[GitHub Primer SplitPageLayout](https://primer.style/product/components/split-page-layout/)는 pane의 최소·최대·기본 폭, 폭 저장과 더블클릭 기본값 복원을 제공한다. JEV도 조절 범위와 기본값 복원을 명시하되 라이브러리를 추가하지 않고 현재 renderer로 구현한다.

## 채택하지 않는 기능

- hover로 열리는 사이드바: [Linear 2026-03-12 변경 기록](https://linear.app/changelog/2026-03-12-ui-refresh)에서 현재 지원 근거를 추가 확인했다. JEV에서는 빠른 실행으로 접힘 설정을 바꾸지 않고 기록에 접근할 수 있게 한다. 가장자리 접근과 클릭이 겹치는 별도 임시 패널은 이번 범위에 추가하지 않는다.
- 작업 트리·팀 공간·즐겨찾기·새 탭: 현재 평면 기록 조회에 새로운 분류 체계를 요구한다. 탐색 개선 범위를 넘는다.
- 시스템 전체 검색이나 DB 전체 검색처럼 보이는 명령창: 현재 제공하는 읽기 API 범위를 지킨다. ‘불러온 작업’이라는 범위를 명령창에도 표시한다.
- 색상·브랜드 로고 교체: 문제는 조작과 정보 구조다. JEV의 로고·graphite/lime 색상은 유지한다.

최종 구현 항목과 확인 기준은 [재적용 계획](desktop-ux-plan.md)에 연결한다.
