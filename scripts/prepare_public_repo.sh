#!/bin/sh
# Build a PUBLIC copy of this repository in a new folder, without touching this one.
#
#   scripts/prepare_public_repo.sh TARGET_DIR "Public Name" public@users.noreply.github.com \
#       [--branch main] [--replace-text FILE] [--words FILE]
#
# 1. git clone --no-local this repo (one branch, default main) into TARGET_DIR (must not exist,
#    and must be outside this repo).
# 2. In that clone only, git filter-repo rewrites every author and committer in history to the
#    one name and email given, and, if an expressions file exists, replaces text in every file
#    and commit message. Default expressions file: .public-replacements at this repo's root
#    (git ignores it). Format (git filter-repo --replace-text), one rule per line:
#        Real Name==>Pat Smith
#        1234567890==>***REMOVED***
#        regex:\b\d{9,}\b==>ACCOUNT
# 3. Runs scripts/check_no_personal_data.py --history on the clone, with your .personal-words.
# 4. Prints what to do next. It never pushes or creates anything on GitHub.
set -eu

die() { echo "prepare_public_repo: $*" >&2; exit 1; }

usage() {
  sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

[ $# -ge 3 ] || usage
target=$1 name=$2 email=$3
shift 3
branch=main replace="" words=""
while [ $# -gt 0 ]; do
  case "$1" in
    --branch) [ $# -ge 2 ] || usage; branch=$2; shift 2 ;;
    --replace-text) [ $# -ge 2 ] || usage; replace=$2; shift 2 ;;
    --words) [ $# -ge 2 ] || usage; words=$2; shift 2 ;;
    *) usage ;;
  esac
done

case "$email" in *@*) ;; *) die "'$email' is not an email address (use your GitHub noreply address)" ;; esac
[ -n "$name" ] || die "the public name is empty"

if ! git filter-repo --version >/dev/null 2>&1; then
  die "git-filter-repo is not installed. Install it with:  brew install git-filter-repo
(or: python3 -m pip install --user git-filter-repo)"
fi

src=$(git rev-parse --show-toplevel) || die "run this from inside the BalancePoint repository"
common=$(cd "$src" && cd "$(git rev-parse --git-common-dir)" && pwd -P)
main_root=$(dirname "$common")   # the main checkout, when $src is a worktree
src=$(cd "$src" && pwd -P)

[ -e "$target" ] && die "$target already exists; give a new folder"
parent=$(dirname "$target")
[ -d "$parent" ] || die "folder $parent does not exist"
target_abs="$(cd "$parent" && pwd -P)/$(basename "$target")"
for root in "$src" "$main_root"; do
  case "$target_abs/" in "$root"/*) die "$target_abs is inside this repository ($root); pick a folder outside it" ;; esac
done

[ -z "$replace" ] && [ -f "$src/.public-replacements" ] && replace="$src/.public-replacements"
[ -n "$replace" ] && { [ -f "$replace" ] || die "replace-text file not found: $replace"; replace=$(cd "$(dirname "$replace")" && pwd -P)/$(basename "$replace"); }
[ -z "$words" ] && [ -f "$src/.personal-words" ] && words="$src/.personal-words"
[ -n "$words" ] && { [ -f "$words" ] || die "words file not found: $words"; words=$(cd "$(dirname "$words")" && pwd -P)/$(basename "$words"); }

git -C "$src" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null || die "no branch '$branch' here"

echo "==> Cloning $src ($branch) into $target_abs"
git clone --quiet --no-local --single-branch --branch "$branch" "$src" "$target_abs"

mailmap=$(mktemp "${TMPDIR:-/tmp}/public-mailmap.XXXXXX")
trap 'rm -f "$mailmap"' EXIT
# Every author and committer identity in history -> the one public identity.
git -C "$target_abs" log --format='%an <%ae>%n%cn <%ce>' | sort -u | while IFS= read -r who; do
  [ -n "$who" ] && printf '%s <%s> %s\n' "$name" "$email" "$who"
done > "$mailmap"
echo "==> Mapping $(wc -l < "$mailmap" | tr -d ' ') identities to: $name <$email>"

set -- --mailmap "$mailmap"
if [ -n "$replace" ]; then
  echo "==> Replacing text in files and commit messages from $replace"
  set -- "$@" --replace-text "$replace" --replace-message "$replace"
fi
git -C "$target_abs" filter-repo --quiet "$@"
git -C "$target_abs" remote remove origin 2>/dev/null || true

echo "==> Checking the public copy's whole history for personal data"
py=python3
[ -x "$src/.venv/bin/python" ] && py="$src/.venv/bin/python"
command -v "$py" >/dev/null 2>&1 || py=python
set -- --repo "$target_abs" --history
[ -n "$words" ] && set -- "$@" --words "$words"
if ! "$py" "$target_abs/scripts/check_no_personal_data.py" "$@"; then
  echo >&2
  echo "The public copy at $target_abs still has personal data (above). Add rules to" >&2
  echo "${replace:-$src/.public-replacements}, delete $target_abs and run this again." >&2
  exit 1
fi

echo "==> Remaining identities in the public copy:"
git -C "$target_abs" log --format='    %an <%ae>' | sort -u

cat <<EOF

Public copy ready: $target_abs  (this repository was not changed)

Next steps:
  1. Look it over:   cd "$target_abs" && git log --stat | less
  2. Check LICENSE (MIT) is there.
  3. Create an empty GitHub repository (no README/license), e.g.
       gh repo create balancepoint --public --source "$target_abs"
     or on github.com, then:
       git -C "$target_abs" remote add origin git@github.com:YOU/balancepoint.git
  4. Push:  git -C "$target_abs" push -u origin $branch
EOF
