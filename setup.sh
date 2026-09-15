#!/usr/bin/env bash
# Установка и первоначальная настройка study: код, токены, каталоги, проверка связи.
#
# Из каталога с инструментом:  bash setup.sh
# Отдельно, одной командой:    см. раздел «Установка» в README.
#
# Каждый шаг — свой экран: [n/7] в заголовке, строка прогресса, снизу — результат.
# Всё, что шаги сообщили, собирается и показывается ещё раз на итоговом экране.
set -euo pipefail

REPO=${STUDY_REPO:-https://github.com/nowherewashere/study-digest.git}
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
STEPS=("Зависимости" "Код" "Токены" "Каталоги" "Проверка" "Оператор" "Курсы")
LOG=()
STEP=0

# --- оформление: цвета только в терминале, без них — тот же текст

if [ -t 1 ] && command -v tput >/dev/null 2>&1 && tput setaf 1 >/dev/null 2>&1; then
  B=$(tput bold) D=$(tput dim) G=$(tput setaf 2) Y=$(tput setaf 3) R=$(tput setaf 1) N=$(tput sgr0)
else
  B='' D='' G='' Y='' R='' N=''
fi
RULE=------------------------------------------------------------------------

step() {
  STEP=$1
  [ -t 1 ] && printf '\033[H\033[2J'
  local i crumbs=""
  for i in "${!STEPS[@]}"; do
    if [ $((i + 1)) -lt "$STEP" ]; then crumbs+="${G}[x]${N} ${D}${STEPS[i]}${N}  "
    elif [ $((i + 1)) -eq "$STEP" ]; then crumbs+="${B}[>] ${STEPS[i]}${N}  "
    else crumbs+="${D}[ ] ${STEPS[i]}${N}  "; fi
  done
  printf '%sstudy%s  установка  %s[%d/%d] %s%s\n%s\n%s\n\n' \
    "$B" "$N" "$B" "$STEP" "${#STEPS[@]}" "${STEPS[STEP - 1]}" "$N" "${crumbs%  }" "$RULE"
}

ok()   { printf '  %s+%s %s\n' "$G" "$N" "$1"; LOG+=("${G}+${N} ${STEPS[STEP - 1]}: $1"); }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; LOG+=("${Y}!${N} ${STEPS[STEP - 1]}: $1"); }
fail() { printf '  %s!%s %s\n' "$R" "$N" "$1"; exit 1; }
note() { printf '  %s%s%s\n' "$D" "$1" "$N"; }
ask()  { local _v; read -r -p "  ${B}>${N} $1 " _v; printf '%s' "$_v"; }

# Тильда в пути, введённом руками или заданном настройкой.
untilde() {
  # shellcheck disable=SC2088  # это образец для case, а не путь
  case $1 in "~/"*) printf '%s' "$HOME/${1#\~/}" ;; *) printf '%s' "$1" ;; esac
}

# Значение настройки: переменная окружения -> строка в config.env -> значение по умолчанию.
cfg() {
  local key=$1 default=${2:-} value=${!1:-}
  if [ -z "$value" ] && [ -f "$CONFIG" ]; then
    value=$(sed -n "s/^$key=//p" "$CONFIG" | tail -1 | tr -d '[:space:]')
  fi
  printf '%s' "${value:-$default}"
}

# Путь из настройки: относительный отсчитывается от каталога .digest/ (как в config.py).
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

step 1
command -v git >/dev/null || fail "git не найден"
ok "git $(git --version | awk '{print $3}')"
command -v python3 >/dev/null || fail "python3 не найден"
python3 - <<'PYVER' || fail "нужен python3 3.8 или новее"
import sys
sys.exit(0 if sys.version_info >= (3, 8) else 1)
PYVER
ok "python3 $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

# --- 2. Код

step 2
if [ -f "$HERE/study" ] && [ -d "$HERE/src/study" ]; then
  DIGEST=$HERE
  ok "уже на месте: $DIGEST"
else
  # Скрипт скачали отдельно — спрашиваем, куда ставить, и проверяем путь.
  note "инструмент ставится в <учебная директория>/.digest; рядом появятся папки курсов"
  default=$HOME/study/.digest
  target=$default
  while [ -t 0 ]; do
    target=$(ask "куда установить [$default]:")
    target=$(untilde "${target:-$default}")
    case $target in /*) ;; *) target=$PWD/$target ;; esac    # относительный — от текущего каталога
    case $target in *=*) warn "в пути нельзя '=' (ломает libvirt/virtiofsd)"; continue ;; esac
    [ "${#target}" -le 100 ] || { warn "слишком длинный путь (лимит unix-сокетов Packer ~108 байт)"; continue; }
    if [ -f "$target/study" ] && [ -d "$target/src/study" ]; then break; fi                # уже установлено — берём как есть
    if [ -e "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
      warn "$target существует и не пуст — выбери другой"; continue
    fi
    mkdir -p "$(dirname "$target")" 2>/dev/null || { warn "нет доступа к $(dirname "$target")"; continue; }
    break
  done
  if [ -f "$target/study" ] && [ -d "$target/src/study" ]; then
    ok "уже установлено: $target"
  else
    git clone --quiet "$REPO" "$target" || fail "git clone не удался: $REPO"
    ok "склонировано в $target"
  fi
  DIGEST=$target
fi
ROOT=$(dirname "$DIGEST")
CONFIG=$DIGEST/config.env
if [ ! -f "$CONFIG" ]; then
  cp "$DIGEST/config.env.example" "$CONFIG"
  chmod 600 "$CONFIG"
  ok "создан config.env из примера (права 600: в нём хранятся токены)"
fi
chmod 600 "$CONFIG" 2>/dev/null || true
ok "учебная директория: $ROOT"

# --- 3. Токены

ask_token() {
  local key=$1 name=$2 where=$3 url=${4:-} token answer short=${2%% *}
  printf '\n  %s%s%s\n' "$B" "$name" "$N"
  note "где взять: $where"
  if [ -n "$url" ]; then
    note "ссылка: $url"
    answer=$(ask "открыть в браузере? [Y/n]")
    case ${answer:-y} in [yY]*) open_url "$url" || warn "не открылось: перейди по ссылке вручную" ;; esac
  fi
  if [ -n "$(cfg "$key")" ]; then
    answer=$(ask "токен уже есть, заменить? [y/N]")
    case ${answer:-n} in [yY]*) ;; *) ok "$short: оставлен прежний"; return 0 ;; esac
  fi
  read -r -s -p "  ${B}>${N} вставь токен (ввод скрыт, Enter - пропустить): " token
  printf '\n'
  if [ -z "$token" ]; then
    warn "$short: пропущен, команды этого сервиса работать не будут"
    return 0
  fi
  set_cfg "$key" "$token"
  ok "$short: сохранён в config.env"
}

step 3
note "все три хранятся в config.env (права 600, в git не входит); Enter - пропустить"
if [ ! -t 0 ]; then
  warn "нет терминала, ввод токенов пропущен"
else
  ask_token TUIS_TOKEN "Moodle (нужен для сводки)" \
    "профиль -> Ключи безопасности -> служба Moodle mobile web service" \
    "$(cfg TUIS_URL https://esystem.rudn.ru)/user/managetoken.php"
  ask_token GITVERSE_TOKEN "GitVerse (необязательно)" \
    "иконка пользователя -> Настройки -> Управление токенами, доступ Репозитории"
  ask_token SOURCECRAFT_TOKEN "SourceCraft (необязательно)" \
    "Home -> Access -> Personal Access Tokens"
fi

# --- 4. Каталоги

step 4
state=$(expand "$(cfg DIGEST_STATE ".state.json")")
mkdir -p "$(dirname "$state")"
ok "снимок состояния сводки: $(dirname "$state")"
courses=0
while read -r _ _ code; do
  case ${code:-} in "") continue ;; esac
  mkdir -p "$ROOT/$code/stash" "$ROOT/$code/tuis"
  ok "$ROOT/$code/{stash,tuis}"
  courses=$((courses + 1))
done < <(grep -E '^CODE[[:space:]]+[0-9]+' "$CONFIG" 2>/dev/null || true)
[ "$courses" -gt 0 ] || note "папки курсов появятся на шаге «Курсы»"

# --- 5. Проверка

step 5
if who=$("$DIGEST/study" me 2>/dev/null | sed 's/ *|.*//'); then
  ok "Moodle отвечает: $who"
else
  warn "Moodle не отвечает: проверь токен и TUIS_URL в config.env"
fi
# Команда в PATH: ~/.local/bin есть в PATH у Ubuntu по умолчанию.
mkdir -p "$HOME/.local/bin"
ln -sfn "$DIGEST/study" "$HOME/.local/bin/study"
ok "команда study: ~/.local/bin/study -> $DIGEST/study"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "добавь ~/.local/bin в PATH или перезайди в оболочку" ;; esac

# --- 6. ИИ-оператор

step 6
note "инструкция для агента (.digest/docs/AGENTS.md) кладётся блоком в файл оператора"
note "в корне учебной директории; всё вне блока - личные правила, они не трогаются"
echo
if [ ! -t 0 ]; then
  warn "нет терминала, шаг пропущен: позже study agent <код>"
else
  "$DIGEST/study" agent | sed 's/^/  /'
  echo
  operators=$(ask "какие поставить (коды через пробел, Enter - пропустить):")
  if [ -z "$operators" ]; then
    note "пропущено: позже study agent <код>"
  else
    # shellcheck disable=SC2086
    if "$DIGEST/study" agent $operators >/dev/null; then
      ok "поставлено: $operators"
    else
      warn "не удалось, проверь коды: study agent"
    fi
  fi
fi

# --- 7. Курсы

step 7
note "какие курсы не отслеживать и как зовутся их папки; потом - первое наполнение stash/"
if [ ! -t 0 ]; then
  warn "нет терминала, шаг пропущен: позже study courses --setup"
elif "$DIGEST/study" courses --setup; then
  chmod 600 "$CONFIG" 2>/dev/null || true
  ok "записаны в config.env: $(grep -c '^CODE ' "$CONFIG") папок"
  # сводка тянет только новое с прошлого запуска, поэтому первое наполнение - отдельно
  echo
  answer=$(ask "скачать материалы всех курсов в stash/ сейчас? [Y/n]")
  case ${answer:-y} in
    [yY]*) "$DIGEST/study" files --pull | sed 's/^/  /' && ok "материалы курсов в stash/" \
             || warn "не всё скачалось: позже study files --pull" ;;
    *) note "позже: study files --pull" ;;
  esac
else
  warn "не удалось (нет токена Moodle?): позже study courses --setup"
fi

# --- Итог

STEPS+=("Готово")
step 8
printf '  %s\n' ${LOG[@]+"${LOG[@]}"}
cat <<NEXT

  ${B}Дальше${N}
  study state --pull       сводка: сроки, тесты, баллы, новое в курсах
  study files --pull       материалы всех курсов в stash/ (в пустую папку - всё, дальше - новое)
  study courses --setup    перенастроить курсы и папки
  study agent <код>        файл инструкций для ИИ-оператора (claude, codex, gemini, copilot)
  study update             обновить инструмент

  ${B}Ежедневная сводка${N}
  Claude Code Desktop -> Code -> Routines -> New routine -> Local:
  рабочая папка $ROOT,
  в Instructions - текст из $DIGEST/docs/daily-digest-prompt.md
NEXT
