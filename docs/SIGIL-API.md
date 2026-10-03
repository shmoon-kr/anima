# Sigil API v0

`anima/sigil/api.py` 에서 생성됨 (`anima sigil api-doc`). 손으로 고치지 말 것.
검증기가 같은 목록을 쓴다: 여기 없는 이름·함수를 쓴 Sigil 은 거부된다.

## 읽을 수 있는 상태

| 이름 | 형 | 뜻 |
|---|---|---|
| `self.name` | text | 캐릭터 이름 |
| `self.hp` | number | 현재 체력 (프롬프트) |
| `self.hp_max` | number | 최대 체력 (score 또는 본 최댓값) |
| `self.hp_pct` | number | 체력 % (0~100) |
| `self.mp` | number | 현재 마나 |
| `self.mp_max` | number | 최대 마나 |
| `self.mp_pct` | number | 마나 % |
| `self.mv` | number | 현재 이동력 |
| `self.mv_max` | number | 최대 이동력 |
| `self.mv_pct` | number | 이동력 % |
| `self.level` | number | 레벨 |
| `self.gold` | number | 금화 |
| `self.practices` | number | 남은 연습 횟수 |
| `self.position` | text | standing | sitting | resting | sleeping |
| `self.fighting` | bool | 최근 몇 초 안에 내가 때리거나 맞았다 |
| `self.hungry` | bool | 배고픔 |
| `self.thirsty` | bool | 목마름 |
| `self.has_light` | bool | 광원을 들고 있다 |
| `self.in_game` | bool | 게임 안에 있다 (로그인 완료) |
| `self.secs_since_fight` | number | 마지막 전투 이후 초 |
| `self.secs_since_kill` | number | 마지막으로 상대를 죽인 뒤 초 |
| `self.secs_since_fled` | number | 마지막 도주 이후 초 |
| `self.chase_dir` | string | 싸우던 몹이 도망간 방향 (본 지 20초 안, 아니면 null): chase() 로 한 방 쫓는다 |
| `self.secs_since_alert` | number | 마지막 경보(낯선 존재 도착·깨워짐) 이후 초 |
| `self.secs_in_behavior` | number | 지금 행동을 시작한 뒤 초 |
| `self.behavior` | text | 지금 고른 행동 이름 |
| `self.task` | text | 실행 중인 작업 이름 (없으면 null) |
| `self.inventory` | list | 가진 물건 짧은 설명 목록 |
| `self.equipment` | list | 입은 물건 목록 |
| `room.name` | text | 현재 방 이름 |
| `room.vnum` | number | 현재 방 번호 (Memoria 추정, 모르면 null) |
| `room.zone` | number | 현재 방의 지역 번호 |
| `room.dark` | bool | 어두워 보이지 않는다 |
| `room.occupants` | list | 방 안 존재 줄 목록 (화면 그대로) |
| `room.objects` | list | 바닥 물건 줄 목록 |
| `room.strangers` | list | 방 안 존재 중 파티원이 아닌 것 |
| `room.shop` | bool | 상점 주인이 있는 방 (세계 데이터) |
| `room.unidentified` | list | 낯선 존재 중 세계 파일로 알아볼 수 없는 것 (Animus 에게 물을 거리) |
| `room.exits` | list | 출구 방향 목록 |
| `room.has_fountain` | bool | 분수대가 있다 |
| `world.night` | bool | 밤 (실외가 어둡다) |
| `party.leader` | text | 리더 이름 |
| `party.is_leader` | bool | 내가 리더 |
| `party.size` | number | 명단 인원 |
| `party.here` | number | 나와 같은 방에 있는 파티원 수 (나 포함) |
| `party.all_here` | bool | 명단 전원이 같은 방 |
| `party.lost_secs` | number | 가장 오래 떨어져 있는 파티원의 이탈 시간 (없으면 0) |
| `party.resting` | bool | 같은 방 파티원 중 누가 쉬는 중 |
| `party.camping` | bool | 내 방의 파티가 야영 중 (한 명의 휴식 필요가 camp_start 에 닿으면 시작, 아무도 필요 없으면 끝, D30) |
| `party.sentry` | text | 야영 중 보초 이름 (시작할 때 체력이 가장 많은 파티원, 야영 동안 고정). 야영이 아니면 null |
| `party.is_sentry` | bool | 내가 보초 |
| `party.trip_stop` | number | 장보기 중 리더가 다음에 갈 상점 방 번호 (go_to 에 쓴다). 장보기가 아니면 null (D32) |
| `party.trip_wanted` | bool | 누군가 팔 것이 쌓였거나 살 것이 있고, 지난 장보기 뒤 trip_cooldown_s 가 지났다 |
| `party.shop_busy` | bool | 같은 방 파티원이 방금(6초 안) 사거나 팔았다 |
| `party.rally` | text | 집결지 방 이름 |
| `party.members_in_room` | list | 같은 방 파티원 (targets 용) |
| `party.role` | text | 역할 배정에서 받은 내 역할 이름들 (쉼표) |
| `party.leader_room` | text | 리더가 있는 방 이름 (모르면 null) |
| `party.leader_vnum` | number | 리더가 있는 방 번호 (Memoria 추정, 모르면 null). go_to 에 그대로 쓸 수 있다 |
| `party.with_leader` | bool | 리더와 같은 방 |
| `party.leader_reachable` | bool | 리더에게 걸어갈 수 있음 (나만 막는 출구, 예: 다른 직업의 길드 경비가 길에 있으면 false) |
| `party.wait_vnum` | number | 리더에게 못 갈 때 기다릴 방: 나를 막는 출구 바로 앞 (모르면 null) |
| `party.following` | bool | 리더를 따라가는 중 (서버 follow) |
| `party.in_group` | bool | 서버 그룹에 들어 있음 |
| `party.online` | number | 게임 안에 있는 명단 인원 |
| `party.all_following` | bool | 리더 외 게임 안 파티원이 모두 리더를 따라가고 그룹에 들어 있다 |
| `party.unseen_here` | number | 위치 추정으로는 같은 방인데 방 목록에 안 보이는 파티원 수 |
| `party.min_mv_pct` | number | 게임 안 파티원 중 가장 낮은 이동력 % (나 포함) |
| `party.thirsty_in_room` | list | 같은 방에서 목마른 파티원 이름 |
| `party.hungry_in_room` | list | 같은 방에서 배고픈 파티원 이름 |

## 동적 이름

| 이름 | 뜻 |
|---|---|
| `event.*` | 반사의 `on` 이벤트 내용. `event.type`, `event.<data 필드>` (PROTOCOL.md 의 data) |
| `policy.*` | 병합된 패키지들이 선언한 정책값 (`policies:`). 선언하지 않은 이름은 오류 |
| `answer.*` | Animus 질문(`asks:`)의 현재 답. 답이 오기 전에는 그 질문의 `default` |
| `target.*` | `targets:` 가 있는 행동에서 지금 점수를 매기는 대상 (`target.*` 필드는 아래 표) |

### `target.*`

| 이름 | 형 | 뜻 |
|---|---|---|
| `target.name` | text | 이름 |
| `target.hp_pct` | number | 체력 % |
| `target.hp_max` | number | 최대 체력 |
| `target.is_self` | bool | 나 자신 |
| `target.thirsty` | bool | 목마름 |
| `target.hungry` | bool | 배고픔 |
| `target.has_drink` | bool | 물통을 가지고 있다 |
| `target.has_food` | bool | 음식을 가지고 있다 |
| `target.class` | text | 직업 (파티 명단 정보) |
| `target.being_hit` | bool | 최근 몇 초 안에 누가 이 대상을 때리는 것을 봤다 |

## 함수 (조건·점수식)

| 함수 | 뜻 |
|---|---|
| `linear(x, lo, hi)` | x 가 lo 이하면 0, hi 이상이면 1, 사이는 비례 |
| `inverse_linear(x, lo, hi)` | x 가 lo 이하면 1, hi 이상이면 0 |
| `step(x, t)` | x >= t 이면 1, 아니면 0 |
| `below(x, t)` | x < t 이면 1, 아니면 0 |
| `window(x, lo, hi)` | lo <= x < hi 이면 1 |
| `bool(x)` | 참이면 1 |
| `min(a, b)` | 작은 값 |
| `max(a, b)` | 큰 값 |
| `abs(x)` | 절댓값 |
| `clamp(x, lo, hi)` | 범위로 자르기 |
| `len(x)` | 목록·문자열 길이 |
| `contains(haystack, needle)` | 목록에 있거나 문자열에 (대소문자 무시) 들어 있다 |
| `any_contains(items, words)` | items 중 하나에 words 중 하나가 (대소문자 무시) 들어 있다 |
| `knows(skill)` | 그 기술·주문을 배웠다 |
| `proficiency(skill)` | 숙련 단계 0(not learned)~8(superb) |
| `first_known(skills)` | 목록에서 처음으로 배운 기술 이름 (없으면 null) |
| `pick_target(targets, shun)` | 방 안 존재 중 targets 순서로 처음 맞는 단어 (shun 이 방에 있으면 null) |
| `path_len(room)` | 그 방(이름)까지 걸음 수, 길이 없으면 null |
| `in_zone_of(room)` | 지금 방이 그 방(이름)과 같은 지역 |
| `secs_since(key)` | mark(key) 이후 초 (한 번도 없으면 큰 값). 작업 실패는 'failed:<작업>' 으로 자동 기록 |
| `if_else(cond, a, b)` | cond 가 참이면 a, 아니면 b |
| `item_of(kind)` | 가진 그 종류 물건의 명령용 키워드 (없으면 null) |
| `item_count(kind)` | 가진 그 종류 물건 수 |
| `surplus_item(keep)` | keep 종류(food, drinkcon, light, key ...)가 아닌 가진 물건 하나의 키워드 (없으면 null) |
| `member_with_role(role)` | 같은 방에서 그 역할을 맡은 파티원 이름 (나 제외, 없으면 null) |
| `has_role(role)` | 내가 그 역할을 맡았다 |
| `has_item(kind)` | 가진 물건 중 그 종류(food, drinkcon, light, weapon ...)가 있다 (세계 데이터 기준) |
| `upgrade_item()` | 가진 물건 중 입은 것보다 나은 장비의 키워드 (policy.gear_avoid·weapon_kinds·레벨 고려, 없으면 null) |
| `list_needed(reserve)` | 살 물건의 이름이 이 상점의 다른 물건과 겹치고 이 상점 목록을 아직 못 봤다 (list 로 번호를 알아야 한다) |
| `guild_door()` | 내 길드 방(policy.guild_room) 바로 바깥 방 번호: 내가 안에서 배우는 동안 파티가 서는 곳 |
| `train_wanted()` | 지금 쓸 연습 횟수(예약분 제외)와 배울 기술이 있다 |
| `gear_gift()` | 내게는 개선이 아니고 같은 방 파티원에게는 개선인 장비: '키워드 이름' (가장 이득이 큰 사람, 그 사람의 직업·레벨·장비로 판단; 없으면 null) |
| `sellable_here(keep)` | 이 방 상점이 사 주고 내게 필요 없는 물건의 키워드 (keep 종류·더 나은 장비·저주·nosell 제외) |
| `buy_here(reserve)` | 이 방 상점에서 살 것의 키워드: 살 수 있는 장비 개선, 없으면 policy.food_min 까지 음식 (reserve 금화는 남김) |
| `pickup_item(min_cost)` | 바닥 물건 중 주울 것의 키워드: 값이 min_cost 이상, 장비 개선, 또는 음식 |
| `next_skill(plan)` | 연습 계획 [{skill, target, attack?}] 에서 다음에 연습할 기술 (없으면 null). target 은 숙련 단계 0~8, 상한에 닿은 기술은 건너뜀, attack 기술은 더 새 attack 을 배웠으면 건너뜀 |
| `practices_spare(reserve_for)` | 남은 연습 수. 다음 레벨에 열리는 기술이 reserve_for {기술: 레벨} 에 있으면 2를 남김 |

## 동작 (`do` 안에서만)

| 동작 | 뜻 |
|---|---|
| `wear_upgrade()` | 가장 나은 장비 개선을 입는다 (자리가 차 있으면 못한 것을 벗고), 그 뒤 equipment·inventory |
| `start_trip()` | 리더: 파티원들이 가고 싶은 상점들로 장보기를 시작 |
| `trip_progress()` | 리더: 지금 상점에서 모두 볼일이 끝났으면 다음 상점으로 |
| `send(text)` | 명령 그대로 보내기 (금지 명령은 검증에서 거부) |
| `attack(target)` | 공격 시작 (kill) |
| `use(skill, target)` | 기술 사용. target 생략 가능 |
| `cast(spell, target)` | 주문 시전. target 생략 가능 |
| `flee()` | 도망 |
| `rest()` | 쉬기 |
| `sleep()` | 자기 |
| `stand()` | 일어서기 (자면 깨고 일어섬) |
| `wake(who)` | 깨우기. who 생략 시 자기 |
| `wake_party()` | 같은 방에서 자는 파티원을 모두 깨운다 (내가 자면 나부터) |
| `eat()` | 가진 음식 먹기 |
| `drink()` | 가진 물통이나 방 분수대에서 마시기 |
| `practice(skill)` | 연습 |
| `go_to(room)` | 그 방(이름 또는 번호)으로 한 걸음 (작업 안에서는 도착까지) |
| `go_back()` | 이동 기록을 한 걸음 되짚기 |
| `chase()` | 도망간 몹을 그 방향으로 한 방 쫓기 (레벨 위 지역이면 가지 않음) |
| `explore()` | 덜 가본 출구로 한 걸음 |
| `ensure_toggle(name, value)` | 서버 토글(autoloot 등)을 그 값으로 맞추기 |
| `set_wimpy(hp)` | 서버 wimpy 를 맞추기 (최대 체력 1/3 상한) |
| `start_task(name)` | 작업 시작 |
| `next_rally()` | 리더만: 파티 거점을 순환 목록(policy circuit)의 다음 곳으로 옮김 |
| `mark(key)` | 지금 시각을 key 로 기록 (secs_since 용) |
| `wait(secs)` | 작업 안에서 기다리기 |

## 금지

- 명령: alias, delete, display, gossip, holler, password, prompt, quit, rent, save, shout, suicide, title
- 명령 문자열의 문자: `; $ @ #` 와 줄바꿈. 다만 명령어 뒤의 물건 번호 `#3` (`buy #3`) 은 된다
