#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ is required"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || { echo "Install venv first:  sudo apt install python3-venv"; exit 1; }
fi
.venv/bin/pip install --no-index --find-links wheels -r requirements.txt
.venv/bin/python -m borna_pricing init
echo
echo "Done. Next steps:"
echo "  1) .venv/bin/python -m borna_pricing test-api"
echo "  2) run as a service: see README_fa.md (borna-pricing.service)"
