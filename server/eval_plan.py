"""The first-time evaluation: a fixed 20-question plan that samples each skill
across three difficulty levels so we can establish a real baseline before any
adaptive drilling kicks in.

Each entry is (skill_id, level). Five questions per skill: 2 at L1, 2 at L2,
1 at L3. Skills are interleaved so a session never feels monotonous, and
difficulty ramps up across the run.
"""

from typing import Final

EVAL_PLAN: Final[list[tuple[str, int]]] = [
    ("addition", 1),
    ("subtraction", 1),
    ("multiplication", 1),
    ("percent_of", 1),
    ("addition", 1),
    ("subtraction", 1),
    ("multiplication", 1),
    ("percent_of", 1),
    ("addition", 2),
    ("subtraction", 2),
    ("multiplication", 2),
    ("percent_of", 2),
    ("addition", 2),
    ("subtraction", 2),
    ("multiplication", 2),
    ("percent_of", 2),
    ("addition", 3),
    ("subtraction", 3),
    ("multiplication", 3),
    ("percent_of", 3),
]

EVAL_LENGTH = len(EVAL_PLAN)


def step(position: int) -> tuple[str, int]:
    """Look up the (skill_id, level) for a 1-based question position."""
    if not (1 <= position <= EVAL_LENGTH):
        raise ValueError(f"position {position} outside eval plan")
    return EVAL_PLAN[position - 1]
