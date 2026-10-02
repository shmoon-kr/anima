"""Single-line messages of tbaMUD 2025, written as the server's own templates.

Each entry names the source location the template was copied from. A rule whose builder
returns None marks a known line that carries no event (not counted as `unknown`).
Multi-line outputs (room, group table, inventory, equipment, practice list, score, exits list)
are handled by the block parser in parser.py.
"""
from __future__ import annotations

from typing import Any

from anima.protocol.envelope import SELF

from .act import Rule


def _name(g: dict[str, Any], key: str = "n") -> str:
    return normalize_name(g.get(key) or "")


def normalize_name(name: str) -> str:
    """act() capitalises the first letter of a message; undo it for articles of mob names."""
    for art in ("The ", "A ", "An ", "Some "):
        if name.startswith(art):
            return art.lower() + name[len(art):]
    return name


def _int(g: dict[str, Any], key: str) -> int:
    return int(g[key])


def ev(type_: str, **data: Any):
    return lambda g: (type_, dict(data))


def known(_g: dict[str, Any]) -> None:
    return None


DIRS = {"north", "east", "south", "west", "up", "down"}

R = Rule
RULES: list[Rule] = [
    # ---- 접속 (interpreter.c nanny, config.c) ----
    R("Attempting to Detect Client, Please Wait...", known),                 # comm.c:1647
    R("Collecting Protocol Information... Please Wait.", known),             # interpreter.c:1401
    R("Reconnecting.", ev("connection.in_game", how="reconnected")),        # interpreter.c:1191
    R("You take over your own body, already in use!", ev("connection.in_game", how="took_over")),  # :1198
    R("Welcome to tbaMUD!  May your visit here be... Enlightening", ev("connection.in_game", how="entered")),  # config.c:287
    R("Welcome to tbaMUD!", known),                                           # config.c:274 menu
    R("0) Exit from tbaMUD.", known), R("1) Enter the game.", known), R("2) Enter description.", known),
    R("3) Read the background story.", known), R("4) Change password.", known), R("5) Delete this character.", known),
    R("Wrong password... disconnecting.", ev("connection.login_failed", reason="wrong_password")),  # :1535

    # ---- 상태 ----
    R("You rise a level!", ev("level.up", levels=1)),                                     # limits.c:253
    R("You rise %d levels!", lambda g: ("level.up", {"levels": _int(g, "d0")})),           # limits.c:255
    R("You receive your share of experience -- %d points.",
      lambda g: ("exp.gain", {"amount": _int(g, "d0"), "kind": "share"})),                # fight.c:345
    R("You receive your share of experience -- one measly little point!",
      ev("exp.gain", amount=1, kind="share")),                                            # fight.c:347
    R("You receive %d experience points.", lambda g: ("exp.gain", {"amount": _int(g, "d0"), "kind": "solo"})),  # fight.c:399
    R("You are hungry.", ev("condition", hungry=True)),                                   # limits.c:324
    R("You are thirsty.", ev("condition", thirsty=True)),                                 # limits.c:327
    R("You are full.", ev("condition", hungry=False, full=True)),                         # act.item.c:973
    R("You are too full to eat more!", ev("condition", full=True)),                       # act.item.c:1028
    R("Your stomach can't contain anymore!", ev("condition", full=True)),                 # act.item.c:877
    R("You don't feel thirsty any more.", ev("condition", thirsty=False, quenched=True)), # act.item.c:884
    R("You do not feel thirsty.", ev("condition", thirsty=False, quenched=True)),

    # 자세 (act.movement.c)
    R("You stand up.", ev("position", position="standing")),                  # :738
    R("You stop resting, and stand up.", ev("position", position="standing")),  # :746
    R("You are already standing.", ev("position", position="standing")),     # :735
    R("You sit down.", ev("position", position="sitting")),                   # :804
    R("You sit down and rest your tired bones.", ev("position", position="resting")),  # :862
    R("You are already resting.", ev("position", position="resting")),        # :872
    R("You go to sleep.", ev("position", position="sleeping")),               # :894
    R("You are already sound asleep.", ev("position", position="sleeping")),  # :899
    R("You awaken, and sit up.", ev("position", position="sitting")),         # :946
    R("You are already awake...", known),                                      # :944
    R("You are awakened by $n.", lambda g: ("position", {"position": "sitting", "awakened_by": _name(g)})),  # :935
    R("You have to wake up first!", ev("command.refused", reason="sleeping")),  # :753
    R("You have to wake up first.", ev("command.refused", reason="sleeping")),  # :845

    # 명령 거부 (interpreter.c:568-587, config.c:98)
    R("Lie still; you are DEAD!!! :-(", ev("command.refused", reason="dead")),
    R("In your dreams, or what?", ev("command.refused", reason="sleeping")),
    R("Nah... You feel too relaxed to do that..", ev("command.refused", reason="resting")),
    R("Maybe you should get on your feet first?", ev("command.refused", reason="sitting")),
    R("No way!  You're fighting for your life!", ev("command.refused", reason="fighting")),
    R("Huh!?!", ev("command.refused", reason="unknown_command")),

    # ---- 세계 (weather.c) ----
    R("The sun rises in the east.", ev("world.time", phase="sunrise")),           # :50
    R("The day has begun.", ev("world.time", phase="day")),                       # :54
    R("The sun slowly disappears in the west.", ev("world.time", phase="sunset")),  # :58
    R("The night has begun.", ev("world.time", phase="night")),                   # :62
    R("The sky starts to get cloudy.", ev("world.weather", change="cloudy")),      # :162
    R("It starts to rain.", ev("world.weather", change="rain")),                   # :166
    R("The clouds disappear.", ev("world.weather", change="clear")),               # :170
    R("Lightning starts to show in the sky.", ev("world.weather", change="lightning")),  # :174
    R("The rain stops.", ev("world.weather", change="rain_stops")),                # :178
    R("The lightning stops.", ev("world.weather", change="lightning_stops")),      # :182

    # ---- 방과 이동 (act.movement.c, act.informative.c) ----
    R("It is pitch black...", ev("room.dark")),                                  # act.informative.c:500
    R("You see nothing but infinite darkness...", ev("room.dark", blind=True)),  # :504
    R("Alas, you cannot go that way...", ev("move.failed", reason="no_exit")),   # act.movement.c:348
    R("The %s seems to be closed.", lambda g: ("move.failed", {"reason": "closed", "door": g["s0"]})),  # :353
    R("It seems to be closed.", ev("move.failed", reason="closed")),             # :355
    R("It seems to be locked.", ev("move.failed", reason="locked")),             # :670
    R("You need a boat to go there.", ev("move.failed", reason="need_boat")),    # :182
    R("You aren't godly enough to use that room!", ev("move.failed", reason="forbidden")),  # :245
    R("The guard humiliates you, and blocks your way.", ev("move.failed", reason="guarded")),  # spec_procs.c:448
    R("You are too exhausted.", ev("move.failed", reason="exhausted")),          # :261
    R("You are too exhausted to follow.", ev("move.failed", reason="exhausted")),  # :259
    R("This zone is above your recommended level.", ev("zone.above_level")),     # :218
    R("$n has arrived.", lambda g: ("occupant.arrived", {"who": _name(g)})),     # :303
    R("$n leaves %s.", lambda g: ("occupant.left", {"who": _name(g), "dir": g["s0"]})
      if g["s0"] in DIRS else None),                                             # :279
    R("You follow $N.", lambda g: ("follow.moved", {"leader": _name(g, "N")})),  # :368
    R("Obvious exits:", known),                                                   # act.informative.c do_exits (block)

    # ---- 전투 (fight.c, act.offensive.c) ----
    R("$n is dead!  R.I.P.", lambda g: ("combat.death", {"who": _name(g)})),     # fight.c:729
    R("You are dead!  Sorry...", ev("self.died")),                               # fight.c:730
    R("Your blood freezes as you hear $n's death cry.",
      lambda g: ("combat.death_cry", {"who": _name(g), "nearby": False})),       # fight.c:271
    R("Your blood freezes as you hear someone's death cry.", ev("combat.death_cry", nearby=True)),  # fight.c:275
    R("$n is stunned, but will probably regain consciousness again.",
      lambda g: ("combat.condition", {"who": _name(g), "state": "stunned"})),    # fight.c:725
    R("You're stunned, but will probably regain consciousness again.",
      ev("combat.condition", who=SELF, state="stunned")),                       # fight.c:726
    R("$n is incapacitated and will slowly die, if not aided.",
      lambda g: ("combat.condition", {"who": _name(g), "state": "incapacitated"})),  # fight.c:721
    R("You are incapacitated and will slowly die, if not aided.",
      ev("combat.condition", who=SELF, state="incapacitated")),
    R("$n is mortally wounded, and will die soon, if not aided.",
      lambda g: ("combat.condition", {"who": _name(g), "state": "mortally_wounded"})),  # fight.c:717
    R("You are mortally wounded, and will die soon, if not aided.",
      ev("combat.condition", who=SELF, state="mortally_wounded")),
    R("That really did HURT!", known),                                           # fight.c:735
    R("You wish that your wounds would stop BLEEDING so much!", known),          # fight.c:738
    R("You wimp out, and attempt to flee!", ev("combat.wimpy")),                 # fight.c:745
    R("You flee head over heels.", ev("self.fled")),                             # act.offensive.c:251
    R("PANIC!  You couldn't escape!", ev("self.flee_failed", reason="panic")),   # :267
    R("You are in pretty bad shape, unable to flee!", ev("self.flee_failed", reason="bad_shape")),  # :240
    R("$n panics, and attempts to flee!", lambda g: ("combat.flee_seen", {"who": _name(g)})),  # :248
    R("$n tries to flee, but can't!", known),                                    # :262
    R("You join the fight!", ev("combat.assist", who=SELF)),                     # act.offensive.c:60
    R("Backstab who?", ev("skill.result", skill="backstab", ok=False, reason="no_target")),  # :141
    R("You need to wield a weapon to make it a success.", ev("skill.result", skill=None, ok=False, reason="need_weapon")),
    R("Only piercing weapons can be used for backstabbing.",
      ev("skill.result", skill="backstab", ok=False, reason="wrong_weapon")),   # :153
    R("You can't backstab a fighting person -- they're too alert!",
      ev("skill.result", skill="backstab", ok=False, reason="too_alert")),      # :157
    R("You have no idea how.", ev("skill.result", skill=None, ok=False, reason="unknown_skill")),
    R("You have no idea how to do that.", ev("skill.result", skill=None, ok=False, reason="unknown_skill")),
    R("You are unfamiliar with that spell.", ev("skill.result", skill=None, ok=False, reason="unknown_skill")),  # spell_parser.c:547
    R("You haven't the energy to cast that spell!", ev("skill.result", skill=None, ok=False, reason="no_mana")),  # :627
    R("You lost your concentration!", ev("skill.result", skill=None, ok=False, reason="lost_concentration")),  # :635
    R("$n stares at you and utters the words, '%s'.", known),
    R("$n stares at $N and utters the words, '%s'.", known),
    R("$n closes $s eyes and utters the words, '%s'.", known),
    R("You feel someone protecting you.", known),                                # magic.c:338
    R("You feel better.", known),                                                # magic.c:812
    R("You feel very sick.", ev("affect.changed", who=SELF, affect="poison", on=True)),          # magic.c:455
    R("$n gets violently ill!", lambda g: ("affect.changed", {"who": _name(g), "affect": "poison", "on": True})),  # :456
    R("You feel less sick.", ev("affect.changed", who=SELF, affect="poison", on=False)),        # spell_parser.c:867 (wear-off)
    R("$n bites you!", lambda g: ("combat.hit", {"attacker": _name(g), "victim": SELF, "verb": "bite",
                                                 "severity": 4, "kind": "skill"})),             # spec_procs.c:352 snake
    R("$n bites $N!", lambda g: ("combat.hit", {"attacker": _name(g), "victim": _name(g, "N"), "verb": "bite",
                                                "severity": 4, "kind": "skill"})),             # spec_procs.c:351

    # ---- 의사소통 (act.comm.c) ----
    R("$n tells you, '%s'", lambda g: ("comm.tell", {"from": _name(g), "to": SELF, "text": g["s0"], "direction": "in"})),  # :102
    R("You tell $N, '%s'", lambda g: ("comm.tell", {"from": SELF, "to": _name(g, "N"), "text": g["s0"], "direction": "out"})),
    R("You group-say, '%s'", lambda g: ("comm.gtell", {"from": SELF, "text": g["s0"], "direction": "out"})),  # :94
    R("$n says, '%s'", lambda g: ("comm.say", {"from": _name(g), "text": g["s0"], "direction": "in"})),  # :53
    R("You say, '%s'", lambda g: ("comm.say", {"from": SELF, "text": g["s0"], "direction": "out"})),  # :63

    # ---- 그룹 (utils.c, handler.c, act.other.c) ----
    R("You now follow $N.", lambda g: ("group.change", {"event": "following", "who": _name(g, "N")})),  # utils.c:677
    R("$n starts following you.", lambda g: ("group.change", {"event": "followed_by", "who": _name(g)})),  # :679
    R("You stop following $N.", lambda g: ("group.change", {"event": "stopped_following", "who": _name(g, "N")})),  # :597
    R("$n stops following you.", lambda g: ("group.change", {"event": "follower_left", "who": _name(g)})),  # :600
    R("%s joins the group.", lambda g: ("group.change", {"event": "joined", "who": g["s0"]})),       # handler.c:1689
    R("%s has left the group.", lambda g: ("group.change", {"event": "left", "who": g["s0"]})),      # :1650
    R("%s has assumed leadership of the group.", lambda g: ("group.change", {"event": "new_leader", "who": g["s0"]})),  # :1669
    R("%s becomes leader of the group.", lambda g: ("group.change", {"event": "new_leader", "who": g["s0"]})),  # :1687
    R("%s reports: %d/%dH, %d/%dM, %d/%dV", known),                               # act.other.c:502

    # ---- 물건 (act.item.c, handler.c) ----
    R("You get $p.", lambda g: ("items.got", {"text": _name(g, "p")})),           # act.item.c:282
    R("You get $p from $P.", lambda g: ("items.got", {"text": _name(g, "p"), "from": _name(g, "P")})),  # :220
    R("There was 1 coin.", known), R("There were %d coins.", known),             # :206-208
    R("$n gives you $p.", lambda g: ("items.received", {"text": _name(g, "p"), "from": _name(g)})),  # :649
    R("$n gives you %d gold coins.", lambda g: ("items.received", {"text": f"{g['d0']} gold coins", "from": _name(g)})),  # :688
    R("$n gives you %d gold coin.", lambda g: ("items.received", {"text": "1 gold coin", "from": _name(g)})),
    R("You give $p to $N.", lambda g: ("items.gave", {"text": _name(g, "p"), "to": _name(g, "N")})),
    R("Your light sputters out and dies.", ev("items.light_out", who=SELF)),       # handler.c:1008
    R("$n's light sputters out and dies.", lambda g: ("items.light_out", {"who": _name(g)})),  # :1009
    R("You don't see %s %s here.", lambda g: ("items.not_found", {"keyword": g["s1"]})),  # :304
    R("You don't see any %ss here.", lambda g: ("items.not_found", {"keyword": g["s0"]})),  # :330
    R("You don't seem to have %s %s.", lambda g: ("items.not_found", {"keyword": g["s1"]})),
    R("You don't seem to have any %ss.", lambda g: ("items.not_found", {"keyword": g["s0"]})),
    R("You can't find it!", ev("items.not_found")),
    R("You can't take %s %s.", lambda g: ("items.cannot_take", {"text": g["s1"]})),  # :301
    R("$p: you can't take that!", lambda g: ("items.cannot_take", {"text": _name(g, "p")})),  # :172
    R("You eat $p.", lambda g: ("items.used", {"action": "eat", "text": _name(g, "p")})),  # :1036
    R("You drink the %s.", lambda g: ("items.used", {"action": "drink", "text": g["s0"]})),  # :936
    R("It is empty.", ev("items.used", action="drink", empty=True)),               # :923
    R("You wield $p.", lambda g: ("items.used", {"action": "wield", "text": _name(g, "p")})),  # :1270
    R("You grab $p.", lambda g: ("items.used", {"action": "hold", "text": _name(g, "p")})),    # :1273
    R("You light $p and hold it.", lambda g: ("items.used", {"action": "hold", "text": _name(g, "p")})),  # :1222
    R("You stop using $p.", lambda g: ("items.used", {"action": "remove", "text": _name(g, "p")})),  # :1518

    # ---- 연습 (spec_procs.c) ----
    R("You practice for a while...", ev("char.practiced", result="improved")),       # :176
    R("You are now learned in that area.", ev("char.practiced", result="maxed")),    # :185
    R("You are already learned in that area.", ev("char.practiced", result="maxed")),  # :173
    R("You do not seem to be able to practice now.", ev("char.practiced", result="cannot", reason="no_practices")),  # :161
    R("You do not know of that %s.", ev("char.practiced", result="cannot", reason="unknown_skill")),        # :169

    # ---- 토글 (act.other.c:725-760) ----
    *[R(f"{name} {state}.", ev("toggle.state", name=name.lower(), value=(state == "enabled")))
      for name in ("Autoloot", "Autogold", "Autosplit", "Autoassist", "Autoexits", "Autosacrifice",
                   "Automap", "Autokey", "Autodoor")
      for state in ("enabled", "disabled")],
]

# wear messages to self (act.item.c:1222-1273 wear_messages[][1])
for _tmpl in ("You slide $p on to your right ring finger.", "You slide $p on to your left ring finger.",
              "You wear $p around your neck.", "You wear $p on your body.", "You wear $p on your head.",
              "You put $p on your legs.", "You wear $p on your feet.", "You put $p on your hands.",
              "You wear $p on your arms.", "You start to use $p as a shield.", "You wear $p around your body.",
              "You wear $p around your waist.", "You put $p on around your right wrist.",
              "You put $p on around your left wrist."):
    RULES.append(R(_tmpl, lambda g: ("items.used", {"action": "wear", "text": _name(g, "p")})))

# ---- 2차: 재생 리포트 상위 unknown 에서 추가 (모두 소스 확인) ----
def _pos(position: str):
    return lambda g: ("occupant.position", {"who": _name(g), "position": position})


RULES += [
    # 다른 존재의 자세 (act.movement.c)
    R("$n clambers to $s feet.", _pos("standing")),                      # :739
    R("$n stops resting, and clambers on $s feet.", _pos("standing")),   # :747
    R("$n stops resting.", _pos("sitting")),                             # :841
    R("$n sits down.", _pos("sitting")),                                 # :805
    R("$n sits down and rests.", _pos("resting")),                       # :863
    R("$n lies down and falls asleep.", _pos("sleeping")),               # :895
    R("$n awakens.", _pos("sitting")),                                   # :947
    R("You wake $M up.", known),                                         # :934
    R("$E is already awake.", known),                                    # :928
    # score 의 자세 줄 (act.informative.c:967-984)
    R("You are sleeping.", ev("position", position="sleeping")),
    R("You are resting.", ev("position", position="resting")),
    R("You are sitting.", ev("position", position="sitting")),
    R("You are standing.", ev("position", position="standing")),
    # 돕기·구조 (act.offensive.c)
    R("$N assists you!", lambda g: ("combat.assist", {"who": _name(g, "N"), "target": SELF})),  # :61
    R("$n assists $N.", lambda g: ("combat.assist", {"who": _name(g), "target": _name(g, "N")})),  # :62
    R("But nobody is fighting $M!", ev("command.refused", reason="no_target")),  # :53
    R("$n heroically rescues $N!", lambda g: ("combat.rescue", {"who": _name(g), "rescued": _name(g, "N")})),  # :381
    R("Kick who?", ev("skill.result", skill="kick", ok=False, reason="no_target")),   # :510
    R("Bash who?", ev("skill.result", skill="bash", ok=False, reason="no_target")),   # :294
    R("That player is not here.", ev("command.refused", reason="no_target")),        # act.offensive.c:78
    R("No one by that name here.", ev("command.refused", reason="no_target")),       # config.c:99 NOPERSON
    R("$E's linkless at the moment.", ev("command.refused", reason="linkless")),     # act.comm.c:131
    R("Sorry, but you cannot do that here!", ev("command.refused", reason="not_here")),  # act.other.c:98
    R("'Hey!  You're the fiend that attacked me!!!', exclaims $n.",
      lambda g: ("combat.aggro", {"who": _name(g), "reason": "remembered"})),       # mobact.c:151
    # 그룹·금화 (act.other.c)
    R("You are already in a group.", known),                                         # :405
    R("You split %d coins among %d members -- %d coins each.", known),               # :562
    R("%s splits %d coins; you receive %d.", known),                                 # :548
    R("%d coins were not splitable, so %s keeps the money.", known),
    R("Okay, you'll wimp out if you drop below %d hit points.",
      lambda g: ("toggle.state", {"name": "wimpy", "value": _int(g, "d0")})),       # act.informative.c:2383
    R("Okay.", known),
    # 물건 (act.item.c, shop.c, magic.c)
    R("You're already wielding a weapon.", known),                                   # :1313
    R("You can't wear anything else around your neck.", known),                      # :1301
    R("You're already wearing something %s.", known),                                # :1299-1312
    R("$p: you can't carry that many items.", lambda g: ("items.cannot_take", {"text": _name(g, "p"), "reason": "too_many"})),  # :178
    R("$p: you can't carry that many items!", lambda g: ("items.cannot_take", {"text": _name(g, "p"), "reason": "too_many"})),  # :1512
    R("There is no room for more.", known),                                          # :1167
    R("You have been rewarded by the gods!", known),                                 # :620
    R("$n has been rewarded by the gods!", known),
    R("You now have %s.", lambda g: ("shop.result", {"action": "buy", "ok": True, "text": g["s0"]})),  # shop.c:637
    R("$n creates $p.", known),                                                      # magic.c:967
    R("$n drinks %s from $p.", known),                                               # act.item.c:933
    R("$n utters the words, '%s'.", known),                                          # spell_parser.c:112
    R("$n stares at $p and utters the words, '%s'.", known),                         # :110
    R("Currently, there is nothing for sale.", ev("shop.list", items=[])),
    R("Presently, none of those are for sale.", ev("shop.list", items=[])),
]

# 다른 존재의 물건 행동 (act.item.c, shop.c) — 사건은 맞지만 1단계 위 계층에 필요 없음: known
for _tmpl in ("$n gets $p.", "$n gets $p from $P.", "$n gives $p to $N.", "$n gives %s to $N.", "$n eats $p.",
              "$n buys %s.", "$n gently fills $p from $P.",
              "$n lights $p and holds it.", "$n slides $p on to $s right ring finger.",
              "$n slides $p on to $s left ring finger.", "$n wears $p around $s neck.", "$n wears $p on $s body.",
              "$n wears $p on $s head.", "$n puts $p on $s legs.", "$n wears $p on $s feet.",
              "$n puts $p on $s hands.", "$n wears $p on $s arms.", "$n straps $p around $s arm as a shield.",
              "$n wears $p about $s body.", "$n wears $p around $s waist.", "$n puts $p on around $s right wrist.",
              "$n puts $p on around $s left wrist.", "$n wields $p.", "$n grabs $p."):
    RULES.append(R(_tmpl, known))
RULES.append(R("You gently fill $p from $P.", lambda g: ("items.used", {"action": "fill", "text": _name(g, "p")})))  # :1174

# ---- 3차 (모두 소스 확인) ----
RULES += [
    R("You %s $p.", lambda g: ("items.used", {"action": g["s0"], "text": _name(g, "p")})
      if g["s0"] in ("drop", "junk", "donate") else None),                          # act.item.c:480
    R("You %s $p.  It vanishes in a puff of smoke!", lambda g: ("items.used", {"action": g["s0"], "text": _name(g, "p")})
      if g["s0"] in ("drop", "junk", "donate") else None),
    R("You can't %s $p, it must be CURSED!", lambda g: ("items.cannot_drop", {"text": _name(g, "p"), "reason": "cursed"})),  # act.item.c:475
    R("But it's currently open!", known),
    R("$n %ss $p.", known), R("$n %ss $p.  It vanishes in a puff of smoke!", known),
    R("$n sells %s.", known),                                                      # shop.c:802
    R("The shopkeeper now has %s.", lambda g: ("shop.result", {"action": "sell", "ok": True, "text": g["s0"]})),  # shop.c:808
    R("%s: You can't carry any more items.", lambda g: ("shop.result", {"action": "buy", "ok": False, "reason": "too_many"})),  # :536
    R("%s: You can't carry that much weight.", lambda g: ("shop.result", {"action": "buy", "ok": False, "reason": "too_heavy"})),  # :540
    R("$p: you can't carry that much weight.", lambda g: ("items.cannot_take", {"text": _name(g, "p"), "reason": "too_heavy"})),  # act.item.c:181
    R("$p seems to be empty.", known),                                             # act.item.c:266 (빈 시체)
    R("$N seems to have $S hands full.", lambda g: ("items.give_failed", {"to": _name(g, "N"), "reason": "hands_full"})),  # :639
    R("$E can't carry that much weight.", ev("items.give_failed", reason="too_heavy")),  # :643
    R("$n stops using $p.", known),                                                # :1519
    R("You can't wield that.", known),                                             # :1464
    R("Your magic fizzles out and dies.", ev("skill.result", skill=None, ok=False, reason="fizzled")),  # spell_parser.c:196
    R("$n's magic fizzles out and dies.", known),                                  # :197
    R("$n has reconnected.", lambda g: ("occupant.link", {"who": _name(g), "state": "reconnected"})),  # interpreter.c:1192
    R("$n has lost $s link.", lambda g: ("occupant.link", {"who": _name(g), "state": "lost"})),        # comm.c:2207
    R("$n has entered the game.", lambda g: ("occupant.arrived", {"who": _name(g), "how": "entered_game"})),  # interpreter.c:1720
    R("Whom do you want to rescue?", ev("skill.result", skill="rescue", ok=False, reason="no_target")),  # act.offensive.c:346
    R("You fail the rescue!", ev("skill.result", skill="rescue", ok=False, reason="failed")),           # :376
    R("Banzai!  To the rescue...", ev("skill.result", skill="rescue", ok=True)),                         # :379
    R("You are rescued by $N, you are confused!", lambda g: ("combat.rescue", {"who": _name(g, "N"), "rescued": SELF})),  # :380
    R("You're already fighting!  How can you assist someone else?", ev("command.refused", reason="fighting")),  # :30
    R("You're fighting the best you can!", ev("command.refused", reason="fighting")),                    # :96
    R("Do you not consider fighting as standing?", ev("position", position="standing")),                 # act.movement.c:756
    R("That's not a direction.", ev("move.failed", reason="other")),                                     # :390
    R("Player killing is not permitted.", ev("command.refused", reason="not_allowed")),                  # fight.c:157
    R("$n jumps to the aid of $N!", lambda g: ("combat.assist", {"who": _name(g), "target": _name(g, "N")})),  # mobact.c:188
    R("%d coins were not splitable, so you keep the money.", known),                                     # act.other.c:566
    R("%d coin was not splitable, so you keep the money.", known),
    R("%d coin was not splitable, so %s keeps the money.", known),
    R("You drop some gold.", known),
]

# ---- 4차 (소스 확인) ----
RULES += [
    R("Upon %s should the spell be cast?", ev("skill.result", skill=None, ok=False, reason="no_target")),  # spell_parser.c:610
    R("But you are already part of a group.", ev("group.change", event="joined", who=SELF)),   # act.other.c:419
    R("You are already following $M.", ev("group.change", event="following", who=None)),       # act.movement.c:975
    R("Join who?", ev("command.refused", reason="no_target")),                     # act.other.c:413
    R("Nobody around by that name.", ev("command.refused", reason="no_target")),   # act.informative.c:1747
    R("You are poisoned!", known),                                                 # act.informative.c:1013 (score)
    R("Rest while fighting?  Are you MAD?", ev("command.refused", reason="fighting")),  # act.movement.c:878
    R("You are in a pretty bad shape, unable to do anything!", ev("command.refused", reason="incapacitated")),  # interpreter.c:572
    R("All you can do right now is think about the stars!", ev("command.refused", reason="stunned")),           # interpreter.c:575
    R("You can't drink from that!", ev("command.refused", reason="invalid_target")),  # act.item.c:905
    R("You can't eat THAT!", ev("command.refused", reason="invalid_target")),         # act.item.c:1024
    R("You're already using a light.", known),                                        # act.item.c:1297
    R("There doesn't seem to be %s %s here.", lambda g: ("items.not_found", {"keyword": g["s1"]})),  # act.item.c:1111 etc
    R("There doesn't seem to be anything here.", ev("items.not_found")),              # act.item.c:328
    R("$n has transferred you!", known),                                              # act.wizard.c
]
RULES += [
    R("Cannot find the target of your spell!", ev("skill.result", skill=None, ok=False, reason="no_target")),  # spell_parser.c:622
    R("There is already another liquid in it!", known),                            # act.item.c:1162
    R("You're already using a shield.", known),                                    # act.item.c:1308
    R("What do you want to %s?", known),                                           # act.item.c
    R("That's not a menu choice!", known),                                         # interpreter.c:1772
]
