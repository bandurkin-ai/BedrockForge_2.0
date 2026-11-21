#!/usr/bin/env bash
# PreToolUse (Bash): refuses git commands that broke shared work before (ADR-free project rule, CLAUDE.md «Git»):
#  - git stash / git add -A|.|--all — several agents share one tree; a stash removed their edits for seconds
#  - git push --force, git rebase — no history rewriting on shared branches
#  - git push of only *.md files while the PR of the branch is open and not a draft (PROGRESS.md «PR: #N (открыт …,
#    не черновик)»): `paths-ignore` of a pull_request event looks at the whole diff of the PR, so such a push still
#    starts `ci` and `windows`. Override for one push: BF_ALLOW_MD_PUSH=1 in the command.
# Exit 2 blocks the call; stderr is the reason Claude reads. No jq — no opinion (exit 0).
command -v jq >/dev/null 2>&1 || exit 0
cmd=$(jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$cmd" ] && exit 0

block() { echo "Заблокировано хуком guard-bash: $1" >&2; exit 2; }

# split on ; && || | and newlines, look at each git invocation
while IFS= read -r part; do
  part="${part#"${part%%[![:space:]]*}"}"
  case "$part" in
    git\ stash*|*" git stash"*) block "git stash трогает правки всех агентов в общем дереве. Коммитьте свои пути: git add <пути>; git commit -- <пути>." ;;
    git\ add\ -A*|git\ add\ --all*|git\ add\ .|git\ add\ .\ *) block "git add -A/./--all захватывает чужие файлы. Указывайте свои пути явно." ;;
    git\ rebase*) block "rebase переписывает историю общей ветки. Используйте merge (CLAUDE.md «Git»)." ;;
    git\ push*--force*|git\ push*\ -f|git\ push*\ -f\ *) block "принудительный push запрещён (Приложение А, 16.6)." ;;
  esac
done < <(printf '%s\n' "$cmd" | sed -E 's/(&&|\|\||;|\|)/\n/g')

if printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git push' && ! printf '%s' "$cmd" | grep -q 'BF_ALLOW_MD_PUSH=1'; then
  root="${CLAUDE_PROJECT_DIR:-.}"
  if grep -Eq 'PR: #[0-9]+ \(открыт[^)]*не черновик\)' "$root/PROGRESS.md" 2>/dev/null; then
    files=$(git -C "$root" diff --name-only '@{u}..HEAD' 2>/dev/null)
    if [ -n "$files" ] && ! printf '%s\n' "$files" | grep -vq '\.md$'; then
      block "в открытый PR уходят только *.md — push всё равно запустит ci и windows (paths-ignore смотрит на весь diff PR). Отложите до пачки с кодом после локального прогона или добавьте BF_ALLOW_MD_PUSH=1 в команду."
    fi
  fi
fi
exit 0
