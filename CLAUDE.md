# anima

MUD 세계에서 AI 에이전트(와 사람)가 살아가게 하는 런타임. tintin++ 를 대체하는 "에이전트 우선" 클라이언트이자,
장기적으로는 관전·빙의·에이전트 공유가 붙는 웹서비스의 중심(허브)이 된다.

세션을 시작하면 먼저 읽을 것:
- `docs/VISION.md`   전체 그림과 용어 (거의 안 바뀜)
- `docs/PHASE-2.md`  지금 단계의 목표, 범위, 완료 조건  ← 현재 작업
- `docs/PHASE-1.md`  끝난 1단계와 결과, 기준선은 `docs/BASELINE.md`
- `docs/DECISIONS.md` 이미 내린 결정과 이유. 뒤집으려면 먼저 사용자에게 물을 것
- `docs/SPEC-from-tintin.md` 1단계 행동 명세 (mud-agents 에서 옮긴 "무엇")

## 용어 (코드, 문서, 명령에서 일관되게)
- **Anima**: 프로젝트 전체 이름이자 반사 계층(밀리초, 규칙). 동양의 백(魄)
- **Animus**: 판단·전략 계층(로컬 LLM 수십 초, Claude 시간 단위). 동양의 혼(魂)
- **Sigil**: 반사 규칙. 사람이 쓰든 LLM이 쓰든 같은 형식, 반드시 검증 후 적용
- **Memoria**: 세계 모델 (지도 그래프, 몹/아이템 지식, 에이전트 기억)
- **Speculum**: 관전 화면 / **Genius**: 하루 단위로 에이전트를 진화시키는 계층
- 동작: `animate`(에이전트 생성·기동), `inspire`(사람이 빙의), `exhale`(빙의 해제)
- **Anima Mundi**: 나중에 새로 쓸 머드 엔진 (지금은 만들지 않음)

## 원칙
1. 런타임은 서버 텍스트를 직접 해석하지 않는다. **어댑터**가 텍스트를 구조화된 이벤트로 바꾸고, 위 계층은 이벤트만 본다.
2. **이벤트 프로토콜이 계약서**다 (`docs/PROTOCOL.md`, 1단계에서 작성). 엔진이 바뀌어도 어댑터만 바뀐다.
3. 세 개의 시계: 반사(ms) / 판단(수십 초) / 전략(시간). 실시간 행동은 절대 LLM 응답을 기다리지 않는다.
4. LLM이 만든 모든 것(Sigil, 설정)은 검증기를 통과해야 적용된다. 실패하면 이전 것을 유지.
5. 녹화된 로그 재생으로 테스트할 수 있어야 한다. 실서버는 마지막 확인용.

## 환경
- 테스트 서버: tbaMUD 2025, `192.168.1.101:4000` (SarahHome). 캐릭터 6인(D8): Vallen(전사·리더), Lil(마법사), Senia(전사), Lumina(성직자), Elysia(성직자), Carmilla(도적)
- 비밀번호: 저장소 밖 또는 gitignore 된 secret 파일. 관리자 캐릭터 Evan 은 절대 사용하지 말 것
- 로컬 LLM: LM Studio `localhost:1234/v1` (qwen/qwen3.8-27b, thinking 은 빈 `<think></think>` 프리필로 끔)
- 전략 LLM: `claude -p` (API 키 없음)
- 언어: Python 3, asyncio

## tbaMUD 에서 실제로 밟은 함정 (어댑터가 처리해야 함)
- 접속 후 1.5초간 클라이언트 탐지 중이라 그 사이 입력은 버려진다
- 응답이 프롬프트 뒤에 같은 줄로 붙어 온다 (`26H 100M 85V > That player is not here.`)
- 방 이름과 몹이 같은 색. 구분은 순서로: 방 이름 → 들여쓴 설명 → `[ Exits: ]` → 물건/몹/플레이어 → 프롬프트
- 대상 없음: `That player is not here.` / wimpy 는 `toggle wimpy N` / autoassist·autoloot·autogold·autosplit 은 토글
- autoassist 는 같은 방, 서 있는, 같은 그룹의 누구든 싸우면 돕는다 (리더만이 아님)
- 어둠 속에서는 방 내용도 자기 인벤토리 이름도 안 보인다. `exits` 는 방향은 알려줌
- 위험 출구: 길드 안쪽 방(Tournament Yard, Secret Yard, Clerics' Inner Sanctum)의 d, The Dump 의 d (하수도), Mid-Air (탈출 불가)
- follow / group join 은 같은 방에서만. 성직자 create food/water 는 레벨 2부터

## 워크스페이스에 함께 있는 것 (마음대로 읽고 활용할 것)
같은 워크스페이스에서 아래를 모두 볼 수 있다. 정확한 경로는 워크스페이스에서 확인.
- **tbaMUD 소스** (`src/`, `lib/world/`): 서버의 실제 동작이 여기 있다.
  - 게임 메시지 문자열은 추측하지 말고 소스에서 grep 해서 어댑터 패턴을 만든다 (`act.*.c`, `fight.c`, `spell_parser.c` 등)
  - 규칙이 애매하면 소스를 읽고 판단한다 (예: autoassist 조건은 `fight.c`, 접속 협상은 `comm.c`)
  - `lib/world/` 의 방·몹·물건·상점 파일로 Memoria 를 미리 채울 수 있다 (길드마스터 위치, 상점, 몹 레벨, 위험 지역)
  - 소스 수정(GMCP 필드 추가 등)은 실행 중인 서버에 영향을 주므로 반드시 사용자에게 먼저 물을 것
- **tintin++ 소스**: 기존 스크립트가 왜 그렇게 동작했는지 확인할 때 (`trigger.c`, `net.c`, `regex.c` 등)
- **mud-agents** (tintin 기반 이전 실험): 1단계의 "무엇을 하는가" 명세 (D15). 지도, 길드 경로, 위험 출구, 행동 목표, 정책값은 이식할 지식이다.
  "어떻게"는 이식하지 않는다: tintin 우회 코드, tell 파티 신호 체계(D14), 상태 변수, tick 우선순위 사다리(D13). `logs/` 는 재생 테스트 재료.

원칙: **동작이 불확실하면 추측해서 고치지 말고 소스를 먼저 읽는다.** 이전 실험에서 추측 수정이 반복 실패의 주원인이었다.
