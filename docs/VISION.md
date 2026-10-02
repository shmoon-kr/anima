# Anima — 전체 그림

## 한 줄
사람이 만든 분신(에이전트)이 텍스트 세계에서 대신 살아가고, 사람은 그걸 지켜보거나, 지휘하거나, 직접 들어가 함께 논다.

## 왜 새로 만드는가
tintin++ 위에서 LLM 에이전트를 돌려보니 문제의 대부분이 하나로 수렴했다:
**사람 눈을 위해 만든 텍스트를 기계가 억지로 읽고 있다.**
프롬프트 뒤에 붙는 응답, 한 줄에 하나만 도는 action, 색으로 구분 못 하는 방과 몹, 괄호 하나에 조용히 죽는 스크립트.
Anima 는 처음부터 "구조화된 지각 + 계층화된 판단"을 뼈대로 갖는다.

## 구조
```
 [사람/LLM 인터페이스]  MCP 서버(LLM이 감독) · Speculum(관전 웹) · 코치(자연어 지시) · 터미널 클라이언트
          │
 [허브]   세션 소유. 관전 = 이벤트 스트림 구독, 빙의 = 입력 권한 이전
          │
 [런타임] Animus 전략 (Claude, 시간)
          Animus 판단 (로컬 LLM, 수십 초)
          Anima 반사 (Sigil, ms)
          Memoria 세계 모델
          │
 [어댑터] 텍스트(CircleMUD 지금) · GMCP/MSDP(tbaMUD 확장) · JSON(Anima Mundi 나중)
          │
 [MUD 서버]
```
모든 캐릭터는 런타임/허브를 거쳐 접속한다. 그래서 관전과 빙의는 엔진 종류와 무관하게 허브에서 구현된다.

## MCP 에 대해
LLM이 `kill`, `flee` 를 직접 치게 하면 실시간 전투가 불가능하다.
MCP 는 게임이 아니라 런타임을 연다: `get_situation`, `set_policy`, `set_goal`, `write_sigil`.
LLM은 선수가 아니라 감독이다.

## 사람이 즐기는 방식
- 자연어로 Sigil 쓰기 → LLM이 규칙으로 번역 → 검증 → 사람이 승인
- 코치: 돌고 있는 에이전트에 채팅으로 끼어들기
- inspire / exhale: 언제든 빙의했다가 돌려주기
- 에이전트·Sigil 공유와 포크, 다른 사람 에이전트와 같은 세계에서 만나기
- 옛 tintin 매크로 가져오기 (원문 텍스트 트리거 계층 + 변환기)

## 로드맵
1. 이벤트 프로토콜 v0 + Anima 반사 런타임 + 텍스트 어댑터 → tintin 없이 Vallen 이 기존 수준으로 동작
2. Animus 이식 (mud-agents 의 scout/strategist) + 파티 5인
3. 허브 + Speculum (웹 관전), MCP 서버
4. inspire/exhale, 사람용 웹·터미널 클라이언트, 자연어 Sigil
5. tbaMUD 에 GMCP 필드 추가 → 텍스트 파싱 축소. 한글화(존 파일 LLM 번역)
6. Anima Mundi: 엔진이 정말 막힐 때 새로 (LLM NPC, 스스로 자라는 세계)
