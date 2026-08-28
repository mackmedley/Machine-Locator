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

# --- pick a free port -----------------------------------------------------
for try in 5000 5050 8080 8000 8800; do
  if ./.venv/bin/python -c "
import socket, sys
s = socket.socket()
try:
    s.bind(('0.0.0.0', $try))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
    PORT=$try
    break
  fi
done

# --- start serving, then prove the iPad can actually reach it -------------
if [ "$(uname -s)" = "Darwin" ]; then
  echo "  ${YELLOW}If macOS asks whether Python can accept incoming network"
  echo "  connections, click Allow. The iPad cannot reach it otherwise.${OFF}"
  echo ""
fi

echo "  Starting up..."
./.venv/bin/mloc serve --host 0.0.0.0 --port "$PORT" &
SERVER_PID=$!

# Ctrl+C, or closing the window, should stop the server rather than orphan it.
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM HUP

# Wait for it to answer on the network address -- not on localhost, because
# binding correctly and being reachable through a firewall are different
# things, and only the second one matters to the iPad.
REACHABLE=0
for _ in $(seq 1 40); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo ""
    echo "${RED}  The app stopped while starting up.${OFF}"
    echo "  The messages above say why."
    echo ""
    read -r -p "  Press Return to close. " _
    exit 1
  fi
  if ./.venv/bin/python -m machine_locator.lan --verify "$PORT" >/dev/null 2>&1; then
    REACHABLE=1
    break
  fi
  sleep 0.5
done

clear 2>/dev/null || true
echo ""
echo "${BOLD}  Machine Locator -- on your iPad${OFF}"
echo "  -------------------------------"

if [ "$REACHABLE" -eq 1 ]; then
  ./.venv/bin/python -m machine_locator.lan "$PORT"
  echo "  ${GREEN}Checked: this computer is answering on that address.${OFF}"
  echo ""
  echo "  The first time, the iPad will ask you to pick a password."
  echo "  Choose one and it remembers you after that."
else
  ./.venv/bin/python -m machine_locator.lan --verify "$PORT" || true
  echo "  ${YELLOW}Once you have fixed that, reload the page on the iPad --"
  echo "  there is no need to restart this.${OFF}"
fi

echo ""
echo "  ${BOLD}Leave this window open.${OFF} Closing it stops the app."
echo ""

wait "$SERVER_PID"
