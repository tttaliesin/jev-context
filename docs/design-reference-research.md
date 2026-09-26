# JEV 데스크톱 디자인 레퍼런스 조사

조사일: 2026-09-26. 당시 관찰 대상은 **0.3.0**의 Electron 관리 앱이다. 로컬 모델 준비·종료, 연결 점검, 저장 작업 조회를 제공하며 코딩 대화는 Codex에서 계속한다. 조사 뒤 설계 방향을 **0.3.1**에 적용했다.

## 추천

**Oxide를 시각적 기준으로, Linear의 실제 앱을 화면 구조의 기준으로 추천한다.** GPU와 작업 상태를 읽고 조작하는 제품에 맞는 방향이다. Raycast는 키보드와 액션 배치, Geist와 Supabase는 상태·목록·빈 화면의 세부 규칙에 참고한다. 이는 이 프로젝트 목적에 따른 설계 판단이며 각 회사의 권고가 아니다.

조사 결과는 처음에 [JEV DESIGN.md](../DESIGN.md)의 설계 초안으로 구체화했다. 조사 단계에서는 앱을 변경하지 않았고, 이후 사용자의 진행 요청에 따라 0.3.1의 화면·키보드 동작과 실행 파일에 반영했다. 이 문서는 선정 근거를 보존하며, 적용 내용과 실제 검증 결과는 [데스크톱 관리 앱 실행 안내](desktop-manager.md)에 분리해 기록한다.

## 공개 DESIGN.md 모음

| 모음 | 실제 성격 | 쓰기 좋은 방식 |
| --- | --- | --- |
| [VoltAgent / awesome-design-md](https://github.com/VoltAgent/awesome-design-md) | 공개 사이트에서 분석한 토큰·컴포넌트·레이아웃 문서. 브랜드 공식 문서와 구분해야 한다. 저장소 MIT 표기 | 브랜드별 문서와 preview를 찾아 시각적 후보를 추리는 시작점 |
| [VoltAgent / official-design-md](https://github.com/VoltAgent/official-design-md) | 회사·프로젝트가 직접 공개한 문서의 링크 모음 | 링크를 따라 원 발행자의 문서와 적용 대상을 확인 |
| [Duply-AI / design-md](https://github.com/Duply-AI/design-md) | 사이트를 분석한 DESIGN.md 모음. README는 조사 시점 311개를 안내. CC BY 4.0 표기 | 다양한 후보 비교. 실제 문서를 재사용할 때 출처·변경 사실을 함께 관리 |

분석 문서에 붙은 라이선스가 해당 회사의 로고·상용 폰트·제품 전체에 적용되는 것은 아니다. 이번에는 원문 파일·폰트·브랜드 자산을 프로젝트에 복제하지 않고 출처와 필요한 원칙을 정리했다.

## 엄선한 후보

### 1. Oxide — 가장 강한 시각적 방향

- [공식 design.md](https://github.com/oxidecomputer/design-system/blob/master/design.md)
- [실제 Console 소스와 원칙](https://github.com/oxidecomputer/console#readme)
- [공식 화면 이미지가 있는 가이드](https://docs.oxide.computer/guides/quickstart)

어두운 중성 바탕, 제한된 녹색 강조, 작은 곡률, 의미별 색상과 고정폭 숫자가 기술 장비를 다루는 인상을 만든다. 공식 문서는 surface/content/stroke 역할을 분리한다. Console은 API의 실제 개념과 상태를 명료하게 드러내는 방향을 설명한다.

JEV에서는 모델 이름·상태·사용 가능한 조작을 한 덩어리로 읽게 하는 데 적합하다. 영문 대문자와 고정폭 글꼴을 한국어 본문 전체에 적용하지는 않는다. 원본의 브랜드 폰트를 가져오는 대신 한국어 가독성과 숫자 정렬을 별도로 설계한다.

### 2. Linear — 실제 앱의 공간 배분

- [공식 2026-03-12 UI 개편](https://linear.app/now/behind-the-latest-design-refresh)
- [공식 2024 앱 리디자인 과정](https://linear.app/now/how-we-redesigned-the-linear-ui)
- [커뮤니티 DESIGN.md](https://github.com/VoltAgent/awesome-design-md/blob/main/design-md/linear.app/DESIGN.md)

2026년 자료는 덜 두드러지는 사이드바, 작은 탭, 줄어든 아이콘 장식, 부드러운 구분선, 따뜻한 중성색을 설명한다. 위치와 동작을 일관되게 배치해 본문에 집중하게 만드는 점을 채택한다.

공개 MD의 실제 경로는 `linear.app`이다. 이 문서는 마케팅 사이트를 분석했으며 80px 제목과 96px 섹션 간격이 포함된다. 이를 데스크톱 앱의 실측 규격으로 취급하지 않는다. JEV에서는 반복되는 프로젝트 제목을 줄이고 모델 조작과 작업 상세에 공간을 배분한다.

### 3. Raycast — 선택과 동작의 연결

- [공식 ActionPanel](https://developers.raycast.com/api-reference/user-interface/action-panel)
- [공식 앱 UI 개편](https://www.raycast.com/blog/a-fresh-look-and-feel)
- [커뮤니티 DESIGN.md](https://github.com/VoltAgent/awesome-design-md/blob/main/design-md/raycast/DESIGN.md)

선택 항목에 연결된 동작, 기본 동작의 우선순위, 의미별 액션 묶음이 유용하다. JEV의 목록 탐색·검색·연결 점검을 빠르게 만드는 참고자료다.

공개 MD는 마케팅 페이지를 분석한 문서다. 빨간 히어로 장식이나 큰 홍보용 여백은 채택하지 않는다. Windows에는 실제 구현한 Windows 단축키를 표시하고, 모델 상태를 계속 관찰해야 하는 화면을 명령 팔레트 하나로 축소하지 않는다.

### 4. Vercel Geist — 컴포넌트의 완성도

- [공식 Geist](https://vercel.com/geist/introduction)
- [Table](https://vercel.com/geist/table), [Status Dot](https://vercel.com/geist/status-dot)
- [공식 design.md](https://vercel.com/design.md)

목록과 개별 항목 상세의 구분, 숫자 정렬, 상태 표시, 빈 결과, 미확인 값의 처리에 참고하기 좋다. 단순한 화면도 행 높이와 타이포 위계가 일정하면 안정적으로 읽힌다.

Vercel의 `/design.md`는 Vercel 명의의 보고서·제안서 사이트용 문서다. 관리 앱의 주요 참고자료는 제품 컴포넌트를 설명하는 Geist로 선정했다. 검은 배경과 흰 글씨만 따라 하는 것으로 설계가 완성되지는 않는다.

### 5. Supabase — 운영 화면의 구체적 패턴

- [공식 Studio 레이아웃](https://supabase.com/design-system/docs/ui-patterns/layout)
- [공식 빈 상태](https://supabase.com/design-system/docs/ui-patterns/empty-states)
- [커뮤니티 DESIGN.md](https://github.com/VoltAgent/awesome-design-md/blob/main/design-md/supabase/DESIGN.md)

헤더와 콘텐츠 너비, 해당 상태 가까이에 놓이는 조작, 최초 사용과 검색 결과 없음의 구분이 JEV에 직접 도움이 된다. 모델 미설정·저장 작업 없음·필터 결과 없음·조회 실패에 서로 다른 문구와 행동을 제공한다. 이 원칙을 위해 React 라이브러리 전체를 도입할 필요는 없다.

### 6. Resend — 절제된 글자와 상태색

- [공식 design.md](https://resend.com/design.md)
- [공식 브랜드 문서](https://resend.com/design)
- [공식 디자인 스킬 저장소](https://github.com/resend/design-skills)

본문과 코드의 글꼴 역할, 간결한 흑백 위계와 의미가 일정한 상태색을 참고한다. 마케팅용 디스플레이 글꼴과 장식 효과를 제품 UI와 분리한다. 커뮤니티의 Forward 행사 페이지 분석과 공식 제품 자료를 혼동하지 않는다.

Twenty도 [공개 MD](https://github.com/Duply-AI/design-md/blob/main/designs/twenty/DESIGN.md)와 [공식 입력 컴포넌트](https://docs.twenty.com/twenty-ui/input/text)를 비교했다. 입력 라벨·오류 배치는 유용하지만 CRM의 아바타·다색 태그를 이 앱에 추가할 이유는 약해 보조 후보로 남긴다.

## 조사 당시 0.3.0 화면에서 바꿀 이유

이 평가는 조사 당시 0.3.0의 `desktop/renderer/index.html`, `style.css`와 실제 앱 검증 화면을 기준으로 한 프로젝트 자체 분석이다. 아래 관찰은 현재 0.3.1 화면에 대한 지적이 아니며, 오른쪽 대응은 이후 0.3.1에 반영했다.

| 0.3.0 관찰 | 0.3.1에 반영한 대응 |
| --- | --- |
| 프로젝트 이름이 사이드바·큰 제목 아래 띠에 반복된다 | 프로젝트 선택과 현재 위치를 상단 한 곳에 모은다 |
| 모델과 연결 상태가 큰 카드 두 개로 같은 시각적 무게를 차지한다 | 모델 상태와 실행 조작을 먼저 배치하고 연결 세부는 명확한 요약과 펼침 영역으로 제공한다 |
| 작은 보조 정보가 9–11px에 많이 의존한다 | 필수 보조 정보는 12–13px, 읽는 본문은 14px을 기준으로 한다 |
| 카드 테두리·배지·설명·영문 장식 제목이 동시에 강조된다 | 탐색 영역의 강조를 낮추고 선택 항목·문제·다음 조작을 강조한다 |
| 작업 상세가 큰 상태 카드 아래로 밀린다 | 첫 화면에서 모델 조작과 선택 작업의 핵심을 함께 보이게 한다 |

0.3.0의 `--faint: #65768d`와 `--background: #101722` 조합의 상대 명도 대비는 **약 3.88:1**이었다. 사이드바 바탕 `#0b111b`에는 약 4.08:1이었다. 로컬 계산이며 전체 화면의 접근성 감사 결과는 아니다. 작은 일반 텍스트에 쓰이는 경우 [WCAG의 4.5:1 기준](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)에 못 미쳤다. 0.3.1은 graphite 바탕과 밝아진 보조 글자로 바꾸었으며, 적용한 색상과 개별 대비 계산은 [DESIGN.md](../DESIGN.md)에 기록했다.

## 0.3.1 적용에 사용한 우선순위

1. 프로젝트·모델·작업을 중심으로 정보 순서를 정리한다.
2. 중성 바탕, 한국어 타이포, 간격과 곡률을 한 규칙으로 통일한다.
3. 대기·준비 중·준비 완료·판단 중·오류·조회 실패 상태를 같은 구조에서 검토한다.
4. 실제 준비·종료 및 상태 정확성을 유지한 채 목록 선택과 키보드 조작을 다듬는다.
5. 940×690과 1220×860, Windows 배율과 키보드 확대에서 다시 확인한다.

## 출처 관리

각 링크의 본문과 적용 범위를 확인했다. 직접 링크가 웹 읽기 도구에서 실패한 일부 공식 MD는 보조 조사에서 원문을 교차 확인했다. 링크·기본 브랜치·문서는 이후 변경될 수 있다. 이번 문서는 전체 라이브러리를 검증했다고 주장하지 않으며, 실제 비교한 후보와 앱에 대한 판단을 기록한다.

- [VoltAgent 분석 문서 MIT](https://github.com/VoltAgent/awesome-design-md/blob/main/LICENSE)
- [Duply 분석 문서 CC BY 4.0](https://github.com/Duply-AI/design-md/blob/main/LICENSE)
- [Oxide design-system MPL 2.0](https://github.com/oxidecomputer/design-system/blob/master/LICENSE)
- [Geist 폰트 OFL](https://github.com/vercel/geist-font/blob/main/OFL.txt)

위 표기는 해당 자료에서 확인한 라이선스의 범위다. 조사에서 제안하고 0.3.1에 적용한 색상값·레이아웃 치수·한국어 규칙은 JEV용으로 정했으며 원 브랜드의 공식 토큰이라고 표시하지 않는다.
