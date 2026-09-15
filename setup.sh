#!/usr/bin/env bash
# Установка переехала в install.py (Linux, macOS, Windows); файл живёт ради старой ссылки из README.
set -eu
d=$(dirname "$0"); [ -f "$d/install.py" ] || { d=$(mktemp -d); curl -fsSL https://raw.githubusercontent.com/nowherewashere/study-digest/master/install.py -o "$d/install.py"; }
python3 "$d/install.py"
