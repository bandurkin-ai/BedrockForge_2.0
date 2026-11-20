#!/usr/bin/env bash
# PostToolUse (Edit|Write): checks of the edited file only.
#  - *.py: `ruff check` without fixes (an import added before its use must survive the next edit), unused names and the order
#    of imports (F401, F841, I001) are left to the full check; what it finds goes back to Claude
#  - any source file: more than 600 lines — a note (CLAUDE.md: a file ≤ ~600 lines)
# Exit 2 on PostToolUse does not undo the edit: stderr is shown to Claude as feedback.
command -v jq >/dev/null 2>&1 || exit 0
path=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$path" ] || [ ! -f "$path" ] && exit 0
root="${CLAUDE_PROJECT_DIR:-.}"
msg=""
case "$path" in
  *.py)
    if command -v uv >/dev/null 2>&1; then
      out=$(cd "$root/core" && env -u UV_NATIVE_TLS uv run --frozen --no-sync ruff check --quiet --output-format concise --extend-ignore F401,F841,I001 "$path" 2>&1)
      [ $? -ne 0 ] && [ -n "$out" ] && msg="ruff: $out"
    fi ;;
esac
case "$path" in
  *.py|*.ts|*.tsx|*.mjs|*.js)
    n=$(wc -l < "$path")
    [ "$n" -gt 600 ] && msg="${msg:+$msg
}Файл $path — $n строк (> 600): вынесите часть в отдельный модуль (CLAUDE.md)." ;;
esac
[ -n "$msg" ] && { echo "$msg" >&2; exit 2; }
exit 0
