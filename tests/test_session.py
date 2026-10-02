"""Session against a fake tbaMUD: telnet, login dialogue, secret masking, death re-entry."""
import asyncio

from anima.bus import Bus
from anima.protocol.envelope import Stamper
from anima.recorder import Recorder, read_recording
from anima.session.session import Session
from anima.session.telnet import DO, IAC, TelnetFilter, WILL, WONT, DONT

ROOM = ("\x1b[0;33mThe Temple Square\x1b[0m\r\n   You are standing on the temple square.\r\n"
        "\x1b[0;36m[ Exits: n e s w ]\x1b[0m\r\n")
PROMPT = "30H 100M 80V > "


class FakeMud:
    """Speaks just enough of tbaMUD's nanny() dialogue (interpreter.c) to log in."""

    def __init__(self, known=("Vallen",), password="hunter2", die_after_look=False):
        self.known, self.password, self.die_after_look = known, password, die_after_look
        self.received: list[bytes] = []
        self.negotiation_replies = b""

    async def handle(self, reader, writer):
        def out(s):
            writer.write(s.encode() if isinstance(s, str) else s)
        out(bytes([IAC, DO, 24, IAC, WILL, 69]))                  # TTYPE, MSDP
        out("Attempting to Detect Client, Please Wait...\r\n")
        await writer.drain()
        self.negotiation_replies = await reader.readexactly(6)
        out("\r\n   T B A M U D\r\nBy what name do you wish to be known? ")
        await writer.drain()
        name = (await reader.readline()).strip().decode()
        self.received.append(name.encode())
        if name not in self.known:
            out(f"Did I get that right, {name} (Y/N)? ")
            await writer.drain()
            self.received.append((await reader.readline()).strip())
            writer.close()
            return
        out("Password: ")
        await writer.drain()
        pw = (await reader.readline()).strip()
        self.received.append(pw)
        out("\r\n      Welcome to\r\n   T B A M U D\r\n\r\n*** PRESS RETURN: ")
        await writer.drain()
        self.received.append((await reader.readline()).strip())
        for _ in range(2):
            out("Welcome to tbaMUD!\r\n0) Exit from tbaMUD.\r\n1) Enter the game.\r\n\r\n   Make your choice: ")
            await writer.drain()
            self.received.append((await reader.readline()).strip())
            out("Welcome to tbaMUD!  May your visit here be... Enlightening\r\n\r\n" + ROOM + PROMPT)
            await writer.drain()
            if not self.die_after_look:
                break
            await asyncio.sleep(0.05)
            out("\r\n\x1b[0;31mThe blob crushes you right out of existence!\r\n\x1b[0mYou are dead!  Sorry...\r\n\r\n")
            self.die_after_look = False
        while line := await reader.readline():
            self.received.append(line.strip())
            if line.strip() == b"quit":
                break
        writer.close()


async def start(mud):
    server = await asyncio.start_server(mud.handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_telnet_filter_refuses_options_and_marks_prompts():
    tf = TelnetFilter()
    text, reply, mark = tf.feed(bytes([IAC, DO, 24]) + b"hi" + bytes([IAC, WILL, 69, IAC, 250, 1, 2, IAC, 240, IAC, 249]))
    assert text == b"hi" and reply == bytes([IAC, WONT, 24, IAC, DONT, 69]) and mark


async def run_session(mud, tmp_path, name="Vallen", seconds=1.5):
    server, port = await start(mud)
    bus = Bus()
    rec = Recorder(tmp_path / "r.jsonl")
    rec.attach(bus)
    sess = Session(name, "127.0.0.1", port, "hunter2", bus, Stamper(name), interval=0.0)
    task = asyncio.create_task(sess.run())
    await asyncio.sleep(seconds)
    await sess.stop()
    task.cancel()
    server.close()
    rec.close()
    return sess, list(read_recording(tmp_path / "r.jsonl"))


async def test_login_enters_game_and_password_is_never_recorded(tmp_path):
    mud = FakeMud()
    sess, evs = await run_session(mud, tmp_path)
    assert mud.negotiation_replies == bytes([IAC, WONT, 24, IAC, DONT, 69])
    assert mud.received[:4] == [b"Vallen", b"hunter2", b"", b"1"]
    types = [e.type for e in evs]
    assert "connection.in_game" in types and "room" in types and "prompt" in types
    assert "hunter2" not in (tmp_path / "r.jsonl").read_text()
    sent = [e.data for e in evs if e.type == "command.sent"]
    pw = next(d for d in sent if d["secret"])
    assert pw["text"] == "***" and pw["source"]["id"] == "session/login"
    assert sess.error is None


async def test_death_brings_us_back_through_the_menu(tmp_path):
    mud = FakeMud(die_after_look=True)
    sess, evs = await run_session(mud, tmp_path)
    types = [e.type for e in evs]
    assert "self.died" in types
    assert types.count("connection.in_game") == 2
    assert mud.received[:5] == [b"Vallen", b"hunter2", b"", b"1", b"1"]


async def test_unknown_character_is_never_created(tmp_path):
    mud = FakeMud(known=())
    sess, evs = await run_session(mud, tmp_path, name="Nobody", seconds=1.0)
    assert mud.received == [b"Nobody", b"N"]
    assert sess.stopped and "does not exist" in sess.error
