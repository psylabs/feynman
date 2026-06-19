"""Pre-generate a batch of drill problems (with cached TTS) so the mobile app
can drill offline.

This reuses the exact same scheduler -> generator -> TTS pipeline a live drill
session uses (see ``Orchestrator._produce_question``), just run N times up
front. The client stores the returned manifest locally and fetches each
``audio_url`` from ``/audio/{filename}`` (TTS is SHA-cached server-side, so a
seed pack is mostly cache hits). The ``tolerance_rule`` is included so the
client can grade typed answers offline with the same rules as the server.
"""

from server import generator, scheduler, tts

MAX_ITEMS = 500


def build_seed_pack(storage, user_id: str, n: int, emit) -> dict:
    """Return ``{user_id, count, items: [...]}`` where each item carries
    everything the client needs to run and grade one problem offline."""
    n = max(1, min(int(n), MAX_ITEMS))
    storage.ensure_skill_states_for_user(user_id)

    tol_cache: dict[str, dict] = {}

    def tolerance_for(skill_id: str) -> dict:
        if skill_id not in tol_cache:
            sk = storage.get_skill(skill_id)
            tol_cache[skill_id] = (sk or {}).get("tolerance", {})
        return tol_cache[skill_id]

    items = []
    for _ in range(n):
        pick = scheduler.pick_drill(storage, user_id, emit)
        skill_id = pick["skill_id"]
        problem = generator.generate(
            skill_id, level=pick["level"], target=pick.get("target_fact")
        )
        audio = tts.synthesize(problem["prompt"], emit)
        items.append(
            {
                "skill_id": skill_id,
                "level": pick["level"],
                "prompt_text": problem["prompt"],
                "expected": problem["expected"],
                "parameters": problem["parameters"],
                "tolerance_rule": tolerance_for(skill_id),
                "audio_url": f"/audio/{audio['filename']}",
                "audio_duration_ms": audio["duration_ms"],
            }
        )

    return {"user_id": user_id, "count": len(items), "items": items}
