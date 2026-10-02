# 1단계 행동 명세 (mud-agents 에서 옮긴 "무엇")

출처: `../mud-agents/` 의 `common/*.tin`, `agents/*.tin`, `brain/*.tin`, `README.md`, `docs/tbamud-rules.md`.
이 문서는 **무엇을 하는가**(목표, 게임 사실, 위험, 수치 정책, 실패에서 배운 것)만 담는다 (D15).
tell 신호 체계, 상태 변수, tick 우선순위 같은 **어떻게**는 옮기지 않는다. 구현은 반사 / 행동 선택 / 작업 / 파티 계층으로 한다 (D13, D14).

메시지 문자열은 여기 적힌 것을 그대로 믿지 말고 tbaMUD 소스에서 확인한다 (CLAUDE.md 원칙).

---

## 1. 세계 사실과 함정

### 서버 동작 (tbaMUD 2025, 소스로 확인됨)
- 접속 직후 약 1.5초 클라이언트 탐지 중 입력은 버려진다 (`comm.c:1645`).
- 프롬프트 `23H 100M 55V (news) (motd) > ` 뒤에 응답이 줄바꿈 없이 붙어 온다. HP 는 음수가 될 수 있다.
- 방 표시 순서: 방 이름(노랑) → 3칸 들여쓴 설명 → `[ Exits: … ]`(청록, 닫힌 문은 괄호) → 물건(초록) → 몹/플레이어(노랑). 방 이름과 몹이 같은 색이라 순서로 구분.
- 어둠: `It is pitch black...` — 방 내용도 자기 인벤토리 이름도 안 보인다. `exits` 명령은 방향을 알려준다 (`Too dark to tell.`).
- 도주: 새 방이 먼저 출력되고 `You flee head over heels.` 가 뒤에 온다. 도주하면 그 몹이 잃은 체력 × 몹 레벨 만큼 경험치를 잃는다.
- 사망: 현재 경험치 절반을 잃고, 곧바로 로그인 메뉴(`Make your choice:`)가 나온다. 시체는 죽은 방에 남는다.
- wimpy 는 `toggle wimpy N` (`wimpy N` 은 Huh!?!). 최대 체력의 1/3 을 넘을 수 없다.
- autoassist·autoloot·autogold·autosplit 은 토글이다 (보낼 때마다 켜짐/꺼짐이 바뀜). 응답 문구로 현재 상태를 확인해야 한다.
- autoassist 는 같은 방, 서 있는, 같은 그룹원이 싸우면 돕는다.
- follow / group join 은 같은 방에서만 된다.
- 잠든 캐릭터는 방에 누가 들어와도 못 보고 tell 도 못 듣는다. 잠든 채 행동하면 `In your dreams, or what?`.
- 앉거나 쉬는 중에는 사고팔기·주기 등이 `Nah... You feel too relaxed to do that..` 로 거부된다.
- 서버는 ASCII 가 아닌 입력 바이트를 버린다 (한글 입력 불가).
- 경험치: 그룹 킬은 몹 경험치 1/3 을 **같은 방에 있는** 그룹원 수로 나눈다. 때릴 때마다 `몹 레벨 × 피해` 만큼 바로 받는다 → 약한 파티원도 전투에 참여해 때려야 큰다.
- 회복: 배고픔이나 갈증이 0 이면 회복이 1/4. 잠 +50%, 휴식 +25%, 앉기 +12.5%. 마법사·성직자는 체력 회복 절반.
- 연습 상한: 마법사·성직자 95%, 도적 85%, 전사 80%. 마법사·성직자는 한두 번이면 상한, 전사·도적은 여러 번.
- backstab: 이미 싸우는 상대에게는 불가. 찌르는 무기만. 레벨에 따라 x2~x5.
- 레벨 1 주문은 대개 30 마나 이상 든다.
- 레벨별 기술 표는 `../mud-agents/docs/tbamud-rules.md`.

### 위험
- **Peacekeeper, cityguard** 는 몹 편을 들어 난입한다. 그들이 있는 방에서 싸움을 걸지 않는다.
- **green gelatinous blob** 은 자기를 공격한 사람을 기억했다가 덮친다. assist 로 뛰어들었다가 여러 번 죽었다.
- 레벨 1~2 는 blob, mercenary 에게 두세 라운드에 죽는다.
- 위험 몹(현재 정책): `blob gelatinous dragon golem guardian` — 들어오면 피하고, 싸우게 되면 빗나간 공격에도 바로 도망.
- 데스트랩(ROOM_DEATH)과 출구 없는 방(`Mid-Air`, `The Free Fall From The Chain`)은 들어가면 끝이다.
- 위험 출구 (지도에 있어도 쓰지 않음):
  - `The Road Crossing` 의 u (쇠사슬 → Abyss 로 떨어진 적 있음)
  - `The Dump` 의 d (빛 없는 하수도 미로)
  - 길드 안쪽 방 `The Tournament And Practice Yard`, `The Secret Yard`, `The Clerics' Inner Sanctum` 의 d (하수도)
  - 함정 출구 2511 의 n
- 보트가 필요한 섹터(물), 비행, 수중 섹터로 가는 출구는 쓰지 않는다.
- `This zone is above your recommended level.` 은 그 지역 방에 들어설 때마다 뜬다. 뜨면 왔던 길을 한 걸음씩 되짚어 나온다.
  (지도에 회피 표시만 하면 돌아갈 길이 막혀 더 깊이 들어간다.)
- 잠긴 문(`It seems to be locked.`)은 일시적일 수 있다(밤에 닫히는 성문). 10분 뒤 다시 시도 가능.
- 길드 문지기(`… humiliates you, and blocks your way.`)는 다른 직업을 막는다.

### 지도와 지식 원천
- 방 그래프: `tbamud/lib/world/wld` (12,730 방). `mud-agents/mkmap.py` 의 규칙(데스트랩·출구 없는 방 회피, 섹터 7/8/9 출구 제거, 함정 출구, 어두운 방 표시, 시내 밖 동명 상점 구분)을 재현한다. `maps/world.map` 은 그 결과물.
- 지역 레벨표: `mud-agents/knowledge/zones.json`/`zones.txt` (권장 레벨, 입구까지 걸음 수, 어둠 비율, 몹 레벨/선공).
- 몹 키워드: `mud-agents/knowledge/mobs.json`. 장비 비교표: `mud-agents/maps/items.tin` (`mkitems.py` 가 `lib/world/obj` 로 생성).
- 상점: The Armory(방어구), The Weapon Shop(무기), The General Store(광원·보물·용기), The Magic Shop(두루마리·물약), The Bakery(음식), Ye Olde Water Shoppe(물통).
  반지·망토 같은 착용품을 사는 가게는 미드가드에 없다.
- 분수대: The Temple Square (그 외 `fountain` 이 보이는 방).

---

## 2. 개인 행동 목표

### 생존 (가장 우선)
- 체력이 정책값(`flee_pct`) 아래로 떨어지면 도망. 서버 wimpy 도 같은 기준으로 맞춰 둔다 (단 최대 체력 1/3 상한).
- 위험 몹이 있는 방에는 머물지 않는다. 위험 몹과 싸우게 되면 즉시 도망.
- 자다가 맞으면 즉시 깨서 일어난다.
- 사망하면 다시 접속해 게임에 들어가고, 시체가 있는 방을 기억했다가 (10분 안, 길을 알 때) 파티와 함께 가서 되찾는다.
  데스트랩에서 죽었으면 그 방을 회피로 표시하고 시체는 포기한다.

### 먹고 마시기
- 배고프거나 목마르면 가진 음식·물통을 쓴다. 쉬거나 자는 중이면 먼저 일어난다.
- 물통이 비면 분수대에서 채우거나, 보급 담당 성직자가 채워 준다.
- 없으면 보급 담당(성직자)에게서 받는다. 그래도 안 되면 파티가 분수대나 빵집으로 간다.
- 물통이 없고 금화가 있으면 Water Shoppe 에서 canteen 을 산다.

### 휴식
- 체력이 `rest_pct` 미만, 마나가 `rest_mp_pct` 미만, 이동력이 `rest_mv` 미만이면 쉰다.
- 체력 90% 이상, 마나 기준 이상, 이동력 절반 이상이면 일어난다.
- 방에 몹이 없고, 밝고, 위험 몹이 없고, 보초가 아니면 잔다 (회복이 더 크다). 아니면 앉아 쉰다.
- 습격 경보 직후 20초는 쉬지 않는다.
- 따라가는 파티원은 휴식 기준이 높으면 파티를 자주 멈추게 하므로 상한을 둔다 (기존: 체력 35%, 마나 60%).

### 사냥
- 리더만 사냥 대상을 고른다. 방 안 몹 중 `targets` 목록 순서로 처음 맞는 것을 공격.
- `shun` 목록이나 guildmaster 가 있는 방에서는 싸움을 걸지 않는다.
- 따라가는 파티원은 스스로 사냥하지 않고 autoassist/assist 로 합류한다.
- 파티원이 맞고 있는데 싸우지 않는 파티원은 돕는다. 단 때리는 몹이 사냥 대상이고 `shun` 이 아닐 때만 (blob 교훈).

### 이동과 탐험
- 목표 방이 있으면 최단 경로(회피 방, 불 없을 때 어두운 방 제외)로 간다.
- 목표가 없고 `roam` 이면 덜 가본 출구를 우선해 배회한다. 거점 지역(rally 가 속한 zone) 밖으로 나가는 것은 강하게 피하고, 왔던 길로 되돌아가는 것은 약하게 피한다.
- 어두운 방에 불 없이 들어섰으면 왔던 방향으로 돌아 나온다. 불이 있어도 갈 길을 모르면 `exits` 로 방향을 확인한다.
- 닫힌 문은 열고 지나간다. 막힌 출구(없는 출구, 잠김, 보트, 길드 문지기)는 그 출구 가치를 크게 낮춘다.
- 위치는 이동 방향과 도착한 방 이름·출구로 확정한다. 같은 이름 방이 여럿이면 이전 위치 이웃 → 같은 이름+같은 출구 → 가장 가까운 vnum 순.

### 장비와 물건
- 접속하면 autoloot·autogold·autosplit 이 켜져 있게 한다 (막타 친 사람이 줍고 금화는 나눈다). 시체를 직접 뒤지지 않는다 (파티원 시체를 털던 버그).
- 바닥 물건: 금화는 누구나 줍는다. 찌르는 무기(dagger/knife/stiletto)는 도적이 같은 방에 있으면 도적 몫. 장비는 입은 게 12개 미만인 사람이 줍는다. 한 번 못 주운 키워드는 다시 시도하지 않는다.
- 장비 교체: 주기적으로(기존 5분) 인벤토리/장비를 보고, 같은 부위에 입은 것보다 비교표 점수가 5점 이상 좋은 물건이 있으면 바꿔 입는다. 직업 제한 물건 제외, 도적은 찌르는 무기만. 영구 광원(glowing jar) 최우선. 반지·목걸이·팔찌는 두 칸.
- 광원: 불이 꺼지면 버리고 여분을 든다. 불이 없고 금화 50 이상이면 General Store 에서 lantern(50골드)을, 여유가 있으면(150 이상) torch 도 산다.
- 여분 정리: 입은 것보다 좋은 장비와 필수품(음식, 물통, 광원, 열쇠)은 남긴다. 여분이 쌓이면(기존 3개 초과) 시내 원정 때 판다. 10개를 넘으면 그만큼 버린다.
- 분배: 파티 전원의 여분 장비를 가장 크게 좋아지는 사람에게 준다 (`mud-agents/distribute.py`: 이득 = 비교표 점수 − 그 부위에 입은 것 중 가장 약한 것, 좋은 물건부터, 배정할 때마다 받는 사람 장비 갱신).
  받는 사람이 같은 방에 있고 둘 다 전투 밖·깨어 있을 때 준다.
- 파티원 시체에서 autoloot 로 나온 물건은 주인에게 돌려준다.

### 연습
- 연습 횟수가 남으면 길드에 가서 연습하고 돌아온다. 다음 레벨에 핵심 기술이 열리면 연습 2회를 남긴다.
- 기술 계획 순서대로, 목표 숙련도에 못 미치고 상한에 닿지 않은 첫 기술을 연습한다. 마법사는 가장 새 공격 주문만 채운다 (A 표시).
- 거점에 있거나 거점 지역 밖일 때 출발. 어두운 방, 15분 초과, 길드 문지기, 길 막힘이면 포기하고 50분 뒤 다시 시도.
- 연습 숙련도 단계: not learned < awful < bad < poor < average < fair < good < very good < superb.

| 직업 | 길드 방 | Temple Square 에서 경로 | 지나는 방 | 기술 계획 (목표 단계) | 연습 남기기 |
|---|---|---|---|---|---|
| 전사 | The Tournament And Practice Yard | s e e s e s | Market Square, Main Street, Main Street, The Entrance Hall To The Guild Of Swordsmen, The Bar Of Swordsmen | kick 6, rescue 6, bash 6, bandage 1 | rescue@3, bash@12 |
| 마법사 | The Mages' Laboratory | s w w s s e | Market Square, Main Street, Main Street, The Entrance To The Mages' Guild, The Mages' Bar | fireball/color_spray/lightning_bolt/shocking_grasp/burning_hands/chill_touch/magic_missile 8 (A), armor 3 | chill_touch@3, burning_hands@5 |
| 도적 | The Secret Yard | s s e s e s | Market Square, The Common Square, The Dark Alley, The Entrance Hall To The Guild Of Thieves, The Thieves' Bar | backstab 7, hide 1 | backstab@3 |
| 성직자 | The Clerics' Inner Sanctum | w n w | The Entrance To The Clerics' Guild, The Bar Of Divination | cure_light 8, create_food 3, create_water 3, armor 3, cure_critic 8, bless 3, group_armor 3, word_of_recall 3, heal 8, sanctuary 8, remove_poison 1 | cure_critic@9, word_of_recall@12, sanctuary@15 |

(목표 단계 숫자: 1 awful, 3 poor, 4 average, 6 good, 7 very good, 8 superb. Temple Of Midgaard 에서는 s 로 Temple Square.)

---

## 3. 직업별 목표

- **전사**: 싸우는 동안 bash(배웠으면) 또는 kick. 약한 직업 파티원이 맞으면 바로 rescue. 다른 전사는 체력 40% 미만이고 내가 20%p 이상 높을 때 rescue.
- **마법사**: 마나가 충분하면(기존 25 이상) 배운 공격 주문 중 가장 센 것 (fireball > color_spray > lightning_bolt > shocking_grasp > burning_hands > chill_touch > magic_missile). 마나 부족 응답 후 30초는 주문을 쓰지 않는다.
- **도적**: 리더가 지정하면 backstab 으로 전투를 연다 (여는 한 방 전용). backstab 을 모르거나 무기가 안 맞으면 리더가 직접 공격. 몹이 도적을 치면 전사가 rescue.
- **성직자** (두 명이 역할을 나눔. 한쪽이 없으면 남은 쪽이 다 함):
  - 주 치료: 전투 중 55%, 평시 80% 미만인 파티원을 치료. 대상은 체력 비율이 낮고 최대 체력이 작은(약한) 사람 우선.
  - 보조(보급): 전투 중 35%, 평시 60% 미만일 때만 치료. 대신 음식·물·armor 담당.
  - 주문 선택: 마나 60 이상이면 heal, 30 이상이면 cure_critic, 아니면 cure_light (배운 것 중).
  - 보급: 배고픈 파티원에게 음식을 만들어(또는 비축분에서) 준다. 목마른 파티원의 빈 물통을 받아 채워 돌려준다. 자기 물통도 채운다.
  - 비축: 마나 여유(70% 이상)가 있으면 음식을 10개까지 만들어 둔다. 8개 이상이면 오래 못 받은 파티원에게 나눠 준다.
  - 버프: 마나 60% 이상이면 15분마다 같은 방 파티원과 자신에게 armor.

---

## 4. 파티 목표

- **구성**: 리더 1명이 이동과 사냥을 정하고, 나머지는 리더를 따라가고 같은 그룹에 든다. 그룹·따라가기는 같은 방에서만 된다.
- **사냥 거점(rally)**: 파티가 사냥하는 지역의 기준 방. 흩어지면 여기로 모이고, 리더는 여기서 `leash` 걸음 이상 벗어나지 않는다 (넘으면 되짚어 돌아옴).
- **사냥터 순환**: 거점 후보 지역 2곳 이상을 돈다. 한 지역에서 5분 이상 머물렀고 2분 넘게 킬이 없으면(또는 25분 넘게 머물면) 다음 지역으로 옮겨 리젠을 기다린다.
  옮기는 조건: 전원이 같은 방, 리더 체력 70% 이상, 쉬는 중·시내 원정 중이 아님. 10분 안에 도착 못 하면 이전 거점으로 돌아가고 30분간 순환을 멈춘다.
  옮긴 직후 10~15분의 흩어짐은 정상이다.
- **낙오와 재집결**: 리더와 떨어진 파티원은 (도주 직후 2분은 떠난 방으로 돌아가 보고) 거점으로 간다. 리더는 낙오자가 생기면 1분 제자리에서 기다리고, 그래도 없으면 거점으로 가서 4분까지 기다린다.
  잠깐 리더 방에서 벗어난 것과 진짜 낙오를 구분해야 한다.
- **휴식 동기화**: 누가 자기 필요로 쉬기 시작하면 리더는 이동을 멈추고 기다린다 (최대 5분). 쉬는 파티원은 따라가지 못하므로, 리더가 떠나면 일어나 같은 방향으로 따라간다.
  리더가 쉬면 같은 방 파티원도 쉰다. 리더가 일어나 떠날 때 잠든 파티원을 깨운다.
- **보초**: 파티가 쉴 때 같은 방에서 체력이 가장 좋은 한 명은 자지 않는다. 파티원이 아닌 누군가 들어오면 모두를 깨운다.
- **전투 참여**: 리더가 싸우면 모두 autoassist 로 합류. 도적이 있고 리더가 rescue 를 배웠으면 도적이 backstab 으로 열고(최대 5초 기다림) 리더가 이어서 공격.
- **연습 다녀오기**: 파티원이 연습하러 가면 리더는 거점에서 기다린다 (최대 10분).
- **시내 원정** (리더가 파티를 데리고): 리더 체력 60% 이상·이동력 40% 이상일 때만.
  - 판매: 누군가 여분이 쌓였거나 불이 필요하면 (20분에 한 번) The Armory → The Weapon Shop → The General Store 를 돌며 각자 판다.
  - 물통: 물통 없는 파티원이 있으면 (25분에 한 번) Water Shoppe.
  - 음식: 리더가 배고프고 금화 10 이상이면 (15분에 한 번) The Bakery 에서 인원수만큼 사서 나눠 주고 먹는다.
  - 도주했거나 길이 없는 가게는 30분간 건너뛴다. 원정 전체 10분 제한.
- **갈증**: 누군가 목마르고 물이 없으면 파티가 분수대로 간다.
- **시체 회수**: 죽은 파티원이 합류하면 리더가 파티를 이끌고 시체 방으로 간다 (10분 안, 길을 알 때). 본인이 `get all 자기이름`.
- **명단**: 파티 명단 밖의 캐릭터가 하는 말이나 행동에는 반응하지 않는다.

---

## 5. 정책값 (현재 `brain/*.tin`, 정적으로 가져옴)

| | Vallen | Lil | Senia | Lumina | Elysia | Carmilla |
|---|---|---|---|---|---|---|
| 직업 | 전사 | 마법사 | 전사 | 성직자 | 성직자 | 도적 |
| 파티 역할 | 리더 | | | 주 치료 | 보급 | 기습 |
| flee_pct | 45 | 45 | 45 | 45 | 45 | 40 |
| rest_pct | 30 | 30 | 30 | 30 | 30 | 30 |
| rest_mp_pct | 0 | 35 | 0 | 45 | 45 | 20 |
| rest_mv | 25 | 15 | 15 | 15 | 15 | 15 |
| roam | 1 | 0 | 0 | 0 | 0 | 0 |

- **targets** (우선순위 순, 전원 같음): newbie quasit crawler monster keeper pet thing fido janitor odif beggar centipede kobold orc hobgoblin snake lion warrior wolf warg apprentice student broom adventurer woman cook cockroach butler skeleton zombie
- **shun**: cityguard Peacekeeper guildmaster Mayor blob gelatinous mercenary knight templar sorcerer dragon golem guardian (Carmilla 는 + watched)
- **danger**: blob gelatinous dragon golem guardian
- **avoid** 공통 (방 이름 부분 일치): The Magic Shop, The Great Chessboard Of Midgaard, A White Square, A Black Square, On The Huge Chain, The Chain Where It Is Too Windy, On The Great Chain Of Naris, The Free Fall From The Chain, The Hill Giant Cave, The Maze, The Cave Of The Green Dragon, The Den Of The Queen Spider, The Entrance To The High Tower, Inside The High Tower Of Sorcery.
  캐릭터별 추가분은 `brain/NAME.tin` 참고 (대부분 실패 경험에서 붙은 것). 단 `Inside The East Gate Of Midgaard`, `Levee` 는 통로라 회피하면 안 된다.
- **파티**: rally `The Entrance To The Newbie Zone`, leash 20, 순환 지역 `The Entrance To The Newbie Zone`(zone 186) ↔ `The End Of The Path`(zone 40).
- **사용자 지시** (`brain/orders.md`): 파티 레벨에 맞는 지역에서 사냥, 미드가드 시내(zone 30)는 사냥터로 쓰지 않음, 초보자 지역 안쪽은 회피에 넣지 않음.
- **키워드**: 음식 bread waybread meat cheese apple fish pie cake muffin sausage steak jerky ration biscuit mushroom berries pastry cookie carrot potato melon banana orange grapes.
  물통 waterskin skin canteen flask bottle cup jug cask mug goblet pitcher barrel.

---

## 6. 실패에서 배운 것 (같은 실수를 반복하지 않기)

- 추측 수정이 반복 실패의 주원인이었다. 동작이 불확실하면 tbaMUD 소스를 먼저 읽는다.
- 도주 기준(flee 65%)이 너무 높으면 파티가 계속 흩어진다 (도주 = 파티 이탈).
- assist 를 무조건 하면 blob 같은 기억형 몹에게 죽는다.
- 고레벨 지역 경고 때 회피 표시만 하면 되돌아갈 길이 막혀 더 깊이 들어간다. 되짚어 나와야 한다.
- 잠든 파티원은 신호를 못 받는다. 잠든 상태에서 행동하면 잠과 깨기를 반복하는 고리가 생긴다.
- 리더가 `score` 를 볼 때마다 그룹을 새로 만들던 버그가 있었다 (그룹 결성은 한 번).
- 성직자가 둘인데 물통 주고받기가 한쪽(주 치료)에게만 묶여 있어 보급 담당의 요청이 무시됐다. 역할은 상태에 따라 정해져야 한다.
- 여러 규칙이 같은 상태(리더 대기 여부)를 덮어써서 리더가 기다려야 할 때 떠났다. 한 결정은 한 곳에서.
- 장비 교체 때 같은 키워드 물건이 여럿이면 순번(`2.dagger`)이 필요하다. 벗은 물건은 인벤토리 맨 앞으로 들어간다.

---

## 7. 옮기지 않는 것 (tintin 판의 "어떻게")

- tell 약속어(rest ready lost train food water sendcon sell canteen corpse canopen noopen open miss sentry circuit alert)와 그 처리. → 파티 블랙보드와 역할 배정 (D14).
- hold, told, train_state, circ_* 같은 상태 변수 묶음. → 작업(시퀀서)과 블랙보드.
- 3초 tick 안의 우선순위 if 사다리. → 행동 선택 (D13).
- 한 줄 한 action 우회, 프롬프트 정규식 분리, `#map find`/`#path` 트릭, 지도 플래그 재사용, 뇌 파일 재로드 후 값 다시 덮기. → 어댑터·Memoria 구조로 대체.

## 8. 1단계 구현에서 달라진 것 (2026-10-03)
위 명세는 tintin 판에서 옮긴 "무엇"이다. 실서버에서 돌려 보고 아래처럼 바꿨거나 아직 하지 않았다. 이유는 `DECISIONS.md`.

| 명세 | 1단계 구현 | 근거 |
|---|---|---|
| 파티 6인 (§5 표) | 그대로. 리더 Vallen, 보급 Elysia, 문 여는 역할 Carmilla | D8 |
| 보급 담당이 빈 물통을 받아 채워 돌려준다 (§3) | 물통을 가진 성직자 **누구나** 채워서 준다. 비성직자는 물통을 목마른 성직자에게 넘기고, 빈 물통은 보급 담당에게 | D22 |
| 휴식: `rest_mv` 절대값 미만 (§2) | 그에 더해 이동력 40%(`rest_mv_pct`) 미만이면 시작, 체력 90%·이동력 60% 까지. 다 회복하면 일어남 | D24 |
| 사냥터 순환: 5분 머물고 2분 무킬(또는 25분), 10분 안에 못 가면 이전 거점·30분 정지 (§4) | 5분 무킬이면 다음 거점, 옮긴 뒤 10분은 안 옮김, 길이 없으면 1분 뒤 되돌림, 거점은 재시작 후에도 유지 | D20 |
| 이동 조건: 전원 같은 방, 리더 체력 70% (§4) | 전원 같은 방(추정 ∧ 실제로 보임) ∧ 모두 따라가기·그룹 ∧ 파티 최저 이동력 30~50% 이상 | D21, D23 |
| 여분 장비: 3개 초과면 시내에서 팔고, 10개 초과면 버림 (§2) | 12개 초과면 필수품 외 `junk`. 저주받은 물건은 기억하고 건너뜀. 팔지 않음 | D25 |
| 장비 교체, 시내 원정(판매·canteen 구매·광원), 연습 원정 중 리더 대기 (§2, §4) | 장비 교체·상점은 **하지 않음** → 2단계. 연습 다녀오기(`train_at_guild`)는 있음 | PHASE-1-CLOSE §6 |
| 낙오 시 리더 1분/4분 대기, 휴식 동기화 최대 5분 (§4) | 고정 대기 시간 없음. 리더는 모두 같은 방·따라붙음·쉬지 않음일 때만 움직이고(행동 점수가 0), 낙오자는 리더 위치로 걸어간다(`go_to_leader`) | D18, D23 |
