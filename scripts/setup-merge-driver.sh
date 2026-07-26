#!/usr/bin/env sh
# Register the "regen-svg" merge driver referenced by .gitattributes.
# The driver config lives in .git/config, which is NOT tracked, so each clone
# must run this once. On a conflict in assets/*.svg it re-runs the generator and
# uses the fresh output as the merge result — no manual conflict resolution.
set -e
cd "$(git rev-parse --show-toplevel)"

# 1. Register the driver implementation (lives in .git/config, not tracked).
#    The helper regenerates into a scratch dir and writes only git's result file
#    (%A), so it never dirties the tracked working-tree SVGs during a rebase.
git config merge.regen-svg.name "regenerate profile SVGs"
git config merge.regen-svg.driver "sh scripts/regen-svg-merge.sh %O %A %P"

# 2. Also map the attribute in .git/info/attributes. The tracked .gitattributes
#    only applies once its commit is checked out, so it can't self-resolve the
#    very rebase that introduces it. .git/info/attributes is per-clone and always
#    active, so the driver fires on every pull/rebase regardless.
attrs="$(git rev-parse --git-dir)/info/attributes"
line="assets/*.svg merge=regen-svg"
grep -qxF "$line" "$attrs" 2>/dev/null || printf '%s\n' "$line" >> "$attrs"

echo "Registered 'regen-svg' merge driver. Pulls/rebases now auto-resolve assets/*.svg."
