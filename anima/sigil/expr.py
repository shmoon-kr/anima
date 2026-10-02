"""The Sigil expression language (D19): small, pure, parsed by hand, never eval'd.

    expr    := or
    or      := and ('or' and)*
    and     := not ('and' not)*
    not     := 'not' not | cmp
    cmp     := sum (('=='|'!='|'<'|'<='|'>'|'>='|'in'|'not in') sum)?
    sum     := prod (('+'|'-') prod)*
    prod    := unary (('*'|'/') unary)*
    unary   := '-' unary | atom
    atom    := number | string | true | false | null | [list] | name(.name)* | call | '(' expr ')'
    call    := name '(' args ')'

No assignment, loops, recursion, indexing or attribute access beyond dotted names.
Expressions are bounded by size, so evaluation always ends quickly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

MAX_NODES = 200
MAX_DEPTH = 24

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>'[^']*'|"[^"]*")
  | (?P<op>==|!=|<=|>=|<|>|\+|-|\*|/|\(|\)|\[|\]|,)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
""", re.X)
_KEYWORDS = {"and", "or", "not", "in", "true", "false", "null"}


class ExprError(ValueError):
    pass


# AST: tuples. ("lit", v) ("name", "a.b") ("call", "f", [args]) ("list", [items])
#      ("un", op, x) ("bin", op, a, b)
Node = tuple


def _tokens(src: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise ExprError(f"unexpected character {src[pos]!r} at {pos} in {src!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        text = m.group(0)
        if kind == "name" and text in _KEYWORDS:
            kind = "kw"
        out.append((kind, text))
    out.append(("end", ""))
    return out


class _Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.toks = _tokens(src)
        self.i = 0
        self.nodes = 0

    def peek(self, k: int = 0) -> tuple[str, str]:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def take(self) -> tuple[str, str]:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, text: str) -> None:
        kind, t = self.take()
        if t != text:
            raise ExprError(f"expected {text!r}, got {t or 'end'!r} in {self.src!r}")

    def node(self, n: Node, depth: int) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ExprError(f"expression too large: {self.src!r}")
        return n

    def parse(self) -> Node:
        n = self.or_(0)
        if self.peek()[0] != "end":
            raise ExprError(f"unexpected {self.peek()[1]!r} in {self.src!r}")
        if _depth(n) > MAX_DEPTH:
            raise ExprError(f"expression nested too deeply: {self.src!r}")
        return n

    def or_(self, d: int) -> Node:
        a = self.and_(d + 1)
        while self.peek() == ("kw", "or"):
            self.take()
            a = self.node(("bin", "or", a, self.and_(d + 1)), d)
        return a

    def and_(self, d: int) -> Node:
        a = self.not_(d + 1)
        while self.peek() == ("kw", "and"):
            self.take()
            a = self.node(("bin", "and", a, self.not_(d + 1)), d)
        return a

    def not_(self, d: int) -> Node:
        if self.peek() == ("kw", "not"):
            self.take()
            return self.node(("un", "not", self.not_(d + 1)), d)
        return self.cmp(d + 1)

    def cmp(self, d: int) -> Node:
        a = self.sum(d + 1)
        k, t = self.peek()
        if t in ("==", "!=", "<", "<=", ">", ">=") and k == "op":
            self.take()
            return self.node(("bin", t, a, self.sum(d + 1)), d)
        if (k, t) == ("kw", "in"):
            self.take()
            return self.node(("bin", "in", a, self.sum(d + 1)), d)
        if (k, t) == ("kw", "not") and self.peek(1) == ("kw", "in"):
            self.take(), self.take()
            return self.node(("bin", "not in", a, self.sum(d + 1)), d)
        return a

    def sum(self, d: int) -> Node:
        a = self.prod(d + 1)
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            a = self.node(("bin", op, a, self.prod(d + 1)), d)
        return a

    def prod(self, d: int) -> Node:
        a = self.unary(d + 1)
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            a = self.node(("bin", op, a, self.unary(d + 1)), d)
        return a

    def unary(self, d: int) -> Node:
        if self.peek() == ("op", "-"):
            self.take()
            return self.node(("un", "-", self.unary(d + 1)), d)
        return self.atom(d + 1)

    def atom(self, d: int) -> Node:
        kind, t = self.take()
        if kind == "num":
            return self.node(("lit", float(t) if "." in t else int(t)), d)
        if kind == "str":
            return self.node(("lit", t[1:-1]), d)
        if kind == "kw" and t in ("true", "false", "null"):
            return self.node(("lit", {"true": True, "false": False, "null": None}[t]), d)
        if (kind, t) == ("op", "("):
            n = self.or_(d + 1)
            self.expect(")")
            return n
        if (kind, t) == ("op", "["):
            items = []
            if self.peek() != ("op", "]"):
                items.append(self.or_(d + 1))
                while self.peek() == ("op", ","):
                    self.take()
                    items.append(self.or_(d + 1))
            self.expect("]")
            return self.node(("list", items), d)
        if kind == "name":
            if self.peek() == ("op", "("):
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.or_(d + 1))
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.or_(d + 1))
                self.expect(")")
                return self.node(("call", t, args), d)
            return self.node(("name", t), d)
        raise ExprError(f"unexpected {t or 'end'!r} in {self.src!r}")


@dataclass(frozen=True)
class Expr:
    src: str
    ast: Node

    def names(self) -> set[str]:
        out: set[str] = set()
        _walk(self.ast, lambda n: out.add(n[1]) if n[0] == "name" else None)
        return out

    def calls(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        _walk(self.ast, lambda n: out.append((n[1], len(n[2]))) if n[0] == "call" else None)
        return out


def _depth(n: Node) -> int:
    k = n[0]
    if k == "call":
        return 1 + max((_depth(a) for a in n[2]), default=0)
    if k == "list":
        return 1 + max((_depth(a) for a in n[1]), default=0)
    if k == "un":
        return 1 + _depth(n[2])
    if k == "bin":
        return 1 + max(_depth(n[2]), _depth(n[3]))
    return 1


def _walk(n: Node, fn: Callable[[Node], None]) -> None:
    fn(n)
    if n[0] == "call":
        for a in n[2]:
            _walk(a, fn)
    elif n[0] == "list":
        for a in n[1]:
            _walk(a, fn)
    elif n[0] == "un":
        _walk(n[2], fn)
    elif n[0] == "bin":
        _walk(n[2], fn)
        _walk(n[3], fn)


def parse(src: str | int | float | bool) -> Expr:
    if isinstance(src, bool):
        return Expr(str(src).lower(), ("lit", src))
    if isinstance(src, (int, float)):
        return Expr(str(src), ("lit", src))
    if not isinstance(src, str) or not src.strip():
        raise ExprError(f"empty or non-text expression: {src!r}")
    return Expr(src, _Parser(src).parse())


Resolver = Callable[[str], Any]


def evaluate(e: Expr | Node, resolve: Resolver, functions: dict[str, Callable[..., Any]]) -> Any:
    n = e.ast if isinstance(e, Expr) else e
    k = n[0]
    if k == "lit":
        return n[1]
    if k == "name":
        return resolve(n[1])
    if k == "list":
        return [evaluate(x, resolve, functions) for x in n[1]]
    if k == "call":
        fn = functions.get(n[1])
        if fn is None:
            raise ExprError(f"unknown function {n[1]}")
        return fn(*[evaluate(x, resolve, functions) for x in n[2]])
    if k == "un":
        v = evaluate(n[2], resolve, functions)
        return (not v) if n[1] == "not" else -_num(v)
    op = n[1]
    if op == "and":
        return bool(evaluate(n[2], resolve, functions)) and bool(evaluate(n[3], resolve, functions))
    if op == "or":
        return bool(evaluate(n[2], resolve, functions)) or bool(evaluate(n[3], resolve, functions))
    a = evaluate(n[2], resolve, functions)
    b = evaluate(n[3], resolve, functions)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "in":
        return a in b if b is not None else False
    if op == "not in":
        return a not in b if b is not None else True
    if op in ("<", "<=", ">", ">="):
        if a is None or b is None:
            return False
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    if op == "+" and (isinstance(a, str) or isinstance(b, str)):
        return f"{'' if a is None else a}{'' if b is None else b}"      # text joining: 'follow ' + party.leader
    a, b = _num(a), _num(b)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return a / b if b else 0
    raise ExprError(f"bad operator {op}")


def _num(v: Any) -> float:
    if v is None or v is False:
        return 0
    if v is True:
        return 1
    if isinstance(v, (int, float)):
        return v
    raise ExprError(f"not a number: {v!r}")
