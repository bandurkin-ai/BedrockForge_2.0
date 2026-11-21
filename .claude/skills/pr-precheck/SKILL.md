---
name: pr-precheck
description: Полный локальный прогон BF2 перед push в открытый PR (ruff, mypy, selftest, pytest, PostgreSQL на временном кластере, vitest, typecheck, build, лицензии, bench ci, все E2E под xvfb). Возвращает сводку «шаг — итог». Использовать перед каждым push в не-черновой PR.
argument-hint: "[--skip e2e,bench,pg,...]"
context: fork
agent: test-runner
---

Запусти полный локальный прогон и верни только сводку.

1. `bash "${CLAUDE_PROJECT_DIR}/.claude/skills/pr-precheck/precheck.sh" $ARGUMENTS` — долго (≈ 45–60 мин: pytest ~30,
   E2E ~13, bench ~7). Запускай в фоне и дождись завершения; шаги идут последовательно.
2. Для каждого шага со статусом «УПАЛ» — открой его журнал (путь в сводке) и выпиши имена упавших тестов
   (pytest — `FAILED …`, Playwright — `N) файл.spec.ts:строка …`) и по 3 строки ошибки.
3. Ничего не исправляй и не коммить.

Ответ ≤ 15 строк: сводка скрипта как есть, затем упавшее с ошибками, затем строка «ИТОГ».
