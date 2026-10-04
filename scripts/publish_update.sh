#!/bin/sh
# Publish this repository's latest commits to the public repository, safely (MAINTAINING.md).
#
#   sh scripts/publish_update.sh <public-copy-folder> <remote-url> "<public name>" <public email> [prepare options]
#
# 1. Rebuilds the public copy from scratch with prepare_public_repo.sh (identities rewritten, the
#    .public-replacements rules applied, the whole history checked for personal data). Any failure stops here.
# 2. Fetches the public repository and pushes only when that's a fast-forward: the rebuild reproduces the
#    published history exactly, so new commits simply go on top. Anything else (a rule or an old commit
#    changed) stops without pushing; published history is never rewritten.
# The folder is deleted first only if it's a public copy built before (it has .git/filter-repo).
set -eu

[ $# -ge 4 ] || { echo "usage: sh scripts/publish_update.sh <public-copy-folder> <remote-url> \"<name>\" <email> [prepare options]" >&2; exit 2; }
target=$1 remote=$2 name=$3 email=$4
shift 4

if [ -e "$target" ]; then
  [ -d "$target/.git/filter-repo" ] || { echo "publish_update: $target exists and isn't a previous public copy; not deleting it" >&2; exit 1; }
  rm -rf "$target"
fi

sh "$(dirname "$0")/prepare_public_repo.sh" "$target" "$name" "$email" "$@"

git -C "$target" remote add origin "$remote"
git -C "$target" fetch -q origin
branch=$(git -C "$target" rev-parse --abbrev-ref HEAD)
if git -C "$target" rev-parse -q --verify "origin/$branch" >/dev/null; then
  if ! git -C "$target" merge-base --is-ancestor "origin/$branch" "$branch"; then
    echo "publish_update: the rebuilt history doesn't extend what's published (a rule or an old commit changed)." >&2
    echo "Nothing was pushed. Published history is never rewritten; undo the change that caused it." >&2
    exit 1
  fi
  count=$(git -C "$target" rev-list --count "origin/$branch..$branch")
  [ "$count" -gt 0 ] || { echo "Nothing new to publish."; exit 0; }
  echo "==> Pushing $count new commit(s)"
fi
git -C "$target" push -u origin "$branch"
echo "Published."
