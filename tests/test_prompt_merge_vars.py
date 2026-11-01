"""Guard: a prompts/*.md file may only use {{...}} where load_prompt fills it.

prompts/writer.md has twice regressed to literal {{first_name}} / {{company}} /
{{title}} in its instruction text (first fixed 2026-10-26, the regression came
back through an upstream merge and was fixed again on 2026-10-31). Brain
.load_prompt substitutes only the kwargs its call sites pass; any other
{{token}} stays literal, trips the "unfilled variables" warning on every writer
run, and can be copied verbatim into a drafted email.

The sanctioned places for merge-variable instructions are the merge_rule
strings in harvey/agents/writer.py (injected at runtime) and plain phrasing in
the .md itself: bare names ("Merge variables available: first_name, company,
title") or [square bracket] placeholders ("Hi [first name]").

How this test decides what is legal:

- For each prompt name the fillable slots are read from the actual
  load_prompt("name", key=...) call sites in the harvey/ package, so a new
  kwarg added at a call site plus its matching {{slot}} in the file passes
  without editing this test.
- The writer and handler call-site slot sets are additionally pinned below.
  If the parser stops finding them (refactor, rename, string change), the test
  fails loudly instead of silently guarding nothing; update the pin consciously
  when a call site legitimately gains or loses a kwarg.
- Prompt files with no load_prompt call site today (scout, system) keep their
  current template slots as an explicit allowlist, so they stay
  fillable-in-principle but cannot quietly gain new literal {{...}} text.

This module deliberately imports nothing from harvey: the guard must work even
when the package is broken.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "prompts"
PACKAGE_DIR = REPO_ROOT / "harvey"

# Pinned call-site slot sets (parser cross-check).
EXPECTED = {
    "writer": {
        "product_name",
        "product_description",
        "product_benefits",
        "product_pricing",
        "persona_name",
        "persona_company",
        "persona_role",
        "persona_tone",
    },
    "handler": {"stage"},
}

# Files no load_prompt call site reads today: scout.py builds its scoring
# prompt inline and system.md is the persona block. Pin their template slots.
EXTRA_SLOTS = {
    "scout": {"industries", "company_size", "geography", "titles"},
    "system": {"company_name", "product_description"},
}

# load_prompt("name", ...) with a literal prompt name — the only shape used.
_CALL_RE = re.compile(r"""load_prompt\(\s*["'](\w+)["']""")
# A keyword argument at a call site: name=value, but not ==, <=, >=, !=, +=.
_KWARG_RE = re.compile(r"(?<![\w.!=<>+*/%'-])([A-Za-z_]\w*)\s*=(?!=)")
# Any {{...}} on a line of a prompt file.
_TOKEN_RE = re.compile(r"\{\{(.*?)\}\}")


def _skip_string(src: str, i: int) -> int:
    """Return the index just past the quoted string starting at src[i]."""
    quote = src[i]
    i += 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return i


def _call_span_end(src: str, start: int) -> int:
    """Index just past the closing paren of a call whose '(' is at start."""
    depth = 1
    i = start + 1
    while i < len(src) and depth:
        c = src[i]
        if c in "\"'":
            i = _skip_string(src, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    return i


def _call_site_slots() -> dict:
    """Map prompt name -> set of kwargs its load_prompt call sites pass."""
    slots: dict = {}
    for py in sorted(PACKAGE_DIR.rglob("*.py")):
        src = py.read_text(encoding="utf-8")
        for m in _CALL_RE.finditer(src):
            # m.start() + len("load_prompt") is the index of the '(' itself.
            end = _call_span_end(src, m.start() + len("load_prompt"))
            span = src[m.end():end - 1]
            names = set(_KWARG_RE.findall(span))
            slots.setdefault(m.group(1), set()).update(names)
    return slots


def _violations() -> list:
    filled = _call_site_slots()
    problems = []
    for md in sorted(PROMPTS_DIR.glob("*.md")):
        prompt = md.stem
        allowed = filled.get(prompt, set()) | EXTRA_SLOTS.get(prompt, set())
        text = md.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in _TOKEN_RE.finditer(line):
                token = m.group(1)
                if token != token.strip():
                    problems.append(
                        f"{md.name}:{lineno}: {m.group(0)} — load_prompt "
                        "replaces the exact {{token}} with no inner spaces, "
                        "so this variant would never be filled"
                    )
                elif token not in allowed:
                    problems.append(
                        f"{md.name}:{lineno}: literal {{{{{token}}}}} — "
                        f"load_prompt fills only {sorted(allowed)} for "
                        f"'{prompt}'. Rewrite the instruction text without "
                        "double braces (bare names or [square brackets]); "
                        "merge-variable instructions belong in the agent code"
                    )
    return problems


def test_prompts_exist_so_the_guard_checks_something():
    files = sorted(PROMPTS_DIR.glob("*.md"))
    assert files, "no prompts/*.md found — the merge-var guard has nothing to scan"


def test_call_site_slots_match_the_pins():
    """The parser must find exactly the pinned kwarg sets.

    If this fails after a refactor, either the call sites changed (update
    EXPECTED consciously) or the parser broke (fix it — a silent parser
    failure would let any literal {{token}} through the file guard).
    """
    filled = _call_site_slots()
    for name in sorted(EXPECTED):
        assert filled.get(name, set()) == EXPECTED[name], (
            f"load_prompt('{name}', ...) call sites now pass "
            f"{sorted(filled.get(name, set()))}, expected "
            f"{sorted(EXPECTED[name])}. Update EXPECTED in "
            "tests/test_prompt_merge_vars.py if this is intentional."
        )


def test_no_unfillable_merge_var_literals():
    problems = _violations()
    assert not problems, (
        "prompts/*.md contain {{...}} tokens load_prompt never fills (this is "
        "how prompts/writer.md regressed twice):\n" + "\n".join(problems)
    )
