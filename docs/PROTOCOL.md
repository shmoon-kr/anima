# Anima 이벤트 프로토콜 v0

런타임·허브·웹·에이전트가 아는 유일한 형식 (D2).
이 문서는 두 부분이다.

- **1부. 엔진 계약**: 봉투, 게임 이벤트, 명령. 텍스트 어댑터(tbaMUD), 나중의 GMCP/MSDP 어댑터, Anima Mundi 의 JSON 어댑터가 지켜야 하는 것은 **1부뿐**이다.
  엔진이 바뀌면 1부를 구현하는 어댑터만 바뀐다.
- **2부. Anima 내부**: 런타임 기록(`runtime.*`), LLM(`animus.*`), 녹화·정규화 규칙. 같은 봉투를 쓰지만 엔진·어댑터는 몰라도 된다.

이 문서가 바뀌면 `v` 를 올리고 맨 아래 변경 기록에 남긴다. v0 은 불안정 버전이다 (1단계 동안 바뀔 수 있음).
새 필드 추가는 호환 변경이다 (받는 쪽은 모르는 필드를 무시한다). 필드 삭제·의미 변경·타입 변경은 호환을 깨는 변경이다.

---

# 1부. 엔진 계약

## 1. 원칙
- **세계에서 일어난 일만** 게임 이벤트가 된다. 앱의 규약(파티 약속어 등)은 이벤트가 아니다 (D10). tell 은 "누가 누구에게 뭐라고 했나"까지만.
- 어댑터는 해석에 자신 없는 줄을 추측해서 이벤트로 만들지 않는다. `unknown` 으로 낸다.
- 색, 줄바꿈, 프롬프트 붙음 같은 표현 형식은 어댑터 안에서 끝난다. 위 계층은 원문을 몰라도 된다 (`raw` 는 디버깅용).
- 이름: 화면에 보인 그대로(`the beastly fido`)를 쓴다. 키워드 추출이나 몹 정체 판정은 Memoria 의 일이다.
- **자기 자신은 `"self"`** 로 쓴다 (서버가 "You" 로 보여준 것). 여러 에이전트의 이벤트를 합칠 때의 정규화 규칙은 2부 §8.
- **고유 ID 자리**: 방·존재(몹/플레이어)·물건을 가리키는 곳에는 선택 필드 `id` 를 둔다. 엔진이 알려주면 채우고, 모르면 비운다.
  텍스트 어댑터는 비우고(방 번호는 Memoria 가 추정), GMCP/JSON 어댑터는 채운다. 받는 쪽은 `id` 가 없을 수 있다고 가정한다.
- **정확한 수치 자리**: 텍스트에서 근사만 가능한 값(피해량 등)은 근사 필드와 별도로 정확한 값의 선택 필드를 둔다.
- **힌트**: 어댑터가 확실히 아는 단서는 `hints` 에 붙인다. 판단은 위 계층이 한다.

## 2. 봉투 (모든 메시지 공통, 2부 포함)
```json
{"v": 0, "t": 1759400000.123, "seq": 1042, "agent": "Vallen", "type": "combat.hit", "data": {…}, "raw": ["…"]}
```
| 필드 | 뜻 |
|---|---|
| `v` | 프로토콜 버전 |
| `t` | 유닉스 시각(초, 소수). 재생 시에는 녹화 시각 |
| `seq` | 에이전트별 단조 증가 번호. 명령 출처와 LLM 답이 원인 이벤트를 가리킬 때 쓴다 |
| `agent` | 이 이벤트를 겪은 캐릭터 (세션 단위). `"self"` 를 이름으로 바꿀 때의 기준 |
| `type` | 아래 목록 중 하나 |
| `data` | 타입별 내용 |
| `raw` | (선택) 이 이벤트를 만든 원문 줄들, ANSI 제거본 |

## 3. 게임 이벤트 (어댑터 → 버스)

공통 표기: `id?` 는 선택 고유 ID, `?` 는 선택 필드.
존재 참조(`attacker`, `victim`, `who`, `from` 등)는 이름 문자열 또는 `"self"`. 같은 객체에 `*_id?` 로 고유 ID 를 붙일 수 있다 (예: `attacker_id`).

### 접속
| type | data | 비고 |
|---|---|---|
| `connection.opened` | `{host, port}` | |
| `connection.closed` | `{reason: remote\|local\|error, detail?}` | |
| `connection.login_prompt` | `{stage: name\|password\|confirm_name\|press_return\|menu}` | 로그인 대화는 세션 관리가 처리 |
| `connection.login_failed` | `{reason: invalid_name\|wrong_password}` | |
| `connection.in_game` | `{how: entered\|reconnected\|took_over}` | `Welcome to tbaMUD!  May…`, `Reconnecting.`, `You take over your own body…` |

### 상태
| type | data | 비고 |
|---|---|---|
| `prompt` | `{hp, mp, mv}` (없는 값은 null) | 서버 출력 한 덩어리의 끝. 텍스트 어댑터는 프롬프트로 블록을 나눈다 |
| `char.vitals_max` | `{hp, mp, mv}` | `score` 의 `You have N(M) hit…` |
| `char.score` | `{level?, exp?, gold?, practices?, …}` | 얻은 필드만 |
| `char.skills` | `{skills: {name: proficiency}}` | `practice` 목록. proficiency 는 `not learned…superb` 단계 문자열 |
| `char.practiced` | `{skill?, result: improved\|learned\|maxed\|cannot, reason?: no_practices\|unknown_skill}` | |
| `level.up` | `{levels}` | `You rise a level!` / `You rise N levels!` |
| `exp.gain` | `{amount, kind: share\|solo}` | |
| `condition` | `{hungry?, thirsty?, full?, quenched?}` | 바뀐 것만 true/false |
| `position` | `{position: standing\|sitting\|resting\|sleeping, awakened_by?}` | 자기 자세 변화 |
| `command.refused` | `{reason: dead\|incapacitated\|stunned\|sleeping\|resting\|sitting\|fighting\|unknown_command\|no_target\|linkless\|not_here\|not_allowed\|invalid_target}` | 서버가 명령을 받지 않음 (`interpreter.c:568-587` 자세 검사, `Huh!?!`, 대상 없음 등) |
| `affect.changed` | `{who, affect: poison, on: bool}` | 독 걸림/풀림 (`magic.c:455-456`, `spell_parser.c:867`). 다른 효과는 필요해지면 추가 |

### 세계
| type | data | 비고 |
|---|---|---|
| `world.time` | `{phase: sunrise\|day\|sunset\|night}` | `The sun rises in the east.` / `The day has begun.` / `The sun slowly disappears in the west.` / `The night has begun.` (`weather.c:50-62`, 실외에 있을 때만 보임). 밤에는 실외도 어두워진다 |
| `world.weather` | `{change: cloudy\|rain\|clear\|lightning\|rain_stops\|lightning_stops}` | `weather.c:162-182`. 실외에서만 |

### 방과 이동
| type | data | 비고 |
|---|---|---|
| `room` | `{id?, name, desc, exits: [{dir, closed, to_id?}], objects: [{id?, text, count}], occupants: [Occupant], dark: false}` | 방 블록 하나 |
| `room.dark` | `{id?, blind?}` | `It is pitch black...` — 방 내용 없음. `blind: true` 는 `You see nothing but infinite darkness...` (실명) |
| `room.exits_listed` | `{exits: [{dir, room_name: string\|null, closed, to_id?}]}` | `exits` 명령 결과. 어두우면 `room_name` 이 null (`Too dark to tell.`) |
| `move.failed` | `{dir?, reason: no_exit\|closed\|locked\|need_boat\|guarded\|forbidden\|exhausted\|too_relaxed\|other, door?}` | `door` 는 닫힌 문 이름 |
| `zone.above_level` | `{}` | 경고만, 이동은 이미 됨 |
| `occupant.arrived` | `{who, who_id?, from_dir?, how?: entered_game}` | `X has arrived.` / `X has entered the game.` |
| `occupant.left` | `{who, who_id?, dir?}` | `X leaves north.` |
| `follow.moved` | `{leader, dir?}` | `You follow X.` — 따라가기로 내가 움직임 |
| `occupant.position` | `{who, position: standing\|sitting\|resting\|sleeping}` | 같은 방 다른 존재의 자세 변화 (`X sits down and rests.` 등, `act.movement.c`) |
| `occupant.link` | `{who, state: lost\|reconnected}` | `X has lost his link.` / `X has reconnected.` |

**Occupant** (방 안 존재 한 줄) = `{id?, text, position?, fighting?, flags: [], hints: []}`
- `text`: 화면 그대로. 몹은 긴 설명(`A cityguard stands here.`)일 수도, 짧은 이름 + 자세(`The beastly fido is resting here.`)일 수도 있다.
- `position`, `fighting`: 문구에서 읽힐 때만 (`act.informative.c list_one_char`).
- `flags`: 서버가 붙인 표시 — `invisible`, `hidden`, `linkless`, `writing`, `buildwalk`, `afk`, `red_aura`, `blue_aura`, `sanctuary`, `blind`.
- `hints`: 어댑터가 확실히 아는 단서. 몹/플레이어 판정은 하지 않는다 (Memoria 몫).
  - `my_group` / `my_group_leader`: 줄 앞의 `(group) ` / `(leader) ` 가 굵은 초록 — 나와 같은 그룹 (`act.informative.c:313-321`)
  - `other_group` / `other_group_leader`: 같은 표시가 굵은 빨강 — 다른 그룹 (그룹은 플레이어나 그 추종자)
  - `player`: `linkless`·`writing`·`buildwalk`·`afk` 처럼 플레이어에게만 붙는 표시가 있을 때
  - `group_member`: 줄이 같은 세션의 최근 `group.status` 에 있는 이름으로 시작할 때
  - 그룹 표시는 `text` 에서 떼어 낸다 (`text` 는 이름부터).

### 전투
| type | data | 비고 |
|---|---|---|
| `combat.hit` | `{attacker, victim, verb, severity: 0..8, kind: weapon\|skill\|spell, damage?}` | `severity`: 무기는 `fight.c dam_weapons` 구간(0=빗나감 … 8=OBLITERATES). 기술·주문은 `lib/misc/messages` 의 miss/hit/die 를 0·4·8 로 근사. `damage` 는 엔진이 정확한 값을 줄 때만. 텍스트에서는 비우고, 필요하면 위 계층이 프롬프트 HP 차이로 계산 |
| `combat.condition` | `{who, state: stunned\|incapacitated\|mortally_wounded}` | |
| `combat.death` | `{who, who_id?}` | `X is dead!  R.I.P.` — 자신이 아닌 죽음 |
| `combat.death_cry` | `{who?, nearby: bool}` | |
| `self.died` | `{}` | `You are dead!  Sorry...` |
| `self.fled` | `{dir?}` | `You flee head over heels.` (텍스트에서는 새 방 `room` 이벤트가 먼저 온다) |
| `self.flee_failed` | `{reason: panic\|bad_shape}` | |
| `combat.wimpy` | `{}` | `You wimp out, and attempt to flee!` |
| `combat.flee_seen` | `{who}` | `X panics, and attempts to flee!` |
| `combat.assist` | `{who, target?}` | `You join the fight!`, `X assists Y.`, `X assists you!`, `X jumps to the aid of Y!`. `target` 은 도움받는 쪽 |
| `combat.rescue` | `{who, rescued}` | `X heroically rescues Y!` / `You are rescued by X, you are confused!` |
| `combat.aggro` | `{who, reason: remembered}` | 몹이 기억했다가 덮침: `'Hey!  You're the fiend that attacked me!!!', exclaims X.` (`mobact.c:151`) |
| `skill.result` | `{skill: string\|null, ok: bool, reason?: unknown_skill\|no_mana\|no_target\|need_weapon\|wrong_weapon\|too_alert\|lost_concentration\|fizzled\|failed}` | 시전·기술 사용 결과 문구. 텍스트만으로 기술 이름을 모르면 `skill` 은 null (보낸 명령과 맞추는 건 위 계층) |

### 의사소통
| type | data | 비고 |
|---|---|---|
| `comm.tell` | `{from, to, text, direction: in\|out}` | `X tells you, '…'` / `You tell X, '…'` |
| `comm.gtell` | `{from, text, direction}` | `[Group] X says, '…'` / `You group-say, '…'` |
| `comm.say` | `{from, text, direction}` | 방 안 말 |
| `comm.channel` | `{channel, from, text}` | gossip 등, 필요해지면 |

### 그룹
| type | data | 비고 |
|---|---|---|
| `group.status` | `{members: [{name, id?, hp, hp_max, mp, mp_max, mv, mv_max, leader: bool}]}` | `group` 표 전체 |
| `group.change` | `{event: joined\|left\|disbanded\|new_leader\|following\|followed_by\|stopped_following\|follower_left, who}` | 그룹 알림은 서버가 `[Group] ` 을 앞에 붙인다 (`comm.c:2562`) |
| `toggle.state` | `{name: autoloot\|autogold\|autosplit\|autoassist\|wimpy…, value}` | 토글 응답으로 확정된 값 |

### 물건
| type | data | 비고 |
|---|---|---|
| `items.inventory` | `{items: [{id?, text, count}]}` | `inventory` 결과 전체 |
| `items.equipment` | `{slots: [{slot, id?, text}]}` | `equipment` 결과 전체 |
| `items.got` | `{text, id?, from?}` | `You get X [from Y].` |
| `items.received` | `{text, id?, from}` | `X gives you Y.` |
| `items.gave` | `{text, id?, to}` | |
| `items.light_out` | `{who}` | `… sputters out and dies.` |
| `items.not_found` | `{keyword?}` | `You don't see …`, `You can't find it!` |
| `items.cannot_take` | `{text, reason?: too_many\|too_heavy}` | |
| `items.cannot_drop` | `{text, reason: cursed}` | `You can't junk X, it must be CURSED!` (`act.item.c:475`) |
| `items.give_failed` | `{to?, reason: hands_full\|too_heavy}` | |
| `items.used` | `{action: eat\|drink\|fill\|wear\|wield\|hold\|remove\|drop\|junk\|donate, text?, empty?}` | 확인된 결과만. `It is empty.` 는 `{action: drink, empty: true}` |
| `shop.list` | `{items: [{id?, text, price, count?}]}` | |
| `shop.result` | `{action: buy\|sell, ok, text?, reason?: too_many\|too_heavy}` | |

### 나머지
| type | data | 비고 |
|---|---|---|
| `unknown` | `{text}` | 어댑터가 모르는 줄. 커버리지 지표 |

## 4. 명령 (런타임 → 어댑터)
```json
{"type": "command.send", "data": {"text": "kick fido", "source": {…}, "secret": false}}
```
| 필드 | 뜻 |
|---|---|
| `text` | 보낼 텍스트 명령. v0 에는 의도 수준 명령(엔진 중립 `attack`, `move` 등)이 없다 |
| `source` | **필수.** 누가 왜 보냈는지 (형식은 2부 §6, D11). 어댑터는 내용을 해석하지 않고 그대로 되돌려준다 |
| `secret` | `true` 면 비밀(비밀번호 등). 어댑터와 녹화는 `text` 를 어디에도 남기지 않는다 (§5) |

어댑터는 보낸 명령마다 `command.sent{text, source, secret, queued_ms}` 를 버스에 낸다. `secret` 이면 `text` 는 `"***"`.

## 5. 비밀 보호
- 비밀번호 전송은 반드시 `secret: true` 로 보낸다.
- 이중 안전장치: 어댑터는 직전 이벤트가 `connection.login_prompt{stage: password}` 이면 `secret` 표시가 없어도 다음 명령을 비밀로 취급한다.
- 비밀 명령은 `command.sent`·`raw`·녹화·로그·관전 스트림 어디에도 원문이 나가지 않는다. 서버 에코가 꺼져 있어도 우리 쪽 기록에 남지 않게 하는 규칙이다.

---

# 2부. Anima 내부 (엔진·어댑터는 몰라도 됨)

## 6. 명령 출처 `source` (D11)
| 필드 | 뜻 |
|---|---|
| `kind` | `reflex` \| `behavior` \| `task` \| `party` \| `human` \| `system` |
| `id` | 레이어 포함 이름. 예: `class-cleric/heal`, `base/eat`. `human` 이면 입력 창구(`watch`), `system` 이면 `session/login` 등 |
| `reason` | 사람이 읽을 한 줄 이유 |
| `task_step?` | 작업이면 `{task_id, step}` |
| `trigger_seq?` | 원인이 된 이벤트의 `seq` (게임 이벤트 또는 `animus.response`) |
| `scores?` | 행동 선택이면 상위 후보와 점수 `[{id, score}]` |

출처 없는 명령은 명령 큐가 거부한다.

## 7. 런타임 기록 (`runtime.*`)
계층들의 결정. 관전 "왜 이렇게 움직였나"와 재생 분석에 쓴다.
| type | data |
|---|---|
| `runtime.reflex` | `{id, trigger_seq, action}` |
| `runtime.behavior` | `{from, to, scores: [{id, score}], trigger_seq?}` — 행동 전환 |
| `runtime.task` | `{task_id, name, event: started\|step\|paused\|resumed\|succeeded\|failed\|abandoned, step?, reason?}` |
| `runtime.party` | `{event: role_assigned\|goal_changed\|member_lost\|member_found…, detail}` |
| `runtime.sigil` | `{event: loaded\|rejected, package, errors?}` |

## 8. 이름 정규화 (여러 에이전트 합치기)
같은 사건을 Vallen 은 `"self"` 로, Lil 은 `"Vallen"` 으로 받는다.
**여러 에이전트의 이벤트를 합치는 모든 소비자(파티 블랙보드, 관전 화면, 통계)는 `"self"` 를 봉투의 `agent` 로 바꾼 뒤 합친다.**
- 정규화는 소비자 쪽 공용 함수 하나(`normalize(event)`)로 한다. 어댑터 출력과 녹화는 원래 관점(`"self"`) 그대로 둔다.
- 같은 사건이 여러 에이전트에게서 오면 중복이다. 블랙보드는 (정규화된 내용, 시각 근접)으로 중복을 합친다. 1단계는 `combat.death`, `occupant.arrived/left` 에만 적용.

## 9. Animus (LLM, D16)
LLM 은 느린 감각기관이다. 질문은 비동기, 답은 이벤트로 온다. 반사와 행동 선택은 답을 기다리지 않는다.
| type | data |
|---|---|
| `animus.request` | `{id, asker, tier: local_fast\|local_think\|claude, priority: danger\|stuck\|routine, question_kind, context, answer_schema, timeout_s, default}` |
| `animus.response` | `{id, answer, latency_s, provider, model}` — `answer_schema` 검증을 통과한 답만 |
| `animus.timeout` | `{id, used_default: true}` |
| `animus.rejected` | `{id, reason: schema\|budget\|stale\|provider_error, detail?}` |

- `asker` 는 명령 `source.id` 와 같은 형식이다 (`class-cleric/heal`).
- 녹화에는 공급자에게 실제로 보낸 프롬프트 원문과 답 원문이 함께 남는다 (재생 공급자가 그대로 돌려준다). 프롬프트에 들어가는 문맥에도 비밀 명령 원문은 넣지 않는다.
- 답을 근거로 보낸 명령은 `source.trigger_seq` 가 `animus.response` 의 `seq` 를 가리킨다.

## 10. 녹화
- JSONL, 한 줄에 봉투 하나. 게임 이벤트, `command.sent`, `runtime.*`, `animus.*` 를 모두 같은 파일에 시간순으로.
- 녹화는 원래 관점(`"self"`) 그대로 남긴다. 합칠 때 §8 로 정규화.
- 비밀은 §5 에 따라 남지 않는다. 녹화를 공유하거나 관전 화면에 흘려도 안전해야 한다.

---

## 변경 기록
- v0 (2026-10-02): 초안. 사용자 검토 반영: 엔진 계약/내부 분리, 선택 `id`·`damage` 자리, 비밀 보호, `world.time`/`world.weather`, occupants `hints`, `"self"` 정규화 규칙.
- v0 (2026-10-02, M2 재생 반영, 호환 추가): `command.refused`, `occupant.position`, `occupant.link`, `combat.rescue`, `combat.aggro`, `items.give_failed` 추가. `skill.result.skill` null 허용과 reason 값 추가, `group.change` `follower_left`, `items.used` drop/junk/donate, 몇몇 선택 필드. 전부 6개 로그 재생의 unknown 상위 줄에서 나온 실제 서버 문구.
- v0 (2026-10-02, 확정 후 정정): occupants 그룹 표시 `(group)`/`(leader)` 는 실제로 있음 — 색으로 같은/다른 그룹 힌트. `sanctuary`, `blind` 플래그 추가 (호환 변경).
- v0 (2026-10-03, 1단계 실서버 반영, 호환 추가): `affect.changed`(독·뱀 물기), `items.cannot_drop`(저주받은 물건), `items.used.empty`(`It is empty.`) 추가.
  `You are already following $M.` / `But you are already part of a group.` 는 실패가 아니라 이미 된 상태로 `group.change` 를 낸다. 1단계 마무리 시점의 v0 이다.
