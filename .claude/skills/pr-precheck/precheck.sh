#!/usr/bin/env bash
# Full local run before a push into an open PR (CLAUDE.md «Бюджет Actions»). Steps run one after another (the
# timings of E2E and bench do not share the processor with pytest); every step goes on, a summary at the end.
# Usage: precheck.sh [--skip e2e,bench,pg,...]   Logs: $LOG_DIR (default: a temporary folder, printed).
set -u
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"
SKIP=",${2:-},"
[ "${1:-}" = "--skip" ] || SKIP=",,"
LOG_DIR="${LOG_DIR:-$(mktemp -d -t bf-precheck-XXXX)}"
export UV_SYSTEM_CERTS=1
unset UV_NATIVE_TLS
declare -a SUMMARY=()
FAILED=0

step() { # name, command...
  local name="$1"; shift
  if [[ "$SKIP" == *",$name,"* ]]; then SUMMARY+=("$name: пропущен"); return; fi
  local log="$LOG_DIR/$name.log" t0=$SECONDS
  ( "$@" ) >"$log" 2>&1
  local rc=$? dt=$((SECONDS - t0))
  local tail; tail=$(grep -E "passed|failed|error|Error|✘|OVER BUDGET|Tests +[0-9]" "$log" | tail -n 2 | tr '\n' ' ')
  if [ $rc -eq 0 ]; then SUMMARY+=("$name: ок (${dt} с) ${tail}"); else SUMMARY+=("$name: УПАЛ rc=$rc (${dt} с) ${tail} — $log"); FAILED=1; fi
}

pg_db() { # a temporary PostgreSQL cluster for `pytest -m db`; initdb does not run as root
  local bin; bin=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -n 1)
  [ -z "$bin" ] && { echo "postgres не найден"; return 3; }
  local user="$(id -un)" dir port=54329
  if [ "$(id -u)" = 0 ]; then user=$(getent passwd 1000 | cut -d: -f1); user=${user:-nobody}; fi
  dir=$(mktemp -d /tmp/bf-pg-XXXX); chown "$user" "$dir" 2>/dev/null
  as() { if [ "$(id -u)" = 0 ]; then runuser -u "$user" -- "$@"; else "$@"; fi; }
  as "$bin/initdb" -D "$dir/data" -A trust -U bf -E UTF8 --locale=C.UTF-8 >/dev/null || return 4
  as "$bin/pg_ctl" -D "$dir/data" -o "-p $port -k $dir -c listen_addresses=127.0.0.1" -l "$dir/log" -w start >/dev/null || return 5
  as "$bin/createdb" -h 127.0.0.1 -p $port -U bf bf_test
  (cd "$ROOT/core" && BF_TEST_PG="postgresql+pg8000://bf@127.0.0.1:$port/bf_test" uv run pytest -q -m db --tb=short -p no:cacheprovider)
  local rc=$?
  as "$bin/pg_ctl" -D "$dir/data" -m fast stop >/dev/null; rm -rf "$dir"
  return $rc
}

cd "$ROOT" || exit 1
step lint      bash -c 'cd core && uv run ruff check . ../bench ../tools && uv run mypy bf_core'
step selftest  bash -c 'cd core && uv run python -m bf_core --selftest'
step pytest    bash -c 'cd core && uv run pytest -q --tb=short -p no:cacheprovider'
step pg        pg_db
step npm       bash -c 'npm test -- --reporter=dot && npm run typecheck && npm run build'
step licenses  bash -c 'uv run --project core python tools/ci/check_licenses_py.py && node tools/ci/check_licenses_npm.mjs'
step bench     bash -c 'uv run --project core python -m bench.run --profile ci --check'
step e2e       bash -c 'cd app && xvfb-run -a npx playwright test --trace off'

echo "== Полный прогон ($LOG_DIR)"
printf '%s\n' "${SUMMARY[@]}"
[ $FAILED -eq 0 ] && echo "ИТОГ: зелёный — можно пушить в PR пачкой" || echo "ИТОГ: красный — не пушить"
exit $FAILED
