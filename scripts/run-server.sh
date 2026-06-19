#!/bin/bash
# Launch the Feynman backend with TLS so the bundled mobile app can reach it
# over the Tailscale HTTPS hostname. Invoked by the com.pip.feynman LaunchAgent.
set -euo pipefail

cd /Users/pip/Documents/Projects/feynman

exec .venv/bin/python -m uvicorn server.main:app \
  --host 0.0.0.0 --port 8765 \
  --ssl-certfile certs/feynman.crt \
  --ssl-keyfile certs/feynman.key
