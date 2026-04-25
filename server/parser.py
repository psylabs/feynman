"""Spoken-text → numeric parser.

Handles common forms heard in MVP:
  "twenty-five", "a hundred and twenty", "two hundred fifty", "forty-five",
  "five point five", "12", "12.5", "negative seven".
Falls back to bare digit extraction when word-parsing fails.
"""

import re

WORDS: dict[str, int] = {
    "zero": 0, "oh": 0, "nought": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
SCALES: dict[str, int] = {"hundred": 100, "thousand": 1000, "million": 1000000}

SKIP_PATTERNS = [
    r"\bskip\b",
    r"\bpass\b",
    r"\bdon'?t know\b",
    r"\bno idea\b",
    r"\bnot sure\b",
    r"\bdunno\b",
]

# Common Whisper homophones for short numeric utterances.
# Only applied when the entire (cleaned) transcript is a single homophone word —
# we don't want to rewrite "to" inside longer phrases.
SHORT_HOMOPHONES: dict[str, int] = {
    "to": 2, "too": 2,
    "for": 4, "fore": 4,
    "ate": 8,
    "won": 1,
    "tree": 3,
    "knot": 0, "naught": 0,
}


def parse(text: str) -> dict:
    """Return {"value": float|None, "skipped": bool, "raw": str}."""
    raw = (text or "").strip()
    if not raw:
        return {"value": None, "skipped": False, "raw": raw}

    cleaned = raw.lower().strip().rstrip(".!?")

    for pat in SKIP_PATTERNS:
        if re.search(pat, cleaned):
            return {"value": None, "skipped": True, "raw": raw}

    # Single-word homophones Whisper commonly emits for short numeric answers.
    bare = re.sub(r"[^a-z]", "", cleaned)
    if bare in SHORT_HOMOPHONES:
        return {"value": float(SHORT_HOMOPHONES[bare]), "skipped": False, "raw": raw}

    # Direct numeric form (digits, possibly negative, possibly decimal)
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    direct: float | None = None
    if m:
        try:
            direct = float(m.group())
        except ValueError:
            direct = None

    # Word-based form
    words_value = _from_words(cleaned)

    if direct is not None:
        return {"value": direct, "skipped": False, "raw": raw}
    if words_value is not None:
        return {"value": words_value, "skipped": False, "raw": raw}
    return {"value": None, "skipped": False, "raw": raw}


_TENS_WORDS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}


def _add_implicit_hundred(tokens: list[str]) -> list[str]:
    """Convert "one twenty three" → "one hundred twenty three" so the standard
    parser handles the common spoken form for numbers like 123, 250, 345.

    Only triggers when the first token is a single-digit word (one..nine), the
    second is a tens word (twenty..ninety), and "hundred"/"thousand"/"million"
    is not already in the token list.
    """
    if len(tokens) < 2:
        return tokens
    if any(s in tokens for s in ("hundred", "thousand", "million")):
        return tokens
    first, second = tokens[0], tokens[1]
    if first in WORDS and 1 <= WORDS[first] <= 9 and second in _TENS_WORDS:
        return [first, "hundred"] + tokens[1:]
    return tokens


def _from_words(text: str) -> float | None:
    text = text.replace("-", " ")
    tokens = re.findall(r"[a-z]+", text)
    if not tokens:
        return None

    negative = False
    if tokens and tokens[0] in ("negative", "minus"):
        negative = True
        tokens = tokens[1:]

    tokens = _add_implicit_hundred(tokens)

    # Split on "point" for decimals
    if "point" in tokens:
        i = tokens.index("point")
        whole_toks, frac_toks = tokens[:i], tokens[i + 1 :]
    else:
        whole_toks, frac_toks = tokens, []

    whole = _parse_int_words(whole_toks)
    if whole is None:
        return None

    frac = 0.0
    if frac_toks:
        digits = []
        for t in frac_toks:
            if t in WORDS and WORDS[t] < 10:
                digits.append(str(WORDS[t]))
            elif t == "and":
                continue
            else:
                return None
        if digits:
            frac = float("0." + "".join(digits))

    value = whole + frac
    return -value if negative else value


def _parse_int_words(tokens: list[str]) -> int | None:
    total = 0
    current = 0
    saw_any = False
    for tok in tokens:
        if tok == "and":
            continue
        if tok in WORDS:
            current += WORDS[tok]
            saw_any = True
        elif tok in SCALES:
            scale = SCALES[tok]
            if scale == 100:
                current = (current or 1) * 100
            else:
                current = (current or 1) * scale
                total += current
                current = 0
            saw_any = True
        elif tok.isdigit():
            current += int(tok)
            saw_any = True
        else:
            # unknown token — ignore
            continue
    if not saw_any:
        return None
    return total + current
