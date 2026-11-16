#!/usr/bin/env bash
# Warns (does not fail) when a module changed but docs/CHECKLIST.md did not (ТЗ 10.4, 10.6).
base="${1:-origin/main}"
changed=$(git diff --name-only "$base"...HEAD 2>/dev/null) || exit 0
if echo "$changed" | grep -qE '^(core/bf_core/[^/]+/|ui/src/modules/)' && ! echo "$changed" | grep -q '^docs/CHECKLIST.md$'; then
  echo "::warning::Изменены модули, но docs/CHECKLIST.md не обновлён"
fi
exit 0
