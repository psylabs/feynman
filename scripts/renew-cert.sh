#!/bin/bash
# Renew the Tailscale TLS cert for the backend and restart it if the cert
# changed. `tailscale cert` only re-fetches when the existing cert is near
# expiry, so this is safe (and cheap) to run on a schedule.
#
# Gotcha: the App Store (sandboxed) Tailscale CLI can only write files to
# ~/Downloads, so we mint there and move the result into the repo's certs/.
set -euo pipefail

TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
FQDN="pips-mac-mini.tail72bfb3.ts.net"
REPO="/Users/pip/Documents/Projects/feynman"
DL="$HOME/Downloads"

"$TS" cert --cert-file "$DL/feynman.crt" --key-file "$DL/feynman.key" "$FQDN"

if cmp -s "$DL/feynman.crt" "$REPO/certs/feynman.crt"; then
  rm -f "$DL/feynman.crt" "$DL/feynman.key"
  echo "$(date '+%Y-%m-%d %H:%M:%S') cert unchanged"
else
  mv -f "$DL/feynman.crt" "$REPO/certs/feynman.crt"
  mv -f "$DL/feynman.key" "$REPO/certs/feynman.key"
  chmod 600 "$REPO/certs/feynman.key"
  launchctl kickstart -k "gui/$(id -u)/com.pip.feynman"
  echo "$(date '+%Y-%m-%d %H:%M:%S') renewed cert and restarted backend"
fi
