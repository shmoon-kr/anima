"""Turn tbaMUD message templates into line matchers.

Patterns are written in the server's own template language so each one can be checked against
the source string it came from:

- act() codes (comm.c perform_act): $n $N names, $e $E he/she/it, $m $M him/her/it,
  $s $S his/her/its, $p $P $o $O objects, $a $A a/an, $T $t $F text, $u $U case hints, $$.
- printf codes: %d %i (number), %s (text), with optional width like %-20s or %2i.
- '@' colour codes (lib/misc/messages) are dropped.

act() capitalises the first character of every message (CAP in perform_act), so the first
character matches either case.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

_PRONOUN = {
    "e": "(?:he|she|it)", "E": "(?:he|she|it)",
    "m": "(?:him|her|it)", "M": "(?:him|her|it)",
    "s": "(?:his|her|its)", "S": "(?:his|her|its)",
    "a": "(?:a|an)", "A": "(?:a|an)",
}
_GROUP = {"n": "n", "N": "N", "p": "p", "o": "p", "P": "P", "O": "P", "T": "T", "t": "T", "F": "T"}
_AT_CODE = re.compile(r"@\[[^\]]*\]|@[a-zA-Z*]")
_TOKEN = re.compile(r"\$.|%-?\d*[dis%]|#[wW]")
_WORD = re.compile(r"[a-z]+")


@dataclass
class Compiled:
    regex: re.Pattern[str]
    literal_len: int
    key_words: list[str]


def compile_template(template: str, extra: dict[str, str] | None = None) -> Compiled:
    """Compile one template line into an anchored regex.

    `extra` maps tokens such as '#w' to ready-made regex fragments (used for weapon verbs).
    """
    t = _AT_CODE.sub("", template).replace("@@", "@").rstrip("\r\n")
    parts: list[str] = []
    literals: list[tuple[str, bool, bool]] = []   # (text, code_before, code_after)
    seen: set[str] = set()
    counters = {"d": 0, "s": 0}
    pos = 0
    tokens = list(_TOKEN.finditer(t))
    for idx, m in enumerate(tokens):
        if m.start() > pos:
            lit = t[pos:m.start()]
            parts.append(_literal(lit, first=(pos == 0)))
            literals.append((lit, pos != 0, True))
        tok = m.group(0)
        parts.append(_token_regex(tok, seen, counters, extra or {}, first=(m.start() == 0)))
        pos = m.end()
    if pos < len(t):
        lit = t[pos:]
        parts.append(_literal(lit, first=(pos == 0)))
        literals.append((lit, bool(tokens), False))
    regex = re.compile("^" + "".join(parts) + r"\s*$")
    literal_len = sum(len(l[0]) for l in literals)
    return Compiled(regex, literal_len, _key_words(literals))


def _literal(lit: str, first: bool) -> str:
    if first and lit and lit[0].isalpha():
        return f"[{lit[0].upper()}{lit[0].lower()}]" + re.escape(lit[1:])
    return re.escape(lit)


def _token_regex(tok: str, seen: set[str], counters: dict[str, int], extra: dict[str, str],
                 first: bool = False) -> str:
    if tok in extra:
        return extra[tok]
    if tok.startswith("$"):
        c = tok[1]
        if c == "$":
            return re.escape("$")
        if c in ("u", "U"):
            return ""
        if c in _PRONOUN:
            if first:                        # act() capitalises the first letter: "She is ..."
                return "(?:" + "|".join(f"[{w[0].upper()}{w[0]}]{w[1:]}" for w in _PRONOUN[c][3:-1].split("|")) + ")"
            return _PRONOUN[c]
        if c in _GROUP:
            g = _GROUP[c]
            if g in seen:
                return f"(?P={g})"
            seen.add(g)
            return f"(?P<{g}>.+?)" if g != "T" else f"(?P<{g}>.*?)"
        return re.escape(tok)
    if tok == "%%":
        return "%"
    kind = tok[-1]
    if kind in ("d", "i"):
        name = f"d{counters['d']}"
        counters["d"] += 1
        return rf"\s*(?P<{name}>-?\d+)"
    name = f"s{counters['s']}"
    counters["s"] += 1
    return f"(?P<{name}>.*?)"


def _key_words(literals: list[tuple[str, bool, bool]]) -> list[str]:
    """Literal words that must appear as whole tokens in any matching line."""
    words: list[str] = []
    for text, code_before, code_after in literals:
        low = text.lower()
        for m in _WORD.finditer(low):
            if code_before and m.start() == 0:
                continue          # may be glued to a substituted value
            if code_after and m.end() == len(low):
                continue
            if len(m.group(0)) >= 3:
                words.append(m.group(0))
    return words


@dataclass
class Rule:
    """One message the adapter recognises.

    `build(groups) -> (type, data) | None`. Returning None marks the line as known but
    not worth an event (so it does not count as `unknown`).
    """
    template: str
    build: Callable[[dict[str, Any]], tuple[str, dict[str, Any]] | None]
    extra: dict[str, str] | None = None
    compiled: Compiled = field(init=False)

    def __post_init__(self) -> None:
        self.compiled = compile_template(self.template, self.extra)


class RuleSet:
    """Matches a line against many rules quickly.

    Each rule is indexed by its longest literal word; a line only tries rules whose key word
    it contains. Rules without a usable word are always tried. When several rules match, the
    one with the most literal text wins (the most specific message).
    """

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.index: dict[str, list[Rule]] = {}
        self.always: list[Rule] = []
        for r in rules or []:
            self.add(r)

    def add(self, rule: Rule) -> None:
        words = rule.compiled.key_words
        if not words:
            self.always.append(rule)
            return
        key = max(words, key=len)
        self.index.setdefault(key, []).append(rule)

    def match(self, text: str) -> tuple[Rule, dict[str, Any]] | None:
        tokens = set(_WORD.findall(text.lower()))
        best: tuple[Rule, dict[str, Any]] | None = None
        best_len = -1
        candidates = list(self.always)
        for tok in tokens:
            rules = self.index.get(tok)
            if rules:
                candidates.extend(rules)
        for rule in candidates:
            if rule.compiled.literal_len <= best_len:
                continue
            m = rule.compiled.regex.match(text)
            if m:
                best = (rule, m.groupdict())
                best_len = rule.compiled.literal_len
        return best
