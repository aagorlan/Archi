# Анализ архитектурных схем

| Документ | Содержание |
|---|---|
| [01-Реестр-схем-по-состояниям.md](01-Реестр-схем-по-состояниям.md) | Все 32 схемы, разделённые на текущее (AS-IS), целевое (TO-BE), переходное и справочные |
| [02-Реестр-информационных-систем.md](02-Реестр-информационных-систем.md) | Сквозное отождествление ИС: одна система — все её имена и все схемы, где она есть |
| [03-Правила-оптимизации-схем.md](03-Правила-оптимизации-схем.md) | Зафиксированный регламент оптимизации раскладки |
| [04-Базовые-метрики.txt](04-Базовые-метрики.txt) | Замер читаемости всех схем на исходном состоянии |
| [05-Правило-PDF-представлений.md](05-Правило-PDF-представлений.md) | Регламент: каждое представление имеет актуальный PDF в папке проекта |

Инструмент замера: [`tools/archi_metrics.py`](../tools/archi_metrics.py).

```bash
python3 tools/archi_metrics.py                                        # все модели
python3 tools/archi_metrics.py "Склады/WMS.archimate"                 # одна модель
python3 tools/archi_metrics.py "Склады/WMS.archimate" --view "Целевая архитектура"
```

Выгрузка представлений в PDF силами самого Archi:

```bash
tools/archi_export_views.sh     # Archi + плагин jArchi: векторный PDF с текстом
tools/archi_export_report.sh    # Archi без плагина: картинка из HTML-отчёта Archi
```

Скрипты: [`tools/archi_export_views.sh`](../tools/archi_export_views.sh) с
[`tools/export_views.js`](../tools/export_views.js) и
[`tools/archi_export_report.sh`](../tools/archi_export_report.sh) с
[`tools/archi_report_pdf.py`](../tools/archi_report_pdf.py).

Резервный рендер без Archi и без внешних зависимостей:
[`tools/archi_export_pdf.py`](../tools/archi_export_pdf.py)
(рисование — [`tools/minipdf.py`](../tools/minipdf.py)).

```bash
python3 tools/archi_export_pdf.py                                     # все представления
python3 tools/archi_export_pdf.py "Склады/WMS.archimate"              # одна модель
python3 tools/archi_export_pdf.py --check                             # проверка актуальности
python3 tools/archi_export_pdf.py --adopt                             # зафиксировать выгрузку Archi
```

На сервере то же самое делает CI:
[`.github/workflows/pdf-representations.yml`](../.github/workflows/pdf-representations.yml)
ставит на раннер Archi с jArchi, выгружает представления и коммитит их в ветку.

PDF-представления лежат в папках проектов под именем `<проект>_<имя представления>.pdf`
и обновляются вместе со схемой — см. [правило](05-Правило-PDF-представлений.md).
