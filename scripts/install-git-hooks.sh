#!/bin/sh
# Opt-in: install a pre-commit hook that runs the personal-data check before every commit.
# Nothing installs this automatically. Remove with: rm "$(git rev-parse --git-path hooks)/pre-commit"
set -e
hook=$(git rev-parse --git-path hooks)/pre-commit
case "$hook" in /*) ;; *) hook="$PWD/$hook" ;; esac

if [ -e "$hook" ] && ! grep -q check_no_personal_data "$hook"; then
  echo "A different pre-commit hook already exists at $hook; not replacing it." >&2
  echo "Add this line to it yourself:  python scripts/check_no_personal_data.py" >&2
  exit 1
fi

mkdir -p "$(dirname "$hook")"
cat > "$hook" <<'HOOK'
#!/bin/sh
# Installed by scripts/install-git-hooks.sh: refuse commits that would add personal data.
# Checks every tracked or staged file, plus your untracked .personal-words deny-list.
# Skip once (not recommended) with: git commit --no-verify
root=$(git rev-parse --show-toplevel)
if [ -x "$root/.venv/bin/python" ]; then py="$root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then py=python3
else py=python; fi
exec "$py" "$root/scripts/check_no_personal_data.py" --repo "$root"
HOOK
chmod +x "$hook"
echo "Installed pre-commit hook: $hook"
echo "Optional: list real names / account numbers to block, one per line, in .personal-words (git ignores it)."
