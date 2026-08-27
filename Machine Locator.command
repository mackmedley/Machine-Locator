#!/usr/bin/env bash
#
# Double-click this file to start Machine Locator.
#
# On a Mac: double-click it in Finder. The first run takes a minute while it
# sets itself up; after that it starts in a couple of seconds.
# On Linux: double-click and choose "Run in Terminal", or run ./Machine\ Locator.command
#
# It does not install anything outside this folder -- everything lives in a
# .venv directory right here, and deleting the folder removes it completely.

set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'

echo ""
echo "${BOLD}  Machine Locator${OFF}"
echo "  ---------------"
echo ""

# --- find a usable Python -------------------------------------------------
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "${RED}  Python 3.9 or newer isn't installed.${OFF}"
  echo ""
  echo "  Machine Locator needs it to run. It's a free download:"
  echo ""
  echo "      ${BOLD}https://www.python.org/downloads/${OFF}"
  echo ""
  echo "  Install it, then double-click this file again."
  echo ""
  if command -v open >/dev/null 2>&1; then
    read -r -p "  Open the download page now? [Y/n] " reply
    case "${reply:-y}" in [Yy]*|"") open "https://www.python.org/downloads/" ;; esac
  fi
  echo ""
  read -r -p "  Press Return to close. " _
  exit 1
fi

# --- set up the private environment on first run --------------------------
NEEDS_INSTALL=0
if [ ! -x ".venv/bin/mloc" ]; then
  NEEDS_INSTALL=1
fi

if [ "$NEEDS_INSTALL" -eq 1 ]; then
  echo "  ${YELLOW}First run -- setting things up. This takes a minute.${OFF}"
  echo ""
  if [ ! -d ".venv" ]; then
    echo "  Creating a private Python environment..."
    "$PYTHON" -m venv .venv
  fi
  echo "  Installing Machine Locator and what it needs..."
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  if ! ./.venv/bin/python -m pip install --quiet -e .; then
    echo ""
    echo "  ${RED}Setup failed.${OFF} This is almost always no internet connection."
    echo "  Check your connection and try again."
    echo ""
    read -r -p "  Press Return to close. " _
    exit 1
  fi
  echo "  ${GREEN}Done. That was a one-time step.${OFF}"
  echo ""
fi

# --- pick a free port -----------------------------------------------------
PORT=5000
for try in 5000 5050 8080 8000 8800 9000; do
  if ! ./.venv/bin/python -c "
import socket, sys
s = socket.socket()
try:
    s.bind(('127.0.0.1', $try))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
    continue
  fi
  PORT=$try
  break
done

echo "  Starting up..."
echo ""
exec ./.venv/bin/mloc app --port "$PORT"
