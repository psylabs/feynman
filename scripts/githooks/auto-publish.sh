#!/bin/bash
# Publish a fresh OTA bundle when the latest commit changed web/. No-op
# otherwise. Invoked by the post-commit / post-merge hooks (see this dir).
#
# Runs on the Mac mini, the OTA host: publish-bundle.mjs zips web/ into
# data/bundles/ and repoints latest.json, which the device polls. Safe to run
# from a hook — publish-bundle.mjs makes no git commit, so it never re-triggers
# itself. Activated by: git config core.hooksPath scripts/githooks
set -u
root="$(git rev-parse --show-toplevel)" || exit 0
cd "$root" || exit 0

# Only publish when the just-applied commit actually touched web/.
if ! git diff-tree --no-commit-id --name-only -r HEAD -- web | grep -q .; then
  exit 0
fi

log="logs/ota-publish.log"
mkdir -p logs
sha="$(git rev-parse --short HEAD)"
echo "[$(date '+%F %T')] web/ changed in $sha — publishing OTA bundle" | tee -a "$log"
if npm run --silent publish >>"$log" 2>&1; then
  echo "[$(date '+%F %T')] OTA publish OK ($sha)" | tee -a "$log"
else
  echo "[$(date '+%F %T')] OTA publish FAILED ($sha) — see $log" | tee -a "$log" >&2
fi
exit 0  # never fail the commit/merge on a publish hiccup
