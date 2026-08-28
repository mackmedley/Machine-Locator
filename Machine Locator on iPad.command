#!/usr/bin/env bash
#
# Opens Machine Locator to your own Wi-Fi so you can use it on an iPad,
# iPhone, or any other device in the house.
#
# This computer does the work; the iPad just displays it. Both have to be on
# the same Wi-Fi, and this window has to stay open.
#
# On a Mac: RIGHT-CLICK this file and choose Open the first time.

set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
PORT=5000

echo ""
echo "${BOLD}  Machine Locator -- on your iPad${OFF}"
echo "  -------------------------------"
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
  echo "  It's a free download: ${BOLD}https://www.python.org/downloads/${OFF}"
  echo ""
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "  ${YELLOW}If a box just appeared asking to install \"command line"
    echo "  developer tools\", click Install, wait, then open this again.${OFF}"
    echo ""
  fi
  read -r -p "  Press Return to close. " _
  exit 1
fi

# --- set up on first run --------------------------------------------------
if [ ! -x ".venv/bin/mloc" ]; then
  if ! touch ".write-test" 2>/dev/null; then
    echo "${RED}  This folder is read-only, so it can't set itself up.${OFF}"
    echo ""
    echo "  It is probably still inside the downloaded .zip. Drag the folder"
    echo "  to your Desktop and open it from there."
    echo ""
    read -r -p "  Press Return to close. " _
    exit 1
  fi
  rm -f ".write-test"

  echo "  ${YELLOW}First run -- setting things up. This takes a minute.${OFF}"
  echo ""
  [ -d ".venv" ] || "$PYTHON" -m venv .venv
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  if ! ./.venv/bin/python -m pip install --quiet -e .; then
    echo ""
    echo "${RED}  Setup failed.${OFF} This is almost always no internet connection."
    echo ""
    read -r -p "  Press Return to close. " _
    exit 1
  fi
  echo "  ${GREEN}Done. That was a one-time step.${OFF}"
fi

# --- start serving, then prove the iPad can actually reach it -------------
# Port picking, the reachability check and the messages all live in
# machine_locator.lan, so this launcher and the Windows one behave the same
# and there is only one copy to get right.
if [ "$(uname -s)" = "Darwin" ]; then
  echo "  ${YELLOW}If macOS asks whether Python can accept incoming network"
  echo "  connections, click Allow. The iPad cannot reach it otherwise.${OFF}"
  echo ""
fi

exec ./.venv/bin/python -m machine_locator.lan --serve
