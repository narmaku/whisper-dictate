#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v uv >/dev/null; then
  [[ -x .venv/bin/python ]] || uv venv --python python3 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
else
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -c 'from PySide6.QtWidgets import QApplication; import sounddevice, faster_whisper'
echo 'GUI and Python dependencies ready. No keyboard-injection service is needed.'
if [[ ${XDG_SESSION_TYPE:-} == wayland ]] && ! command -v wl-copy >/dev/null; then
  echo 'Clipboard tool unavailable; use the GUI to select and copy the transcript manually.'
fi
echo 'Run: .venv/bin/python dictate.py'
