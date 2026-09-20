#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF-представления из HTML-отчёта Archi (путь без плагина jArchi).

Archi умеет собирать HTML-отчёт из командной строки:

    Archi -application com.archimatetool.commandline.app -consoleLog -nosplash \\
          --loadModel "<модель>.archimate" --html.createReport <каталог>

В отчёте лежат картинки всех представлений, нарисованные самим Archi, — то есть
вид ровно как в редакторе. Этот скрипт раскладывает их по правилу репозитория:
каждая картинка становится отдельным PDF `<проект>_<имя представления>.pdf`
рядом с моделью.

    python3 tools/archi_report_pdf.py "Склады/WMS.archimate" /tmp/отчёт

Картинка растровая, поэтому текст в таком PDF не выделяется — это плата за то,
что рисует сам Archi. Векторный PDF с текстом даёт путь через jArchi
(tools/archi_export_views.sh), см. docs/05-Правило-PDF-представлений.md.
"""
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minipdf import PDFDocument                                  # noqa: E402
from archi_export_pdf import Model, pdf_path, project_of, rel    # noqa: E402

PT_PER_PX = 0.75               # картинки отчёта — 96 dpi, PDF считает в пунктах


def png_size(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('не PNG')
    return struct.unpack('>II', data[16:24])


def find_view_image(report_dir, view_id):
    """Картинка представления в отчёте: сначала по id, потом по ссылке в HTML."""
    for name in ('%s.png' % view_id, '%s.jpg' % view_id):
        candidate = os.path.join(report_dir, 'images', name)
        if os.path.exists(candidate):
            return candidate

    for root, _dirs, names in os.walk(report_dir):
        for name in names:
            if view_id in name and name.lower().endswith('.png'):
                return os.path.join(root, name)

    # id встречается в html-странице представления — берём картинку оттуда
    for root, _dirs, names in os.walk(report_dir):
        for name in names:
            if not name.lower().endswith(('.html', '.htm')):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding='utf-8', errors='ignore') as fh:
                    text = fh.read()
            except IOError:
                continue
            if view_id not in text:
                continue
            for src in re.findall(r'<img[^>]+src="([^"]+)"', text, re.I):
                image = os.path.normpath(os.path.join(root, src))
                if os.path.exists(image) and image.lower().endswith(('.png', '.jpg')):
                    return image
    return None


def build_pdf(image_path, target, title, subject):
    with open(image_path, 'rb') as fh:
        data = fh.read()
    width, height = png_size(data)
    doc = PDFDocument(title=title, subject=subject)
    name = doc.png_image(data, os.path.basename(image_path))
    page = doc.add_page(width * PT_PER_PX, height * PT_PER_PX)
    page.image(name, 0, 0, page.width, page.height)
    doc.save(target)


def export(model_path, report_dir, log_path=None, quiet=False):
    model = Model(model_path)
    project = project_of(model_path)
    written, missing = [], []

    for view in model.views():
        name = view.get('name') or 'View'
        image = find_view_image(report_dir, view.get('id') or '')
        if not image:
            missing.append(name)
            continue
        target = pdf_path(model_path, name)
        build_pdf(image, target,
                  title='%s — %s' % (project, name),
                  subject='Представление модели Archi «%s» (картинка отчёта Archi)'
                          % model.name)
        written.append(target)
        if not quiet:
            print('+ %s' % rel(target))

    if log_path:
        with open(log_path, 'a', encoding='utf-8') as fh:
            for path in written:
                fh.write(os.path.abspath(path) + '\n')

    for name in missing:
        print('! в отчёте нет картинки представления «%s»' % name, file=sys.stderr)
    return written, missing


def main():
    ap = argparse.ArgumentParser(
        description='PDF-представления из HTML-отчёта Archi')
    ap.add_argument('model', help='файл .archimate')
    ap.add_argument('report', help='каталог отчёта (--html.createReport)')
    ap.add_argument('--log', default=os.environ.get('ARCHI_EXPORT_LOG'),
                    help='дописать пути выгруженных PDF в этот файл')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    written, missing = export(args.model, args.report, args.log, args.quiet)
    if not written:
        print('Ни одного представления из отчёта не получено: %s' % args.report,
              file=sys.stderr)
        return 3
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
