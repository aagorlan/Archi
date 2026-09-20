#!/bin/sh
# Выгрузка представлений картинками самого Archi — без плагина jArchi.
#
# Archi собирает HTML-отчёт из командной строки, в отчёте лежат картинки всех
# представлений, нарисованные редактором. Скрипт раскладывает их по правилу
# репозитория: <проект>_<имя представления>.pdf рядом с моделью.
#
# Требуется установленный Archi 5.x; на Linux без графической сессии — xvfb.
#
#   tools/archi_export_report.sh
#   ARCHI=/opt/Archi/Archi tools/archi_export_report.sh
#
# Плата за отсутствие jArchi: PDF получается растровым (текст не выделяется).
# Векторный PDF с текстом даёт tools/archi_export_views.sh.
# Правило: docs/05-Правило-PDF-представлений.md
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if [ -z "$ARCHI" ]; then
    for candidate in \
        "$(command -v Archi 2>/dev/null || true)" \
        "/Applications/Archi.app/Contents/MacOS/Archi" \
        "/opt/Archi/Archi" \
        "$HOME/Archi/Archi"
    do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            ARCHI="$candidate"
            break
        fi
    done
fi

if [ -z "$ARCHI" ]; then
    echo "Не найден Archi. Укажите путь: ARCHI=/путь/к/Archi tools/archi_export_report.sh" >&2
    exit 2
fi

RUNNER=""
if [ -z "$DISPLAY" ] && command -v xvfb-run >/dev/null 2>&1; then
    RUNNER="xvfb-run -a"          # Archi рисует через SWT, ему нужен дисплей
fi

ARCHI_EXPORT_LOG=$(mktemp)
export ARCHI_EXPORT_LOG
WORK=$(mktemp -d)
trap 'rm -rf "$WORK" "$ARCHI_EXPORT_LOG"' EXIT

echo "Archi: $ARCHI"

find . -name '*.archimate' -not -path './.git/*' | sort | while read -r model; do
    echo "== $model"
    report="$WORK/$(echo "$model" | tr '/ ' '__')"
    mkdir -p "$report"
    # shellcheck disable=SC2086
    $RUNNER "$ARCHI" -application com.archimatetool.commandline.app -consoleLog -nosplash \
        --loadModel "$model" --html.createReport "$report"
    python3 tools/archi_report_pdf.py "$model" "$report" || true
done

exported=$(grep -c . "$ARCHI_EXPORT_LOG" 2>/dev/null || echo 0)
if [ "$exported" -eq 0 ]; then
    echo "Из отчётов Archi не получено ни одного представления." >&2
    exit 3
fi

echo
echo "Выгружено представлений: $exported. Фиксация в манифесте..."
python3 tools/archi_export_pdf.py --adopt --renderer archi-report --only-listed "$ARCHI_EXPORT_LOG"
