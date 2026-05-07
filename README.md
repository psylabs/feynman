# Feynman

Personal voice-first cognitive trainer. Mac-local. See [docs/](docs/) for the full PRD, MVP spec, and architecture.

## Setup

Requires Python 3.11+, [uv](https://github.com/astral-sh/uv), and an OpenAI API key (used for STT and short tutor-style feedback):

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
cp .env.example .env  # then put your OPENAI_API_KEY in .env
```

## Run

```bash
python -m server.main
```

Then open http://127.0.0.1:8765 in a browser. The server binds to `0.0.0.0:8765` by default so devices on your local network or Tailnet can also reach it; set `FEYNMAN_HOST=127.0.0.1` in your environment to keep it strictly localhost.

## Run on your phone (private, no public URL)

The Mac runs the server; the phone is just a browser. Two private paths — pick one. Both keep the server reachable only by your devices, never the internet.

### Option A — Same WiFi (simplest)

When the phone and the Mac are on the same network:

1. Find your Mac's LAN IP: `ipconfig getifaddr en0` (or System Settings → Wi-Fi → Details).
2. On the phone, open `http://<that-ip>:8765`. Example: `http://192.168.1.42:8765`.
3. Allow microphone access when prompted.

Caveat: most browsers block `getUserMedia` on plain HTTP unless the host is `localhost` or `127.0.0.1`. Chrome on Android is the most permissive on a LAN; Safari on iOS will refuse. If recording doesn't work, use Tailscale (Option B) — its hostnames count as secure.

### Option B — Tailscale (works on cellular too)

[Tailscale](https://tailscale.com) is a zero-config VPN that puts your Mac and phone on the same private network. Free for personal use. Solves the HTTPS/microphone problem because Tailscale issues HTTPS certs for tailnet hostnames.

1. Install Tailscale on the Mac and phone, sign in with the same account.
2. On the Mac: `tailscale cert "$(tailscale status --json | jq -r .Self.DNSName | sed 's/\.$//')"` to mint a cert for the Mac's tailnet hostname.
3. Run the server (it already binds to `0.0.0.0`):
   ```bash
   python -m server.main
   ```
4. On the phone, open `https://<your-mac>.<tailnet>.ts.net:8765`. Microphone works because Tailscale's hostname is HTTPS.

Tailscale traffic is end-to-end encrypted between your devices; nothing is exposed publicly.

### Install as an app (Add to Home Screen)

The web app ships a PWA manifest, so once you've loaded it (LAN or Tailscale), Android Chrome offers **Add to Home Screen**, which opens it standalone (no URL bar). iOS Safari has the same option in the Share menu.

For the Add-to-Home-Screen prompt to fire reliably on Android, drop two PNG icons next to the rest of the static assets:

- `web/icon-192.png` (192×192)
- `web/icon-512.png` (512×512)

Without them the app still works; you just won't get the install banner.

## How a session works

Click **Start session**. Each question:

1. The system speaks a problem.
2. When the audio finishes, the **push-to-talk** button activates.
3. Press and hold (or hold spacebar), say your answer, release.
4. The result is shown briefly, then the next question starts.

After ~12 questions the session ends and a review is shown.

The right pane streams every event happening inside the system in real time — TTS, STT, scheduler decisions, grader verdicts. The same events are written to `logs/YYYY-MM-DD.jsonl` for later inspection.

## Files of interest

- `skills.yaml` — skill definitions (id, tolerance, target latency, templates).
- `schema.sql` — SQLite schema.
- `data/feynman.db` — local database (gitignored).
- `logs/` — daily JSONL event logs (gitignored).
