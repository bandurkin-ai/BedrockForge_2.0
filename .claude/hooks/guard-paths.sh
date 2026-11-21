#!/usr/bin/env bash
# PreToolUse (Edit|Write|NotebookEdit): files no tool edits by hand —
#  - tests/data/real/** — the owner's real data, read only (CLAUDE.md «Структура», Приложение А)
#  - core/uv.lock, package-lock.json — only through uv / npm by ADR 0001 (exact versions, exclude-newer, --before)
command -v jq >/dev/null 2>&1 || exit 0
path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)
[ -z "$path" ] && exit 0
case "$path" in
  */tests/data/real/*|tests/data/real/*)
    echo "Заблокировано хуком guard-paths: tests/data/real/ — данные заказчика, только чтение." >&2; exit 2 ;;
  */core/uv.lock|core/uv.lock|*/package-lock.json|package-lock.json)
    echo "Заблокировано хуком guard-paths: lock-файл меняется только через uv / npm по ADR 0001 (новая зависимость — через ADR)." >&2; exit 2 ;;
esac
exit 0
