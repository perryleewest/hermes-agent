#!/usr/bin/env bash
# stop-hook-git-check.sh
#
# Warn when a repo has work that is genuinely unsaved.
#
# Written 2026-09-14 to replace a version that fired twice on work that was
# already safe. Both false alarms would have caused damage if acted on:
#
#   1. A vault clone made on Linux showed 6 "modified" files -- 14,338
#      insertions and 14,338 deletions, and zero real content change. Pure
#      CRLF->LF normalisation, because the vault's files were authored on
#      Windows and its .gitattributes normalises to LF in the index.
#      mesh-brain has been bitten by this before: an additive 3,350-line
#      backfill once reported 39,608 insertions and 36,400 deletions, and
#      "during an audit that looked exactly like mass data loss".
#      Pushing it would have recreated that, mid obsidian-git sync.
#
#   2. A branch whose commit was already on the remote AND already merged
#      into main was reported as "1 unpushed commit, no remote branch",
#      because a `git fetch origin main` had not refreshed that branch's
#      remote-tracking ref. Local HEAD and the remote ref were byte-identical.
#
# The fix in both cases is the same: ask whether the thing is real before
# reporting it.
#
#   dirty tree   -> re-check with --ignore-cr-at-eol; line endings alone
#                   are not unsaved work
#   unpushed     -> re-check with merge-base --is-ancestor against the
#                   remote default branch; an already-merged commit is not
#                   unpushed, whatever the tracking config says

set -uo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root" || exit 0

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
findings=()

# ---------------------------------------------------------------------------
# 1. Uncommitted changes -- but only ones that survive ignoring line endings
# ---------------------------------------------------------------------------
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  # Staged + unstaged, ignoring CR-at-EOL. Empty means the only difference is
  # line endings, which git will normalise on its own and which carry no
  # content change.
  real_diff=$(git diff --ignore-cr-at-eol --name-only 2>/dev/null; \
              git diff --cached --ignore-cr-at-eol --name-only 2>/dev/null)
  # Untracked files are always real -- nothing to normalise away.
  untracked=$(git ls-files --others --exclude-standard 2>/dev/null)

  if [ -n "$real_diff" ] || [ -n "$untracked" ]; then
    n=$(printf '%s\n%s\n' "$real_diff" "$untracked" | grep -c . || true)
    findings+=("$n file(s) with real uncommitted changes")
  fi
  # else: line-ending-only churn. Silent by design. Do NOT commit this --
  # see the header. `git checkout -- .` will not clear it while .gitattributes
  # normalises; just leave the clone alone, or delete it if you are done.
fi

# ---------------------------------------------------------------------------
# 2. Unpushed commits -- but only ones not already contained upstream
# ---------------------------------------------------------------------------
default_branch=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)
default_branch=${default_branch#origin/}
[ -z "$default_branch" ] && default_branch=main

already_upstream=false
# Already merged into the remote default branch? Then it is not unpushed.
if git rev-parse --verify --quiet "refs/remotes/origin/$default_branch" >/dev/null 2>&1; then
  if git merge-base --is-ancestor HEAD "refs/remotes/origin/$default_branch" 2>/dev/null; then
    already_upstream=true
  fi
fi

# Or the remote branch exists at exactly this commit, tracking config aside.
if [ "$already_upstream" = false ]; then
  remote_sha=$(git ls-remote --heads origin "$branch" 2>/dev/null | cut -f1)
  [ -n "$remote_sha" ] && [ "$remote_sha" = "$(git rev-parse HEAD)" ] && already_upstream=true
fi

if [ "$already_upstream" = false ]; then
  if upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
    ahead=$(git rev-list --count "$upstream"..HEAD 2>/dev/null || echo 0)
    [ "${ahead:-0}" -gt 0 ] && findings+=("$ahead commit(s) not pushed to $upstream")
  else
    n=$(git rev-list --count "refs/remotes/origin/$default_branch"..HEAD 2>/dev/null || echo 0)
    [ "${n:-0}" -gt 0 ] && findings+=("$n commit(s) on '$branch', which has no remote branch")
  fi
fi

# ---------------------------------------------------------------------------
[ ${#findings[@]} -eq 0 ] && exit 0

echo "Unsaved work in $repo_root (branch '$branch'):"
for f in "${findings[@]}"; do echo "  - $f"; done
echo "Commit and push if this is work you want to keep."
