#!/usr/bin/env bash
# Установка и первоначальная настройка study: код, токены, каталоги, проверка связи.
#
# Из каталога с инструментом:  bash setup.sh
# Отдельно, одной командой:    см. раздел «Установка» в README.
set -euo pipefail

REPO=${STUDY_REPO:-https://github.com/nowherewashere/study-digest.git}
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
warn() { printf '  ! %s\n' "$1"; }
ok() { printf '  + %s\n' "$1"; }

# Тильда в пути, введённом руками или заданном настройкой.
untilde() {
  # shellcheck disable=SC2088  # это образец для case, а не путь
  case $1 in "~/"*) printf '%s' "$HOME/${1#\~/}" ;; *) printf '%s' "$1" ;; esac
}

# Значение настройки: переменная окружения → строка в config.env → значение по умолчанию.
cfg() {
  local key=$1 default=${2:-} value=${!1:-}
  if [ -z "$value" ] && [ -f "$CONFIG" ]; then
    value=$(sed -n "s/^$key=//p" "$CONFIG" | tail -1 | tr -d '[:space:]')
  fi
  printf '%s' "${value:-$default}"
}

# Путь из настройки: относительный отсчитывается от каталога digest/ (как в config.py).
expand() {
  local path
  path=$(untilde "$1")
  case $path in /*) printf '%s' "$path" ;; *) printf '%s' "$DIGEST/$path" ;; esac
}

# Записать KEY=значение в config.env (заменить существующую строку или добавить), права 600.
set_cfg() {
  local key=$1 value=$2 tmp
  tmp=$(mktemp)
  grep -vE "^$key=" "$CONFIG" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$CONFIG"
}

# Открыть ссылку в браузере (WSL/Linux), не роняя установку.
open_url() {
  local url=$1
  if command -v wslview >/dev/null 2>&1; then wslview "$url" >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 &
  elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$url" >/dev/null 2>&1 &
  else return 1; fi
}

# --- 1. Зависимости

bold "Зависимости"
command -v git >/dev/null || { warn "git не найден"; exit 1; }
ok "git $(git --version | awk '{print $3}')"

command -v python3 >/dev/null || { warn "python3 не найден"; exit 1; }
python3 - <<'PYVER' || { warn "нужен python3 3.8 или новее"; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 8) else 1)
PYVER
ok "python3 $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

# --- 2. Код

bold $'\nКод'
if [ -x "$HERE/study" ]; then
  DIGEST=$HERE
  ok "уже на месте: $DIGEST"
else
  # Скрипт скачали отдельно — спрашиваем, куда ставить, и проверяем путь.
  default=$HOME/study/.digest
  target=$default
  while [ -t 0 ]; do
    read -r -p "  куда установить [$default]: " target
    target=$(untilde "${target:-$default}")
    case $target in /*) ;; *) target=$PWD/$target ;; esac    # относительный — от текущего каталога
    case $target in *=*) warn "в пути нельзя '=' (ломает libvirt/virtiofsd)"; continue ;; esac
    [ "${#target}" -le 100 ] || { warn "слишком длинный путь (лимит unix-сокетов Packer ~108 байт)"; continue; }
    if [ -x "$target/study" ]; then break; fi                # уже установлено — берём как есть
    if [ -e "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
      warn "$target существует и не пуст — выбери другой"; continue
    fi
    mkdir -p "$(dirname "$target")" 2>/dev/null || { warn "нет доступа к $(dirname "$target")"; continue; }
    break
  done
  if [ -x "$target/study" ]; then
    ok "уже установлено: $target"
  else
    git clone --quiet "$REPO" "$target" || { warn "git clone не удался: $REPO"; exit 1; }
    ok "склонировано в $target"
  fi
  DIGEST=$target
fi
ROOT=$(dirname "$DIGEST")
CONFIG=$DIGEST/config.env
if [ ! -f "$CONFIG" ]; then
  cp "$DIGEST/config.env.example" "$CONFIG"
  chmod 600 "$CONFIG"
  ok "создан config.env из примера (права 600 — в нём хранятся токены)"
fi
chmod 600 "$CONFIG" 2>/dev/null || true
ok "корень учебной директории: $ROOT"

# --- 3. Токены

ask_token() {
  local key=$1 name=$2 where=$3 url=${4:-} token answer
  printf '\n%s\n' "$name"
  printf '  где взять: %s\n' "$where"
  if [ -n "$url" ]; then
    printf '  ссылка: %s\n' "$url"
    read -r -p "  открыть в браузере? [Y/n] " answer
    case ${answer:-y} in [yY]*) open_url "$url" && ok "открываю в браузере" || warn "не открылось — перейди по ссылке вручную" ;; esac
  fi

  if [ -n "$(cfg "$key")" ]; then
    read -r -p "  токен уже есть, заменить? [y/N] " answer
    case ${answer:-n} in [yY]*) ;; *) ok "оставлен прежний"; return 0 ;; esac
  fi

  read -r -s -p "  вставь токен (ввод не отображается, Enter — пропустить): " token
  printf '\n'
  if [ -z "$token" ]; then
    warn "пропущено, команды этого сервиса работать не будут"
    return 0
  fi

  set_cfg "$key" "$token"
  ok "сохранён в config.env (права $(stat -c '%a' "$CONFIG"))"
}

bold $'\nТокены'
if [ ! -t 0 ]; then
  warn "нет терминала, ввод токенов пропущен"
else
  ask_token TUIS_TOKEN "Moodle (нужен для сводки)" \
    "профиль → «Ключи безопасности» → служба Moodle mobile web service" \
    "$(cfg TUIS_URL https://esystem.rudn.ru)/user/managetoken.php"
  ask_token GITVERSE_TOKEN "GitVerse (необязательно)" \
    "иконка пользователя → Настройки → Управление токенами, доступ «Репозитории»"
  ask_token SOURCECRAFT_TOKEN "SourceCraft (необязательно)" \
    "Home → Access → Personal Access Tokens"
fi

# --- 4. Каталоги

bold $'\nКаталоги'
state=$(expand "$(cfg DIGEST_STATE ".state.json")")
mkdir -p "$(dirname "$state")"
ok "$(dirname "$state") — снимок состояния сводки"

courses=0
while read -r _ _ code; do
  case ${code:-} in "") continue ;; esac
  mkdir -p "$ROOT/$code/stash" "$ROOT/$code/tuis"
  ok "$ROOT/$code/{stash,tuis}"
  courses=$((courses + 1))
done < <(grep -E '^CODE[[:space:]]+[0-9]+' "$CONFIG" 2>/dev/null || true)
[ "$courses" -gt 0 ] || warn "в config.env нет строк CODE (папки курсов задаст шаг «Курсы»)"

# --- 5. Проверка

bold $'\nПроверка'
if "$DIGEST/study" me 2>/dev/null; then
  ok "токен Moodle работает"
else
  warn "Moodle не отвечает — проверь токен и TUIS_URL в config.env"
fi

# Команда в PATH: ~/.local/bin есть в PATH у Ubuntu по умолчанию.
mkdir -p "$HOME/.local/bin"
ln -sfn "$DIGEST/study" "$HOME/.local/bin/study"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "добавь ~/.local/bin в PATH или перезайди в оболочку" ;; esac

# --- 6. ИИ-оператор

bold $'\nИИ-оператор'
echo "  Инструкция для агента (.digest/docs/AGENTS.md) кладётся блоком в файл оператора"
echo "  в корне учебной директории; всё вне блока — личные правила, они не трогаются."
if [ ! -t 0 ]; then
  warn "нет терминала, шаг пропущен — позже: study agent <код>"
else
  "$DIGEST/study" agent | sed 's/^/  /'
  read -r -p "  какие поставить (коды через пробел, Enter - пропустить): " operators
  if [ -n "$operators" ]; then
    # shellcheck disable=SC2086
    "$DIGEST/study" agent $operators | sed 's/^/  /' || warn "не удалось — проверь коды: study agent"
  fi
fi

# --- 7. Курсы

bold $'\nКурсы'
if [ ! -t 0 ]; then
  warn "нет терминала, шаг курсов пропущен — позже: study courses --setup"
elif "$DIGEST/study" courses --setup; then
  # папки курсов уже созданы командой выше; докрутим права config.env
  chmod 600 "$CONFIG" 2>/dev/null || true
  # сводка тянет только новое с прошлого запуска, поэтому первое наполнение — отдельно
  read -r -p "  скачать материалы всех курсов в stash/ сейчас? [Y/n] " answer
  case ${answer:-y} in [yY]*) "$DIGEST/study" files --pull | sed 's/^/  /' || warn "не всё скачалось — позже: study files --pull" ;; esac
else
  warn "не удалось (нет токена Moodle?) — позже: study courses --setup"
fi

bold $'\nДальше'
cat <<NEXT
  study courses            список курсов и текущий COURSE_IGNORE
  study courses --setup    перенастроить: какие курсы игнорировать и папки
  study agent <код>        файл инструкций для ИИ-оператора (claude, codex, gemini, copilot)
  study digest             первый запуск сохраняет снимок состояния
  study files --pull       материалы всех курсов в stash/ (в пустую папку — всё, дальше — новое)
  study files <код> --pull то же для одного курса, с подробным списком

Ежедневная сводка: Claude Code Desktop → Code → Routines → New routine → Local,
рабочая папка $ROOT, в Instructions — текст из $DIGEST/docs/daily-digest-prompt.md.
NEXT
