#!/usr/bin/env sh
# git merge driver for the generated profile SVGs (assets/*.svg).
#
# Invoked as:  regen-svg-merge.sh %O %A %P
#   $1 = %O  ancestor version           (unused - we regenerate from source)
#   $2 = %A  our version / RESULT file   (git reads the merge result from here)
#   $3 = %P  the real pathname being merged, e.g. assets/contributions.svg
#
# The SVGs are pure build artifacts of scripts/generate.py, so on any conflict we
# just regenerate from the (already-merged) source and use that. We generate into
# a scratch dir via PROFILE_OUT_DIR so the tracked working-tree copies are never
# touched mid-rebase (touching them makes git abort with "local changes would be
# overwritten"). Only %A is written, which is what git consumes as the result.
set -e
root=$(git rev-parse --show-toplevel)
cd "$root"

out=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/regen-svg.$$")
mkdir -p "$out"
trap 'rm -rf "$out"' EXIT

PROFILE_OUT_DIR="$out" python scripts/generate.py >/dev/null 2>&1
cp "$out/$(basename "$3")" "$2"
