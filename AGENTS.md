
# Agents.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Pre-Commit Privacy Check

**This is a public repo. Before every commit, verify nothing sensitive or unintended is staged.**

Check each staged file for:
- Personal data: real names, email addresses, home locations, financial figures tied to the user
- Credentials: API keys, tokens, passwords, private URLs
- Files that should be gitignored: anything under `data/`, `logs/`, `.env`
- Casual personal notes in docs (e.g. `docs/todo.md`) that weren't meant for public consumption

If any staged file is ambiguous, stop and ask before committing.

---

## Project gotchas (Feynman mobile + Mac mini backend)

Hard-won notes for future agents working on the Capacitor mobile migration.

### Environment
- **The dev machine IS the backend host** — the Mac mini, tailnet FQDN
  `pips-mac-mini.tail72bfb3.ts.net`. The target phone is `motorola-razr-2025`
  on the same tailnet.
- **`.venv` is uv-managed and has no `pip`/`pytest`.** Run the suite with
  `uv run --with pytest pytest`. Plain `python -m pytest` / `pip` will fail.
- **`gh` is not installed.** Can't create PRs via CLI; merge locally and push.
- **Android toolchain (JDK/SDK/Android Studio) is not installed** by default.
  Building the APK is done in Android Studio (`npx cap open android`).

### Tailscale is the App Store (sandboxed) build — this matters
- CLI lives at `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.
- **`tailscale serve` hangs silently** (sandbox can't bind :443). Don't use it.
- **`tailscale cert` can only write to `~/Downloads`.** Other paths — even
  absolute repo paths — fail with `operation not permitted` (sandbox redirect).
  Mint into `~/Downloads`, then `mv` the files into the repo.
- HTTPS certs must first be enabled in the tailnet admin console
  (login.tailscale.com/admin/dns → Enable HTTPS), else you get
  `your Tailscale account does not support getting TLS certs`.

### Mobile architecture (Phase 0, on `main` as of 3f9b1f6)
- The APK **bundles** `web/` and serves it from `https://localhost` (a secure
  context, so the mic works). API calls go **cross-origin** to the backend over
  the Tailscale HTTPS hostname.
- `web/config.js` (loads first) rewrites `/`-prefixed `fetch`/`EventSource`
  URLs and TTS `audio_url` (via `apiUrl()`) to the backend base, and gates the
  service worker to browser-only. `server/main.py` has a CORS regex for
  `https?://localhost` and `capacitor://localhost`.
- The backend base is **hardcoded** in `config.js` (`FEYNMAN_API_BASE`). Moving
  it to an in-app settings screen is Phase 1.
- **The backend is launchd-managed** (LaunchAgent `com.pip.feynman`, defined in
  `deploy/launchd/`, runs `scripts/run-server.sh` with TLS, KeepAlive on). Don't
  `pkill` it expecting it to stay down — it respawns. Manage it with
  `launchctl kickstart -k gui/$(id -u)/com.pip.feynman` (restart) or
  `launchctl bootout gui/$(id -u)/com.pip.feynman` (stop). Logs:
  `~/Library/Logs/feynman-server.log`. A weekly `com.pip.feynman.certrenew`
  agent renews the cert and restarts only when it changes.
- **Certs live in gitignored `certs/` and expire ~90 days** (Let's Encrypt;
  current expiry 2026-09-16). Re-mint with `tailscale cert` before then.
- After changing `web/` or `capacitor.config.json`, run `npx cap sync android`.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
