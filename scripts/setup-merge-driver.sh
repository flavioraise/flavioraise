#!/usr/bin/env sh
# One-time per-clone setup so pull/rebase never stops on a generated-SVG conflict.
#
# The daily CI workflow regenerates and commits assets/*.svg, which conflicts with
# your locally regenerated copies. Your local commit's SVG already matches its own
# config.json, so on a rebase we just keep the replayed commit's version.
set -e
cd "$(git rev-parse --show-toplevel)"

# 1. Always rebase on pull, so `git pull` never creates a merge commit and the
#    driver's %A/%B roles stay consistent (%B = the replayed local commit).
git config pull.rebase true

# 2. The "keep-local-svg" driver keeps the replayed commit's SVG (%B) as the
#    result. It touches no tracked file, so it works even on the first rebase
#    that introduces this setup. Lives in .git/config (not tracked).
git config merge.keep-local-svg.name "keep the local commit's generated SVG"
git config merge.keep-local-svg.driver "cp %B %A"

# 3. Mirror the attribute into .git/info/attributes (per-clone, always active
#    regardless of the checked-out tree) so the driver fires on every rebase,
#    including the one that first adds the tracked .gitattributes.
attrs="$(git rev-parse --git-dir)/info/attributes"
line="assets/*.svg merge=keep-local-svg"
grep -qxF "$line" "$attrs" 2>/dev/null || printf '%s\n' "$line" >> "$attrs"

echo "Done. 'git pull' now rebases and auto-resolves assets/*.svg conflicts."
