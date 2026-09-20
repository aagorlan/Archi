#!/bin/sh
# Выгрузка всех представлений всех моделей репозитория в PDF силами самого Archi.
#
# Требуется:
#   - установленный Archi 5.x;
#   - плагин jArchi 1.7+ (Help → Install jArchi / архив с archimatetool.com);
#   - Linux: X-сервер или xvfb (скрипт сам подставит xvfb-run, если нет DISPLAY).
#
# Запуск из корня репозитория:
#   tools/archi_export_views.sh                     # Archi ищется в типовых местах
#   ARCHI=/opt/Archi/Archi tools/archi_export_views.sh
#
# Windows (PowerShell), по одной модели:
#   & "C:\Program Files\Archi\Archi.exe" -application com.archimatetool.commandline.app `
#       -consoleLog -nosplash --loadModel "CRM\CRM.archimate" `
#       --script.runScript "tools\export_views.js"
#   python3 tools\archi_export_pdf.py --adopt
#
# Правило: docs/05-Правило-PDF-представлений.md
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if [ -z "$ARCHI" ]; then
    for candidate in \
        "$(command -v Archi 2>/dev/null || true)" \
        "/Applications/Archi.app/Contents/MacOS/Archi" \
        "/opt/Archi/Archi" \
        "$HOME/Archi/Archi" \
        "$HOME/opt/Archi/Archi"
    do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            ARCHI="$candidate"
            break
        fi
    done
fi

if [ -z "$ARCHI" ]; then
    echo "Не найден Archi. Укажите путь: ARCHI=/путь/к/Archi tools/archi_export_views.sh" >&2
    exit 2
fi

RUNNER=""
if [ -z "$DISPLAY" ] && command -v xvfb-run >/dev/null 2>&1; then
    RUNNER="xvfb-run -a"          # Archi рисует через SWT, ему нужен дисплей
fi

echo "Archi: $ARCHI"

# Журнал: скрипт jArchi пишет в него каждый созданный PDF. По журналу в манифесте
# отмечаются ровно те файлы, которые действительно выгрузил Archi.
ARCHI_EXPORT_LOG=$(mktemp)
export ARCHI_EXPORT_LOG
trap 'rm -f "$ARCHI_EXPORT_LOG"' EXIT

find . -name '*.archimate' -not -path './.git/*' | sort | while read -r model; do
    echo "== $model"
    # shellcheck disable=SC2086
    $RUNNER "$ARCHI" -application com.archimatetool.commandline.app -consoleLog -nosplash \
        --loadModel "$model" --script.runScript "$ROOT/tools/export_views.js"
done

exported=$(grep -c . "$ARCHI_EXPORT_LOG" 2>/dev/null || echo 0)
if [ "$exported" -eq 0 ]; then
    echo "Archi не выгрузил ни одного представления — проверьте плагин jArchi 1.7+." >&2
    exit 3
fi

echo
echo "Выгружено представлений: $exported. Фиксация в манифесте..."
python3 tools/archi_export_pdf.py --adopt --renderer archi --only-listed "$ARCHI_EXPORT_LOG"
