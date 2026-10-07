#!/bin/sh
# Make a run's results durable: commit files to the `tuning-results` branch.
#
#   tuning/ci/record.sh <dest-dir-in-branch> <file>...
#
# Runners are thrown away when a job ends and artifacts expire after 90 days, so anything
# worth keeping -- verdict summaries, SPSA state, small gzipped game files -- is committed
# to a branch of its own. A branch of its own because the working branches hold source and
# this holds measurements; the two have nothing to merge but RESULTS.md, which a person
# writes. Pushes race when several jobs finish together, so this retries after fetching.
#
# Needs: a checkout with `origin` and a token that may push (permissions: contents: write).
set -eu
dest="$1"; shift
branch="tuning-results"
work="$(mktemp -d)"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

if git fetch origin "$branch" 2>/dev/null; then
    git worktree add "$work" FETCH_HEAD >/dev/null
    (cd "$work" && git checkout -q -B "$branch")
else
    git worktree add --detach "$work" >/dev/null
    (cd "$work" && git checkout -q --orphan "$branch" && git rm -rfq . >/dev/null 2>&1 || true)
    printf '# tuning-results\n\nMeasurements committed by GitHub Actions. See tuning/RESULTS.md on the source branches.\n' > "$work/README.md"
fi

attempt=0
while :; do
    attempt=$((attempt + 1))
    mkdir -p "$work/$dest"
    for f in "$@"; do
        if [ -d "$f" ]; then cp -R "$f" "$work/$dest/"; else cp "$f" "$work/$dest/"; fi
    done
    (
        cd "$work"
        git add -A
        if git diff --cached --quiet; then echo "record: nothing new to commit"; exit 0; fi
        git commit -q -m "${GITHUB_WORKFLOW:-tuning}: ${GITHUB_RUN_ID:-local} -> $dest"
        git push -q origin "$branch"
    ) && break
    if [ "$attempt" -ge 5 ]; then echo "record: giving up after $attempt attempts" >&2; exit 1; fi
    echo "record: push rejected, refetching (attempt $attempt)"
    (cd "$work" && git fetch -q origin "$branch" && git reset -q --hard "origin/$branch")
done
git worktree remove --force "$work" >/dev/null 2>&1 || true
echo "record: committed to $branch/$dest"
