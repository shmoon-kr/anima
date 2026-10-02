# M5 선택안: 행동 선택 방식과 Sigil 형식

두 가지를 골라야 M5 를 시작할 수 있다 (PHASE-1 ★, D13).
같은 세 예제를 각 방식으로 써 보고 비교한다. 예제의 수치는 SPEC-from-tintin.md 의 현재 정책값이다.

- 예제 1. 평시 욕구 경쟁: 배고픔·휴식·사냥·연습 중 지금 할 일 고르기
- 예제 2. 성직자: 누구를 무슨 주문으로 치료할까
- 예제 3. 리더: 파티원이 낙오했을 때 기다리기/집결지로 가기/계속 사냥

공통 전제: 반사(도주·위험 회피·깨기)는 두 방식 모두 바깥에서 먼저 끼어든다. 여러 단계 절차(길드 연습, 시내 원정)는 두 방식 모두 작업(시퀀서)이 실행한다.
여기서 비교하는 것은 "지금 어떤 행동/작업을 할까"를 정하는 층 하나다.

---

## 선택 1. 행동 선택: 유틸리티 AI vs 행동 트리

### A. 유틸리티 AI
각 행동이 지금 상황에서 0~1 의 점수를 내고, 가장 높은 것을 한다. 점수 = 고려사항(consideration) 점수들의 곱 × 가중치.
고려사항은 입력 하나를 곡선으로 0~1 에 대응시킨 것이다. 현재 하고 있는 행동에는 관성 보너스를 주어 오락가락하지 않게 한다.

예제 1
```yaml
behaviors:
  eat:
    weight: 1.0
    considerations:
      - {input: self.hunger, curve: step(0.5)}          # 배고프면 1
      - {input: self.has_food_or_provider, curve: bool}
    do: task eat_or_ask_food
  rest:
    weight: 0.9
    considerations:
      - {input: self.hp_pct, curve: inverse_linear(policy.rest_pct, 90)}  # rest_pct 이하에서 1, 90% 에서 0
      - {input: room.safe_to_rest, curve: bool}
    do: task rest_until(hp_pct >= 90)
  hunt:
    weight: 0.7
    considerations:
      - {input: room.target_present, curve: bool}
      - {input: self.hp_pct, curve: linear(policy.flee_pct, 60)}         # 도주 기준 근처면 0, 60% 이상이면 1
      - {input: party.all_here, curve: bool}
    do: attack(pick_target(policy.targets, policy.shun))
  train:
    weight: 0.5
    considerations:
      - {input: self.practices_spare, curve: step(1)}
      - {input: self.hp_pct, curve: step(0.7)}
    do: task train_at_guild
inertia: 0.1
```
Vallen(rest_pct 30, flee_pct 45), 배고프지 않음, 방에 kobold, 파티 전원 있음:
- 체력 60%: rest = 0.9 × (90−60)/(90−30) = 0.45, hunt = 0.7 × 1 × 1 × 1 = 0.70 → **사냥**
- 체력 35%: rest = 0.9 × (90−35)/60 = 0.83, hunt = 0.7 × 0 (도주 기준 아래) = 0 → **휴식**

같은 규칙에서 상황에 따라 결과가 자연스럽게 바뀐다.

예제 2 (행동 하나 = "치료", 대상과 주문은 점수로 고름)
```yaml
  heal:
    weight: 1.0
    targets: party.members_in_room          # 대상마다 점수를 따로 낸다
    considerations:
      - {input: target.hp_pct, curve: inverse_linear(role.heal_below, 100)}   # healer 55/80, provider 35/60
      - {input: target.max_hp, curve: inverse_linear(0, 200)}                # 약한 사람 우선
      - {input: self.mana, curve: step(30)}
    do: cast(best_of([heal: mana>=60, cure_critic: mana>=30, cure_light: mana>=30]), target)
```

예제 3
```yaml
  wait_for_straggler:                       # 리더 역할 패키지
    weight: 1.0
    considerations:
      - {input: party.lost_seconds, curve: window(0, 60)}     # 낙오 직후 1분: 제자리
    do: wait
  regroup_at_rally:
    weight: 1.0
    considerations:
      - {input: party.lost_seconds, curve: window(60, 240)}   # 1~4분: 집결지로 가서 기다림
    do: task go_to(party.rally) then wait
  hunt:  # (위와 같음) party.all_here 가 0 이면 점수 0
```

### B. 행동 트리
위에서부터 조건을 검사해 처음 통과하는 가지를 실행한다 (Selector = 우선순위 목록, Sequence = 조건과 동작의 연쇄).

예제 1
```yaml
selector:
  - sequence: [{cond: "self.hungry and self.has_food_or_provider"}, {task: eat_or_ask_food}]
  - sequence: [{cond: "self.hp_pct < policy.rest_pct and room.safe_to_rest"}, {task: rest_until_ready}]
  - sequence: [{cond: "room.target_present and party.all_here and self.hp_pct > 50"}, {do: attack_target}]
  - sequence: [{cond: "self.practices_spare >= 1"}, {task: train_at_guild}]
  - {do: explore}
```
예제 2
```yaml
selector:
  - sequence: [{cond: "lowest_member.hp_pct < role.heal_below and self.mana >= 60"}, {do: "cast heal lowest_member"}]
  - sequence: [{cond: "lowest_member.hp_pct < role.heal_below and self.mana >= 30"}, {do: "cast cure_critic lowest_member"}]
  - ...
```
예제 3
```yaml
selector:
  - sequence: [{cond: "party.lost_seconds between 0 and 60"}, {do: wait}]
  - sequence: [{cond: "party.lost_seconds between 60 and 240"}, {task: go_rally_and_wait}]
  - {subtree: hunt}
```

### 비교
| 기준 | 유틸리티 AI | 행동 트리 |
|---|---|---|
| 경쟁하는 욕구 | 점수로 저울질. "체력 60% 인데 쉬기 vs 사냥" 같은 줄다리기를 그대로 표현 | 순서가 곧 우선순위. 줄다리기는 조건 숫자(>50)를 손으로 맞춰야 한다 — tintin 의 if 사다리와 같은 모양 |
| 진동 방지 | 관성 보너스 한 값으로 전체에 적용 | 가지마다 히스테리시스 조건을 따로 써야 함 |
| LLM 이 조정하기 | 가중치·곡선 숫자만 바꾸면 됨 (D16 의 "가중치 조정"과 바로 맞음). 구조를 안 건드리므로 검증이 쉽다 | 조건식이나 순서를 고쳐야 함. 구조 변경이라 검증과 부작용 예측이 어렵다 |
| 설명 ("왜 이걸 했나") | 후보별 점수표가 그대로 이유 (`source.scores`, D11) | 통과한 가지 경로. 왜 다른 가지가 아닌지는 앞 조건들을 봐야 함 |
| 패키지 겹쳐 쌓기 (D12) | 행동 하나를 추가·교체·가중치 조정해도 다른 행동과 독립 | 트리 안의 위치가 의미라서, 덮어쓰기가 "어디에 끼울지" 문제가 됨 |
| 남이 쓴 규칙의 안전 | 둘 다 선언형이라 같음 | 같음 |
| 단점 | 곡선 설계가 처음엔 낯설다. 엄격한 "반드시 먼저" 순서가 필요한 경우엔 가중치를 크게 줘야 함 | 단순한 경우엔 읽기 쉽다 |

**권장: 유틸리티 AI.** 이 프로젝트의 핵심(경쟁하는 욕구, LLM 의 가중치 조정, 점수로 설명, 패키지 덮어쓰기)에 모두 맞는다.
엄격한 순서가 필요한 것(생존)은 이미 반사 층이 맡으므로 유틸리티의 약점이 크지 않다.

---

## 선택 2. Sigil 형식

두 후보 모두 순수 식(부작용·반복·재귀 없음)만 허용하고, 엔진의 API 목록(SIGIL-API.md) 밖의 것은 쓸 수 없다. 제한 Python 은 제외 (D12, 남이 쓴 코드 실행 문제).

### (a) YAML 선언형 + 작은 식 언어
```yaml
sigil: 0                      # 요구 API 버전
package: class-cleric
policies:
  heal_below_fight: 55
  heal_below_idle: 80
behaviors:
  heal:
    weight: 1.0
    targets: party.members_in_room
    considerations:
      - {input: target.hp_pct, curve: "inverse_linear(policy.heal_below, 100)"}
    do: "cast(best_spell(), target)"
reflexes:
  wake_when_hit:
    on: combat.hit
    if: "event.victim == 'self' and self.position == 'sleeping'"
    do: [wake, stand]
tasks:
  train_at_guild:
    steps:
      - go_to: "role.guild_room"
      - repeat: {do: "practice(next_skill())", while: "self.practices_spare > 0", max: 20}
      - go_back: true
    timeout_s: 900
```
- 장점: 구조는 YAML 스키마로, 식은 작은 파서로 검증. 편집기·diff·LLM 출력 모두 익숙한 형식. 패키지 덮어쓰기가 키 단위 병합으로 자연스럽다.
- 단점: 식이 문자열 안에 들어가 따옴표가 번거롭다. 들여쓰기 실수.

### (b) 전용 문법
```
package class-cleric requires sigil 0

policy heal_below_fight = 55

behavior heal weight 1.0
  for target in party.members_in_room
  consider target.hp_pct by inverse_linear(policy.heal_below, 100)
  do cast(best_spell(), target)

reflex wake_when_hit on combat.hit
  if event.victim == self and self.position == sleeping
  do wake, stand
```
- 장점: 사람이 읽기 가장 좋다. 따옴표 없음.
- 단점: 파서·오류 메시지·편집기 지원을 전부 직접 만들어야 한다. LLM 이 처음 보는 문법이라 문법 실수가 늘어 검증 거부가 잦아진다. 덮어쓰기 병합 규칙도 새로 정의해야 한다.

**권장: (a) YAML + 작은 식 언어.** 식 언어(비교·산술·and/or/not·API 함수 호출·필드 접근)는 어느 쪽이든 만들어야 하니, 차이는 바깥 구조다.
YAML 쪽이 검증·병합·LLM 출력 모두에서 비용이 작다. 나중에 사람용 편집 화면(4단계 자연어 Sigil)이 생기면 그 화면이 (b) 같은 보기 좋은 표현을 대신 맡을 수 있다.
