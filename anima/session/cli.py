"""`anima` command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="anima")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("replay", help="mud-agents 로그를 어댑터에 흘려 이벤트로 바꾼다")
    r.add_argument("log", type=Path)
    r.add_argument("--agent", help="캐릭터 이름 (기본: 파일 이름)")
    r.add_argument("--out", type=Path, help="JSONL 로 저장할 경로")
    r.add_argument("--stats", action="store_true", help="이벤트 통계와 unknown 상위 목록")
    r.add_argument("--no-raw", action="store_true", help="raw 원문을 넣지 않음")
    r.add_argument("--top", type=int, default=40)

    s = sub.add_parser("stats", help="녹화(.jsonl) 또는 tintin 로그들에서 킬·경험치·사망·도주·이탈 통계")
    s.add_argument("paths", nargs="+", type=Path, help="Anima 녹화 하나, 또는 캐릭터별 tintin 로그 여러 개")
    s.add_argument("--from", dest="since", type=float, help="녹화 시작 후 이 분부터")
    s.add_argument("--minutes", type=float, help="이 길이(분)만")
    s.add_argument("--by-window", type=float, metavar="MIN", help="이 분 단위로 킬·경험치·대기 표")

    sg = sub.add_parser("sigil", help="Sigil 패키지 검사·설명")
    sgs = sg.add_subparsers(dest="sigil_cmd", required=True)
    ex = sgs.add_parser("explain", help="에이전트의 병합 결과와 각 항목이 온 레이어")
    ex.add_argument("agent")
    ck = sgs.add_parser("check", help="모든 에이전트 manifest 를 검증")
    sgs.add_parser("api-doc", help="docs/SIGIL-API.md 를 레지스트리에서 다시 생성")
    for x in (ex, ck):
        x.add_argument("--agents", type=Path, default=Path("agents"))
        x.add_argument("--packages", type=Path, default=Path("packages"))

    for name, hlp in (("run", "에이전트들을 이 터미널에서 실행 (Ctrl-C 로 종료)"),
                      ("start", "에이전트들을 백그라운드로 실행")):
        x = sub.add_parser(name, help=hlp)
        x.add_argument("agents", nargs="*", help="이름 (기본: agents/*.yaml 전부, 리더 먼저)")
    sub.add_parser("stop", help="실행 중인 에이전트를 종료 (게임에서 quit)")
    sub.add_parser("status", help="에이전트별 접속·체력·위치·행동")
    sub.add_parser("reload", help="실행 중인 에이전트의 Sigil 을 다시 읽기 (검증 실패면 이전 것 유지)")
    an = sub.add_parser("animus", help="Animus 덮어쓰기 레이어: 보기·이력·되돌리기·끄기")
    ans = an.add_subparsers(dest="animus_cmd", required=True)
    ans.add_parser("show", help="지금 덮어쓴 값과 잠긴 키")
    h = ans.add_parser("history", help="적용·거부·되돌림 이력")
    h.add_argument("-n", type=int, default=20)
    rv = ans.add_parser("revert", help="패치 하나 또는 전부 되돌리기")
    rv.add_argument("patch_id", nargs="?")
    rv.add_argument("--all", action="store_true")
    ans.add_parser("off", help="덮어쓰기를 끄고 1단계 동작으로 (값은 보존)")
    ans.add_parser("on", help="덮어쓰기를 다시 켬")
    st = ans.add_parser("set", help="사람이 값 하나를 직접 덮어쓰기 (시험용)")
    st.add_argument("layer", help="party 또는 에이전트 이름")
    st.add_argument("key", help="policy.NAME 또는 weight.BEHAVIOR")
    st.add_argument("value", help="JSON (숫자, 목록) 또는 문자열")
    st.add_argument("--ttl", type=float)
    st.add_argument("--reason", default="set by hand")
    kd = ans.add_parser("knobs-doc", help="docs/ANIMUS-KNOBS.md 를 패키지에서 다시 생성")
    kd.add_argument("--agents", type=Path, default=Path("agents"))
    kd.add_argument("--packages", type=Path, default=Path("packages"))

    w = sub.add_parser("watch", help="한 에이전트의 이벤트를 실시간으로 보고, 입력한 줄을 사람 명령으로 보냄")
    w.add_argument("agent")
    w.add_argument("--raw", action="store_true", help="JSON 그대로")

    args = p.parse_args(argv)
    if args.cmd == "replay":
        return _replay(args)
    if args.cmd == "stats":
        return _stats(args)
    if args.cmd == "sigil":
        return _sigil(args)
    if args.cmd in ("run", "start"):
        return _run(args)
    if args.cmd == "stop":
        return _control({"op": "stop"})
    if args.cmd == "status":
        return _status()
    if args.cmd == "reload":
        return _control({"op": "reload"})
    if args.cmd == "watch":
        return _watch(args)
    if args.cmd == "animus":
        return _animus(args)
    return 1


RUN_DIR = Path("run")


def _agent_names(given: list[str]) -> list[str]:
    if given:
        return given
    import yaml
    names = sorted(p.stem for p in Path("agents").glob("*.yaml"))
    lead = [n for n in names if "role-leader" in str(yaml.safe_load((Path("agents") / f"{n}.yaml").read_text()))]
    return lead + [n for n in names if n not in lead]


def _run(args: argparse.Namespace) -> int:
    import asyncio
    import signal
    import subprocess

    names = _agent_names(args.agents)
    if args.cmd == "start":
        Path("logs").mkdir(exist_ok=True)
        out = open("logs/anima.out", "a")
        proc = subprocess.Popen([sys.executable, "-m", "anima.session.cli", "run", *names], stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
        print(f"started pid {proc.pid}: {' '.join(names)}  (log: logs/anima.out)")
        return 0
    from anima.session.supervisor import Config, Supervisor
    sup = Supervisor(Config.load(Path(".")), names)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, sup.stop)
        await sup.run()
    asyncio.run(main())
    return 0


def _control(req: dict) -> int:
    import json
    import socket
    sock_path = RUN_DIR / "anima.sock"
    if not sock_path.exists():
        print("not running")
        return 1
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(sock_path))
        s.sendall((json.dumps(req) + "\n").encode())
        data = s.makefile().readline()
    print(data.strip())
    return 0


def _animus(args: argparse.Namespace) -> int:
    import json
    c = args.animus_cmd
    if c == "knobs-doc":
        from anima.animus.knobs_doc import write_doc
        print(write_doc(args.agents, args.packages, Path("docs/ANIMUS-KNOBS.md")), "written")
        return 0
    req: dict = {"op": "animus", "sub": c}
    if c == "history":
        req["n"] = args.n
    elif c == "revert":
        if not args.patch_id and not args.all:
            print("give a patch id or --all")
            return 1
        req.update(patch_id=args.patch_id, all=args.all)
    elif c == "set":
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            value = args.value
        req.update(layer=args.layer, key=args.key, value=value, ttl_s=args.ttl, reason=args.reason)
    return _control(req)


def _status() -> int:
    import json
    import socket
    sock_path = RUN_DIR / "anima.sock"
    if not sock_path.exists():
        print("not running")
        return 1
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(sock_path))
        s.sendall(b'{"op": "status"}\n')
        st = json.loads(s.makefile().readline())
    for name, a in st.items():
        conn = "in game" if a["in_game"] else "connected" if a["connected"] else "offline"
        scores = " ".join(f"{x['id'].split('/')[-1]}:{x['score']}" for x in a["scores"])
        print(f"{name:9s} {conn:9s} {a['hp']}/{a['hp_max']}H {a['mp']}M {a['mv']}V {a['position']:8s} "
              f"[{a['vnum']}] {a['room']}  · {a['behavior']}{' / ' + a['task'] if a['task'] else ''}  ({scores})"
              + (f"  ERROR {a['error']}" if a["error"] else ""))
    return 0


def describe(ev: dict) -> str | None:
    """One readable line per event for `anima watch`."""
    t, d = ev["type"], ev.get("data", {})
    if t == "prompt":
        return None
    if t == "command.sent":
        src = d.get("source", {})
        return f"  >> {d.get('text')}    [{src.get('kind')} {src.get('id')}: {src.get('reason', '')}]"
    if t == "runtime.behavior":
        sc = ", ".join(f"{x['id'].split('/')[-1]} {x['score']}" for x in d.get("scores", []))
        return f"  ** {d.get('from')} → {d.get('to')}   ({sc})"
    if t == "runtime.task":
        return f"  ** task {d.get('name')} {d.get('event')} step {d.get('step')} {d.get('reason', '')}"
    if t == "room":
        return f"[{d.get('name')}] exits {' '.join(e['dir'][0] for e in d.get('exits', []))}" + "".join(
            f"\n    · {o['text']}" for o in d.get("occupants", []))
    if t == "unknown":
        return f"  ? {d.get('text')}"
    return f"  {t} {d}"


def _watch(args: argparse.Namespace) -> int:
    import json
    import socket
    import threading
    sock_path = RUN_DIR / "anima.sock"
    if not sock_path.exists():
        print("not running")
        return 1
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_path))
    s.sendall((json.dumps({"op": "watch", "agent": args.agent}) + "\n").encode())

    def typing() -> None:
        for line in sys.stdin:
            s.sendall(line.encode())
    threading.Thread(target=typing, daemon=True).start()
    try:
        for line in s.makefile():
            if args.raw:
                print(line.rstrip())
                continue
            out = describe(json.loads(line))
            if out:
                print(out)
    except KeyboardInterrupt:
        pass
    return 0


def _sigil(args: argparse.Namespace) -> int:
    from anima.sigil import api
    from anima.sigil.program import SigilError, load_agent

    if args.sigil_cmd == "api-doc":
        Path("docs/SIGIL-API.md").write_text(api.generate_markdown(), encoding="utf-8")
        print("docs/SIGIL-API.md written")
        return 0
    names = [args.agent] if args.sigil_cmd == "explain" else sorted(p.stem for p in args.agents.glob("*.yaml"))
    bad = 0
    for name in names:
        try:
            prog = load_agent(args.agents / f"{name}.yaml", args.packages)
        except SigilError as e:
            bad += 1
            print(f"{name}: REJECTED")
            for err in e.errors:
                print(f"  {err}")
            continue
        print(prog.explain() if args.sigil_cmd == "explain" else f"{name}: ok ({' → '.join(prog.layers)})")
    return 1 if bad else 0


def _stats(args: argparse.Namespace) -> int:
    from anima.stats import compute, game_clock, merge, render

    if all(p.suffix == ".jsonl" for p in args.paths):
        from anima.recorder import read_recording
        events = [ev for p in args.paths for ev in read_recording(p)]
        if events and (args.since or args.minutes):
            t0 = min(ev.t for ev in events) + 60 * (args.since or 0)
            t1 = t0 + 60 * args.minutes if args.minutes else float("inf")
            events = [ev for ev in events if t0 <= ev.t < t1]
        print(render(compute(events, window_min=args.by_window or 10.0), args.by_window))
        return 0
    from anima.adapters.tbamud_text.replay import replay_file
    parts = [compute(game_clock(list(replay_file(p, keep_raw=False))), shared_clock=False)
             for p in args.paths]
    print(render(merge(parts)))
    return 0


def _replay(args: argparse.Namespace) -> int:
    from anima.adapters.tbamud_text.replay import replay_file, stats

    events = replay_file(args.log, args.agent, keep_raw=not args.no_raw)
    if args.out:
        def tee():
            with args.out.open("w", encoding="utf-8") as f:
                for ev in events:
                    f.write(ev.to_json() + "\n")
                    yield ev
        events = tee()
    if args.stats:
        st = stats(events, args.top)
        print(f"events {st['total']}  unknown ratio (excluding prompts) {st['unknown_ratio']:.2%}")
        for t, c in st["types"].most_common():
            print(f"  {c:8d}  {t}")
        print("\nunknown top:")
        for text, c in st["unknown"]:
            print(f"  {c:7d}  {text}")
    elif not args.out:
        for ev in events:
            sys.stdout.write(ev.to_json() + "\n")
    else:
        for _ in events:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
