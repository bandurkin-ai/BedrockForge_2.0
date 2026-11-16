#!/usr/bin/env bash
# Prints the working state at session start, resume and after compaction.
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
echo "== Branch: $(git branch --show-current)"
git status --short | head -n 20
echo "== Last commits"
git log --oneline -5
echo "== PROGRESS.md"
awk '/^## Сейчас/{p=1} /^## План этапа/{p=0} p' PROGRESS.md 2>/dev/null | head -n 30
exit 0
