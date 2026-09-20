#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Выгрузка представлений (View) моделей Archi в PDF.

Правило репозитория: любое изменение представления в схеме обязано сопровождаться
обновлением его PDF-представления. Файл кладётся в папку проекта под именем
    <проект>_<имя представления>.pdf
где «проект» — имя папки верхнего уровня, в которой лежит файл `.archimate`.

Использование:
    python3 tools/archi_export_pdf.py                       # все модели репозитория
    python3 tools/archi_export_pdf.py "Склады/WMS.archimate"
    python3 tools/archi_export_pdf.py "Склады/WMS.archimate" --view "Целевая архитектура"
    python3 tools/archi_export_pdf.py --check               # проверка актуальности (CI/хук)
    python3 tools/archi_export_pdf.py --list                # перечислить ожидаемые файлы

Выгрузка детерминирована: один и тот же `.archimate` даёт побайтово одинаковый PDF,
поэтому `--check` умеет отличать устаревшее представление от актуального.

Зависимостей нет — рисование берёт на себя `tools/minipdf.py`.
"""
import argparse
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minipdf import PDFDocument, find_font                      # noqa: E402

XSI = '{http://www.w3.org/2001/XMLSchema-instance}type'

# --- геометрия страницы (координаты — «пиксели» Archi, они же пункты PDF) ---
MARGIN = 28
HEADER_H = 54
FOOTER_H = 22
PX_PER_PT = 4.0 / 3.0          # 9 pt в Archi (96 dpi) = 12 px нашего холста

DEFAULT_FONT_PT = 9.0
MIN_FONT_PX = 6.5
PAD = 5                        # внутренний отступ текста в блоке
ICON = 13                      # сторона значка типа элемента

# --- цвета по слоям ArchiMate (умолчания Archi) ---
LAYER_FILL = {
    'business': '#ffffb5',
    'application': '#b5ffff',
    'technology': '#c9e7b7',
    'physical': '#c9e7b7',
    'motivation': '#ccccff',
    'strategy': '#f5deaa',
    'implementation': '#ffe0e0',
    'other': '#ffffff',
}

BUSINESS = ('BusinessActor', 'BusinessRole', 'BusinessCollaboration', 'BusinessInterface',
            'BusinessProcess', 'BusinessFunction', 'BusinessInteraction', 'BusinessEvent',
            'BusinessService', 'BusinessObject', 'Contract', 'Representation', 'Product')
APPLICATION = ('ApplicationComponent', 'ApplicationCollaboration', 'ApplicationInterface',
               'ApplicationFunction', 'ApplicationInteraction', 'ApplicationProcess',
               'ApplicationEvent', 'ApplicationService', 'DataObject')
TECHNOLOGY = ('Node', 'Device', 'SystemSoftware', 'TechnologyCollaboration',
              'TechnologyInterface', 'Path', 'CommunicationNetwork', 'TechnologyFunction',
              'TechnologyProcess', 'TechnologyInteraction', 'TechnologyEvent',
              'TechnologyService', 'Artifact')
PHYSICAL = ('Equipment', 'Facility', 'DistributionNetwork', 'Material')
MOTIVATION = ('Stakeholder', 'Driver', 'Assessment', 'Goal', 'Outcome', 'Principle',
              'Requirement', 'Constraint', 'Meaning', 'Value')
STRATEGY = ('Resource', 'Capability', 'CourseOfAction', 'ValueStream')
IMPLEMENTATION = ('WorkPackage', 'Deliverable', 'ImplementationEvent', 'Plateau', 'Gap')


def layer_of(etype):
    if etype in BUSINESS:
        return 'business'
    if etype in APPLICATION:
        return 'application'
    if etype in TECHNOLOGY:
        return 'technology'
    if etype in PHYSICAL:
        return 'physical'
    if etype in MOTIVATION:
        return 'motivation'
    if etype in STRATEGY:
        return 'strategy'
    if etype in IMPLEMENTATION:
        return 'implementation'
    return 'other'


# --------------------------------------------------------------------------
#  Загрузка модели
# --------------------------------------------------------------------------

class Model(object):
    """Разобранный файл .archimate: элементы, связи, представления, картинки."""

    def __init__(self, path):
        self.path = path
        self.images = {}
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                self.root = ET.fromstring(z.read('model.xml'))
                for entry in z.namelist():
                    if entry.lower().endswith('.png'):
                        self.images[entry] = z.read(entry)
        else:
            self.root = ET.parse(path).getroot()
        self.name = self.root.get('name') or os.path.basename(path)
        self.rel_path = os.path.relpath(os.path.abspath(path), repo_root()).replace(os.sep, '/')
        self.elements = {}
        self.relations = {}
        for el in self.root.iter('element'):
            t = (el.get(XSI) or '').split(':')[-1]
            if 'DiagramModel' in t or 'SketchModel' in t:
                continue
            rec = {
                'type': t,
                'name': el.get('name') or '',
                'documentation': (el.findtext('documentation') or ''),
                'properties': {p.get('key'): (p.get('value') or '')
                               for p in el.findall('property')},
                'source': el.get('source'),
                'target': el.get('target'),
            }
            if t.endswith('Relationship'):
                self.relations[el.get('id')] = rec
            else:
                self.elements[el.get('id')] = rec

    def views(self):
        out = []
        for folder in self.root.findall('folder'):
            if folder.get('type') != 'diagrams':
                continue
            for el in folder.iter('element'):
                t = el.get(XSI) or ''
                if 'DiagramModel' in t or 'SketchModel' in t:
                    out.append(el)
        return out


def feature(node, name):
    for f in node.findall('feature'):
        if f.get('name') == name:
            return f.get('value')
    return None


# --------------------------------------------------------------------------
#  Разбор представления
# --------------------------------------------------------------------------

class Node(object):
    """Объект схемы с абсолютными координатами."""

    def __init__(self, xml, x, y, w, h, depth, parent):
        self.xml = xml
        self.x, self.y, self.w, self.h = x, y, w, h
        self.depth = depth
        self.parent = parent
        self.id = xml.get('id')
        self.kind = (xml.get(XSI) or '').split(':')[-1]   # DiagramObject / Note / Group

    def centre(self):
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)


def collect_nodes(view):
    """Обход дерева схемы: список узлов в порядке отрисовки (родители раньше детей)."""
    nodes = []

    def walk(parent_xml, ox, oy, depth, parent):
        for ch in parent_xml.findall('child'):
            b = ch.find('bounds')
            if b is None:
                continue
            x = ox + int(b.get('x') or 0)
            y = oy + int(b.get('y') or 0)
            w = int(b.get('width') or 120)
            h = int(b.get('height') or 55)
            node = Node(ch, x, y, w, h, depth, parent)
            nodes.append(node)
            walk(ch, x, y, depth + 1, node)

    walk(view, 0, 0, 0, None)
    return nodes


def is_nested(a, b):
    """Один из блоков вложен в другой (Archi такие связи не рисует)."""
    for pair in ((a, b), (b, a)):
        node = pair[0].parent
        while node is not None:
            if node is pair[1]:
                return True
            node = node.parent
    return False


def collect_connections(view, by_id):
    out = []
    for conn in view.iter('sourceConnection'):
        s, t = by_id.get(conn.get('source')), by_id.get(conn.get('target'))
        if s is None or t is None or is_nested(s, t):
            continue
        out.append((conn, s, t))
    return out


# --------------------------------------------------------------------------
#  Стиль
# --------------------------------------------------------------------------

def darker(color, factor=0.62):
    c = color.lstrip('#')
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return '#%02x%02x%02x' % (int(r * factor), int(g * factor), int(b * factor))


def font_px(node_xml):
    """Размер шрифта в пикселях холста из атрибута font ('1|Segoe UI|9.0|0|...')."""
    raw = node_xml.get('font')
    pt = DEFAULT_FONT_PT
    if raw:
        parts = raw.split('|')
        if len(parts) > 2:
            try:
                pt = float(parts[2])
            except ValueError:
                pt = DEFAULT_FONT_PT
    return pt * PX_PER_PT


def label_of(node_xml, element):
    """Подпись: labelExpression имеет приоритет над именем элемента."""
    expr = feature(node_xml, 'labelExpression')
    if expr:
        return expand_expression(expr, element)
    if node_xml.get('name'):
        return node_xml.get('name')
    return element['name'] if element else ''


def expand_expression(expr, element):
    """Поддержаны ${name}, ${documentation}, ${property:Ключ}, ${type}."""
    out, i = [], 0
    while i < len(expr):
        if expr.startswith('${', i):
            end = expr.find('}', i)
            if end < 0:
                out.append(expr[i:])
                break
            token = expr[i + 2:end]
            value = ''
            if element:
                if token == 'name':
                    value = element['name']
                elif token == 'documentation':
                    value = element['documentation']
                elif token == 'type':
                    value = element['type']
                elif token.startswith('property:'):
                    value = element['properties'].get(token[9:], '')
            out.append(value)
            i = end + 1
        else:
            out.append(expr[i])
            i += 1
    return ''.join(out)


# --------------------------------------------------------------------------
#  Текст
# --------------------------------------------------------------------------

def wrap_text(page, font, size, text, width):
    lines = []
    for para in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        para = para.rstrip()
        if not para:
            lines.append('')
            continue
        words, cur = para.split(' '), ''
        for word in words:
            probe = (cur + ' ' + word).strip()
            if cur and page.text_width(probe, font, size) > width:
                lines.append(cur)
                cur = word
            else:
                cur = probe
            while page.text_width(cur, font, size) > width and len(cur) > 1:
                cut = len(cur)
                while cut > 1 and page.text_width(cur[:cut], font, size) > width:
                    cut -= 1
                lines.append(cur[:cut])
                cur = cur[cut:]
        lines.append(cur)
    return lines


def fit_text(page, font, text, box_w, box_h, size):
    """Подбирает размер шрифта так, чтобы подпись поместилась в блок."""
    while size > MIN_FONT_PX:
        lines = wrap_text(page, font, size, text, box_w)
        if len(lines) * size * 1.22 <= box_h:
            return size, lines
        size -= 0.5
    return size, wrap_text(page, font, size, text, box_w)


def draw_label(page, font, text, x, y, w, h, size, color, align='center',
               position='top', shrink=True):
    if not text.strip():
        return
    if shrink:
        size, lines = fit_text(page, font, text, w, h, size)
    else:
        lines = wrap_text(page, font, size, text, w)
    line_h = size * 1.22
    total = len(lines) * line_h
    if position == 'center':
        top = y + max(0.0, (h - total) / 2.0)
    elif position == 'bottom':
        top = y + max(0.0, h - total)
    else:
        top = y
    page.save_state()
    page.clip_rect(x - 1, y - 1, w + 2, h + 2)
    for i, line in enumerate(lines):
        if not line:
            continue
        tw = page.text_width(line, font, size)
        if align == 'left':
            tx = x
        elif align == 'right':
            tx = x + w - tw
        else:
            tx = x + (w - tw) / 2.0
        page.text(tx, top + i * line_h + size * 0.92, line, font, size, color)
    page.restore_state()


# --------------------------------------------------------------------------
#  Значки типов элементов (нотация ArchiMate, правый верхний угол)
# --------------------------------------------------------------------------

def draw_icon(page, etype, x, y, s, color, bg='#ffffff'):
    """Рисует значок типа в квадрате (x, y, s, s) поверх заливки блока bg."""
    w = s
    h = s
    page.set_line_width(0.9)

    def rect(rx, ry, rw, rh, fill=None):
        page.rect(x + rx * w, y + ry * h, rw * w, rh * h, fill=fill, stroke=color)

    if etype.endswith('Component'):
        rect(0.25, 0.1, 0.75, 0.8)
        rect(0.0, 0.25, 0.35, 0.18, fill=bg)
        rect(0.0, 0.57, 0.35, 0.18, fill=bg)
    elif etype.endswith('Function'):
        page.polygon([(x + 0.12 * w, y + 0.95 * h), (x + 0.12 * w, y + 0.3 * h),
                      (x + 0.5 * w, y + 0.05 * h), (x + 0.88 * w, y + 0.3 * h),
                      (x + 0.88 * w, y + 0.95 * h), (x + 0.5 * w, y + 0.7 * h)],
                     stroke=color)
    elif etype.endswith('Process'):
        page.polygon([(x + 0.05 * w, y + 0.3 * h), (x + 0.6 * w, y + 0.3 * h),
                      (x + 0.6 * w, y + 0.1 * h), (x + 0.95 * w, y + 0.5 * h),
                      (x + 0.6 * w, y + 0.9 * h), (x + 0.6 * w, y + 0.7 * h),
                      (x + 0.05 * w, y + 0.7 * h)], stroke=color)
    elif etype.endswith('Service'):
        page.round_rect(x + 0.05 * w, y + 0.28 * h, 0.9 * w, 0.44 * h, 0.22 * h,
                        stroke=color)
    elif etype.endswith('Interface'):
        page.line(x + 0.05 * w, y + 0.5 * h, x + 0.55 * w, y + 0.5 * h, color)
        page.ellipse(x + 0.72 * w, y + 0.5 * h, 0.22 * w, 0.22 * h, stroke=color)
    elif etype.endswith('Collaboration'):
        page.ellipse(x + 0.32 * w, y + 0.5 * h, 0.3 * w, 0.3 * h, stroke=color)
        page.ellipse(x + 0.68 * w, y + 0.5 * h, 0.3 * w, 0.3 * h, stroke=color)
    elif etype in ('BusinessActor', 'Stakeholder'):
        page.ellipse(x + 0.5 * w, y + 0.2 * h, 0.16 * w, 0.16 * h, stroke=color)
        page.line(x + 0.5 * w, y + 0.36 * h, x + 0.5 * w, y + 0.72 * h, color)
        page.line(x + 0.18 * w, y + 0.48 * h, x + 0.82 * w, y + 0.48 * h, color)
        page.line(x + 0.5 * w, y + 0.72 * h, x + 0.2 * w, y + 0.98 * h, color)
        page.line(x + 0.5 * w, y + 0.72 * h, x + 0.8 * w, y + 0.98 * h, color)
    elif etype in ('BusinessObject', 'DataObject', 'Artifact', 'Representation',
                   'Deliverable', 'Contract'):
        rect(0.05, 0.15, 0.9, 0.7)
        page.line(x + 0.05 * w, y + 0.38 * h, x + 0.95 * w, y + 0.38 * h, color)
    elif etype in ('Node', 'Device', 'Equipment', 'SystemSoftware'):
        rect(0.05, 0.3, 0.7, 0.6)
        page.polyline([(x + 0.05 * w, y + 0.3 * h), (x + 0.3 * w, y + 0.08 * h),
                       (x + 0.95 * w, y + 0.08 * h), (x + 0.95 * w, y + 0.68 * h),
                       (x + 0.75 * w, y + 0.9 * h)], stroke=color)
        page.line(x + 0.75 * w, y + 0.3 * h, x + 0.95 * w, y + 0.08 * h, color)
    elif etype in ('CommunicationNetwork', 'Path', 'DistributionNetwork'):
        page.ellipse(x + 0.15 * w, y + 0.25 * h, 0.13 * w, 0.13 * h, fill=color)
        page.ellipse(x + 0.85 * w, y + 0.25 * h, 0.13 * w, 0.13 * h, fill=color)
        page.ellipse(x + 0.5 * w, y + 0.8 * h, 0.13 * w, 0.13 * h, fill=color)
        page.line(x + 0.15 * w, y + 0.25 * h, x + 0.85 * w, y + 0.25 * h, color)
        page.line(x + 0.15 * w, y + 0.25 * h, x + 0.5 * w, y + 0.8 * h, color)
        page.line(x + 0.85 * w, y + 0.25 * h, x + 0.5 * w, y + 0.8 * h, color)
    elif etype in ('Goal', 'Outcome'):
        page.ellipse(x + 0.5 * w, y + 0.5 * h, 0.45 * w, 0.45 * h, stroke=color)
        page.ellipse(x + 0.5 * w, y + 0.5 * h, 0.25 * w, 0.25 * h, stroke=color)
        page.ellipse(x + 0.5 * w, y + 0.5 * h, 0.08 * w, 0.08 * h, fill=color)
    elif etype == 'Assessment':
        page.ellipse(x + 0.42 * w, y + 0.4 * h, 0.35 * w, 0.35 * h, stroke=color)
        page.line(x + 0.66 * w, y + 0.66 * h, x + 0.95 * w, y + 0.95 * h, color)
    elif etype in ('Driver', 'Value'):
        page.ellipse(x + 0.5 * w, y + 0.5 * h, 0.45 * w, 0.45 * h, stroke=color)
        page.line(x + 0.5 * w, y + 0.05 * h, x + 0.5 * w, y + 0.95 * h, color)
        page.line(x + 0.05 * w, y + 0.5 * h, x + 0.95 * w, y + 0.5 * h, color)
    elif etype == 'Product':
        rect(0.05, 0.15, 0.9, 0.7)
        rect(0.05, 0.15, 0.45, 0.22, fill=None)
    elif etype.endswith('Event'):
        page.polyline([(x + 0.05 * w, y + 0.28 * h), (x + 0.6 * w, y + 0.28 * h),
                       (x + 0.92 * w, y + 0.5 * h), (x + 0.6 * w, y + 0.72 * h),
                       (x + 0.05 * w, y + 0.72 * h)], stroke=color)
        page.polyline([(x + 0.05 * w, y + 0.72 * h), (x + 0.25 * w, y + 0.5 * h),
                       (x + 0.05 * w, y + 0.28 * h)], stroke=color)
    elif etype == 'Grouping':
        page.set_dash([2, 2])
        rect(0.05, 0.25, 0.9, 0.65)
        rect(0.05, 0.08, 0.4, 0.17)
        page.set_dash()
    elif etype in ('Capability', 'Resource', 'CourseOfAction', 'ValueStream',
                   'WorkPackage', 'Plateau'):
        rect(0.05, 0.2, 0.9, 0.6)
    elif etype == 'Location':
        page.ellipse(x + 0.5 * w, y + 0.35 * h, 0.3 * w, 0.3 * h, stroke=color)
        page.polygon([(x + 0.25 * w, y + 0.5 * h), (x + 0.75 * w, y + 0.5 * h),
                      (x + 0.5 * w, y + 0.95 * h)], stroke=color)
    else:
        rect(0.1, 0.2, 0.8, 0.6)
    page.set_line_width(1)


# --------------------------------------------------------------------------
#  Фигуры элементов
# --------------------------------------------------------------------------

ROUNDED = ('BusinessService', 'ApplicationService', 'TechnologyService')


def draw_shape(page, etype, x, y, w, h, fill, line):
    """Контур элемента согласно нотации ArchiMate."""
    if etype in ROUNDED:
        page.round_rect(x, y, w, h, min(12, h / 2.0), fill=fill, stroke=line)
    elif etype in MOTIVATION:
        c = min(10, w / 4.0, h / 4.0)
        page.polygon([(x + c, y), (x + w - c, y), (x + w, y + c), (x + w, y + h - c),
                      (x + w - c, y + h), (x + c, y + h), (x, y + h - c), (x, y + c)],
                     fill=fill, stroke=line)
    elif etype == 'Grouping':
        tab_w = min(max(w * 0.32, 60), w - 10)
        tab_h = min(18, h / 3.0)
        page.set_dash([4, 3])
        page.polygon([(x, y + tab_h), (x + tab_w, y + tab_h), (x + tab_w, y),
                      (x, y)], fill=fill, stroke=line)
        page.rect(x, y + tab_h, w, h - tab_h, fill=fill, stroke=line)
        page.set_dash()
    else:
        page.rect(x, y, w, h, fill=fill, stroke=line)


def draw_note(page, x, y, w, h, fill, line, border_type):
    """Заметка: по умолчанию — «загнутый угол», borderType=1 — рамка, 2 — без рамки."""
    if border_type == '2':
        page.rect(x, y, w, h, fill=fill, stroke=None)
        return
    if border_type == '1':
        page.rect(x, y, w, h, fill=fill, stroke=line)
        return
    c = min(14, w / 4.0, h / 3.0)
    page.polygon([(x, y), (x + w, y), (x + w, y + h - c), (x + w - c, y + h), (x, y + h)],
                 fill=fill, stroke=line)
    page.polyline([(x + w, y + h - c), (x + w - c, y + h - c), (x + w - c, y + h)],
                  stroke=line)


# --------------------------------------------------------------------------
#  Связи
# --------------------------------------------------------------------------

ARROW_FILLED = ('FlowRelationship', 'TriggeringRelationship')
ARROW_HOLLOW = ('RealizationRelationship', 'SpecializationRelationship')
ARROW_OPEN = ('ServingRelationship', 'AccessRelationship', 'InfluenceRelationship',
              'AssignmentRelationship')
DASHED = {'FlowRelationship': [6, 4], 'RealizationRelationship': [2, 3],
          'AccessRelationship': [1.5, 3], 'InfluenceRelationship': [4, 3]}


def clip_to_box(node, inner, outer):
    """Точка пересечения отрезка центр->inner с границей блока node."""
    cx, cy = outer
    px, py = inner
    dx, dy = px - cx, py - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return outer
    x0, y0, w, h = node.x, node.y, node.w, node.h
    best = None
    for t_edge, coord in (((x0 - cx) / dx if dx else None, 'x'),
                          ((x0 + w - cx) / dx if dx else None, 'x'),
                          ((y0 - cy) / dy if dy else None, 'y'),
                          ((y0 + h - cy) / dy if dy else None, 'y')):
        if t_edge is None or t_edge <= 0 or t_edge > 1.0001:
            continue
        ix, iy = cx + dx * t_edge, cy + dy * t_edge
        if x0 - 0.5 <= ix <= x0 + w + 0.5 and y0 - 0.5 <= iy <= y0 + h + 0.5:
            if best is None or t_edge < best[0]:
                best = (t_edge, (ix, iy))
    return best[1] if best else outer


def connection_points(conn, src, dst):
    """Ломаная связи: точки излома хранятся относительно центра источника."""
    sc, tc = src.centre(), dst.centre()
    mids = []
    for bp in conn.findall('bendpoint'):
        mids.append((sc[0] + float(bp.get('startX') or 0),
                     sc[1] + float(bp.get('startY') or 0)))
    start = clip_to_box(src, mids[0] if mids else tc, sc)
    end = clip_to_box(dst, mids[-1] if mids else sc, tc)
    return [start] + mids + [end]


def draw_arrow_head(page, tip, frm, kind, color):
    import math
    ang = math.atan2(tip[1] - frm[1], tip[0] - frm[0])
    if kind == 'open':
        size, spread = 9.0, 0.45
        for s in (1, -1):
            page.line(tip[0], tip[1],
                      tip[0] - size * math.cos(ang - s * spread),
                      tip[1] - size * math.sin(ang - s * spread), color)
        return
    size = 10.0 if kind == 'hollow' else 9.0
    spread = 0.38
    p1 = (tip[0] - size * math.cos(ang - spread), tip[1] - size * math.sin(ang - spread))
    p2 = (tip[0] - size * math.cos(ang + spread), tip[1] - size * math.sin(ang + spread))
    if kind == 'hollow':
        page.polygon([tip, p1, p2], fill='#ffffff', stroke=color)
    else:
        page.polygon([tip, p1, p2], fill=color, stroke=color)


def draw_source_decoration(page, rtype, start, nxt, color):
    import math
    if rtype not in ('CompositionRelationship', 'AggregationRelationship',
                     'AssignmentRelationship'):
        return
    ang = math.atan2(nxt[1] - start[1], nxt[0] - start[0])
    if rtype == 'AssignmentRelationship':
        page.ellipse(start[0] + 3 * math.cos(ang), start[1] + 3 * math.sin(ang),
                     3.2, 3.2, fill=color)
        return
    ln, half = 12.0, 4.5
    tip = start
    mid1 = (start[0] + ln / 2 * math.cos(ang) - half * math.sin(ang),
            start[1] + ln / 2 * math.sin(ang) + half * math.cos(ang))
    far = (start[0] + ln * math.cos(ang), start[1] + ln * math.sin(ang))
    mid2 = (start[0] + ln / 2 * math.cos(ang) + half * math.sin(ang),
            start[1] + ln / 2 * math.sin(ang) - half * math.cos(ang))
    fill = color if rtype == 'CompositionRelationship' else '#ffffff'
    page.polygon([tip, mid1, far, mid2], fill=fill, stroke=color)


def path_midpoint(points):
    import math
    total = 0.0
    seg = []
    for i in range(len(points) - 1):
        d = math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
        seg.append(d)
        total += d
    half = total / 2.0
    acc = 0.0
    for i, d in enumerate(seg):
        if acc + d >= half and d > 0:
            k = (half - acc) / d
            return (points[i][0] + (points[i + 1][0] - points[i][0]) * k,
                    points[i][1] + (points[i + 1][1] - points[i][1]) * k)
        acc += d
    return points[0]


# --------------------------------------------------------------------------
#  Отрисовка представления
# --------------------------------------------------------------------------

def render_view(model, view, doc, font, font_bold, project):
    nodes = collect_nodes(view)
    by_id = {n.id: n for n in nodes}
    conns = collect_connections(view, by_id)

    if nodes:
        min_x = min(n.x for n in nodes)
        min_y = min(n.y for n in nodes)
        max_x = max(n.x + n.w for n in nodes)
        max_y = max(n.y + n.h for n in nodes)
    else:
        min_x = min_y = 0
        max_x = max_y = 200

    # точки излома и подписи связей могут выходить за габариты блоков
    for conn, src, dst in conns:
        pts = connection_points(conn, src, dst)
        for px, py in pts:
            min_x, max_x = min(min_x, px), max(max_x, px)
            min_y, max_y = min(min_y, py), max(max_y, py)
        text = connection_label(conn, model.relations.get(conn.get('archimateRelationship')))
        if text:
            bx, by, bw, bh, _ = label_box(font, text, font_px(conn), path_midpoint(pts))
            min_x, max_x = min(min_x, bx), max(max_x, bx + bw)
            min_y, max_y = min(min_y, by), max(max_y, by + bh)

    draw_w = max_x - min_x
    draw_h = max_y - min_y
    page_w = draw_w + 2 * MARGIN
    page_h = draw_h + 2 * MARGIN + HEADER_H + FOOTER_H
    page = doc.add_page(page_w, page_h)

    off_x = MARGIN - min_x
    off_y = MARGIN + HEADER_H - min_y

    page.rect(0, 0, page_w, page_h, fill='#ffffff')

    # --- шапка ---
    view_name = view.get('name') or 'View'
    page.text(MARGIN, MARGIN + 6, '%s — %s' % (project, view_name), font_bold, 17)
    page.text(MARGIN, MARGIN + 24,
              'Модель «%s» · %s' % (model.name, model.rel_path),
              font, 10, '#666666')
    page.set_line_width(0.8)
    page.line(MARGIN, MARGIN + HEADER_H - 16, page_w - MARGIN, MARGIN + HEADER_H - 16,
              '#cccccc')

    # --- блоки ---
    for node in nodes:
        draw_node(page, model, doc, node, font, off_x, off_y)

    # --- связи поверх блоков ---
    for conn, src, dst in conns:
        draw_connection(page, model, conn, src, dst, font, off_x, off_y)

    # --- подвал ---
    page.line(MARGIN, page_h - FOOTER_H - 4, page_w - MARGIN, page_h - FOOTER_H - 4,
              '#cccccc')
    page.text(MARGIN, page_h - FOOTER_H + 8,
              'Представление выгружено из модели Archi: %s → «%s». '
              'Обновляется автоматически: tools/archi_export_pdf.py'
              % (model.rel_path, view_name), font, 8.5, '#777777')
    return page


def draw_node(page, model, doc, node, font, off_x, off_y):
    x, y = node.x + off_x, node.y + off_y
    w, h = node.w, node.h
    xml = node.xml
    fill = xml.get('fillColor')
    line = xml.get('lineColor')
    fcolor = xml.get('fontColor') or '#000000'
    size = font_px(xml)
    align = {'1': 'left', '2': 'center', '3': 'right'}.get(xml.get('textAlignment'))
    position = {'0': 'top', '1': 'center', '2': 'bottom'}.get(xml.get('textPosition'), 'top')

    page.set_line_width(1)

    if node.kind == 'Note':
        fill = fill or '#ffffff'
        line = line or '#b5b5b5'
        draw_note(page, x, y, w, h, fill, line, xml.get('borderType'))
        text = xml.findtext('content') or ''
        draw_label(page, font, text, x + PAD, y + PAD, w - 2 * PAD, h - 2 * PAD,
                   size, fcolor, align or 'left', position, shrink=False)
        return

    if node.kind == 'Group':
        fill = fill or '#dcdcdc'
        line = line or darker(fill, 0.75)
        tab_h = min(20, h / 3.0)
        tab_w = min(max(w * 0.3, 80), w)
        page.polygon([(x, y), (x + tab_w, y), (x + tab_w, y + tab_h), (x + w, y + tab_h),
                      (x + w, y + h), (x, y + h)], fill=fill, stroke=line)
        page.line(x, y + tab_h, x + tab_w, y + tab_h, line)
        draw_label(page, font, xml.get('name') or '', x + PAD, y + 2, tab_w - 2 * PAD,
                   tab_h, size, fcolor, align or 'left', 'center')
        return

    element = model.elements.get(xml.get('archimateElement'))
    etype = element['type'] if element else 'Other'
    fill = fill or LAYER_FILL[layer_of(etype)]
    line = line or darker(fill)
    draw_shape(page, etype, x, y, w, h, fill, line)

    # собственная картинка элемента (imagePosition 0 — левый верхний угол)
    img_w = 0
    img_path = xml.get('imagePath')
    if img_path and img_path in model.images:
        name = doc.png_image(model.images[img_path], img_path)
        side = min(16.0, w / 3.0, h - 2)
        if side >= 6:
            page.image(name, x + 3, y + 3, side, side)
            img_w = side + 2

    # значок типа в правом верхнем углу
    icon_w = 0
    if w >= 52 and h >= 20:
        draw_icon(page, etype, x + w - ICON - 4, y + 4, ICON, line, fill)
        icon_w = ICON + 6

    text = label_of(xml, element)
    tx = x + PAD + img_w
    tw = w - 2 * PAD - img_w - icon_w
    if tw < 20:
        tx, tw = x + 2, max(16, w - 4)
    draw_label(page, font, text, tx, y + PAD, tw, h - 2 * PAD, size, fcolor,
               align or 'center', position)


def connection_label(conn, rel):
    text = feature(conn, 'labelExpression') or (rel['name'] if rel else '')
    if not text:
        return None
    if '${' in text:
        text = expand_expression(text, rel)
    return text.replace('\r\n', '\n').replace('\r', '\n').strip()


def label_box(font, text, size, mid):
    """Габариты подписи связи вокруг середины ломаной."""
    lines = text.split('\n')
    tw = max(font.ttf.text_width(ln, size) for ln in lines)
    th = len(lines) * size * 1.2
    return (mid[0] - tw / 2.0 - 2, mid[1] - th / 2.0 - 1, tw + 4, th + 2, lines)


def draw_connection(page, model, conn, src, dst, font, off_x, off_y):
    rel = model.relations.get(conn.get('archimateRelationship'))
    rtype = rel['type'] if rel else 'DiagramConnection'
    color = conn.get('lineColor') or ('#5a5a5a' if rel else '#a0a0a0')
    pts = [(p[0] + off_x, p[1] + off_y) for p in connection_points(conn, src, dst)]

    page.set_line_width(1)
    dash = DASHED.get(rtype)
    if rel is None:
        dash = [3, 3]
    page.set_dash(dash)
    page.polyline(pts, stroke=color)
    page.set_dash()

    if rtype in ARROW_FILLED:
        draw_arrow_head(page, pts[-1], pts[-2], 'filled', color)
    elif rtype in ARROW_HOLLOW:
        draw_arrow_head(page, pts[-1], pts[-2], 'hollow', color)
    elif rtype in ARROW_OPEN:
        draw_arrow_head(page, pts[-1], pts[-2], 'open', color)
    elif rtype == 'AssociationRelationship' and rel and rel.get('directed') == 'true':
        draw_arrow_head(page, pts[-1], pts[-2], 'open', color)
    draw_source_decoration(page, rtype, pts[0], pts[1], color)

    text = connection_label(conn, rel)
    if not text:
        return
    size = font_px(conn)
    fcolor = conn.get('fontColor') or '#000000'
    mid = path_midpoint(pts)
    bx, by, bw, bh, lines = label_box(font, text, size, mid)
    page.rect(bx, by, bw, bh, fill='#ffffff')
    for i, ln in enumerate(lines):
        page.text(mid[0] - font.ttf.text_width(ln, size) / 2.0,
                  by + i * size * 1.2 + size * 1.02, ln, font, size, fcolor)


# --------------------------------------------------------------------------
#  Файлы и запуск
# --------------------------------------------------------------------------

def project_of(model_path):
    """Имя проекта — папка верхнего уровня относительно корня репозитория."""
    rel = os.path.relpath(model_path, repo_root())
    parts = rel.replace(os.sep, '/').split('/')
    return parts[0] if len(parts) > 1 else os.path.splitext(parts[0])[0]


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def safe_name(name):
    out = name.strip()
    for bad in '/\\:*?"<>|\n\r\t':
        out = out.replace(bad, '-')
    return out.strip(' .') or 'View'


def pdf_path(model_path, view_name, out_dir=None):
    project = project_of(model_path)
    folder = out_dir or os.path.dirname(os.path.abspath(model_path))
    return os.path.join(folder, '%s_%s.pdf' % (safe_name(project), safe_name(view_name)))


def build_pdf_bytes(model, view, font_path, font_bold_path, tmp_path):
    project = project_of(model.path)
    doc = PDFDocument(title='%s — %s' % (project, view.get('name') or ''),
                      author=model.name,
                      subject='Представление модели Archi «%s»' % model.name)
    font = doc.font(font_path)
    font_bold = doc.font(font_bold_path)
    render_view(model, view, doc, font, font_bold, project)
    doc.save(tmp_path)
    with open(tmp_path, 'rb') as fh:
        return fh.read()


def find_models(paths):
    if paths:
        return [p for p in paths]
    found = []
    for dirpath, dirnames, names in os.walk(repo_root()):
        dirnames[:] = [d for d in dirnames if d != '.git']
        for n in names:
            if n.endswith('.archimate'):
                found.append(os.path.join(dirpath, n))
    return sorted(found)


def main():
    ap = argparse.ArgumentParser(description='Выгрузка представлений Archi в PDF')
    ap.add_argument('files', nargs='*', help='файлы .archimate (по умолчанию — все)')
    ap.add_argument('--view', default=None, help='выгрузить только это представление')
    ap.add_argument('--out', default=None, help='каталог вывода (по умолчанию — папка проекта)')
    ap.add_argument('--check', action='store_true',
                    help='только проверить актуальность PDF, ничего не записывать')
    ap.add_argument('--list', action='store_true', help='перечислить ожидаемые PDF')
    ap.add_argument('--font', default=None, help='путь к TTF основного шрифта')
    ap.add_argument('--font-bold', default=None, help='путь к TTF полужирного шрифта')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    font_path = args.font or find_font()
    font_bold_path = args.font_bold or find_font(bold=True)

    models = find_models(args.files)
    stale, written, expected = [], [], []
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.export.tmp.pdf')

    for mp in models:
        model = Model(mp)
        for view in model.views():
            name = view.get('name') or 'View'
            if args.view and name != args.view:
                continue
            target = pdf_path(mp, name, args.out)
            expected.append(target)
            if args.list:
                print(os.path.relpath(target, repo_root()))
                continue
            data = build_pdf_bytes(model, view, font_path, font_bold_path, tmp_path)
            old = None
            if os.path.exists(target):
                with open(target, 'rb') as fh:
                    old = fh.read()
            if old == data:
                if not args.quiet and not args.check:
                    print('= %s' % os.path.relpath(target, repo_root()))
                continue
            if args.check:
                stale.append(target)
                continue
            with open(target, 'wb') as fh:
                fh.write(data)
            written.append(target)
            if not args.quiet:
                print('%s %s' % ('+' if old is None else '~',
                                 os.path.relpath(target, repo_root())))

    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    if args.list:
        return 0

    # осиротевшие файлы: PDF есть, а представления с таким именем уже нет
    orphans = []
    if not args.view and not args.files and not args.out:
        keep = set(os.path.abspath(p) for p in expected)
        for dirpath, dirnames, names in os.walk(repo_root()):
            dirnames[:] = [d for d in dirnames if d != '.git']
            for n in names:
                if n.endswith('.pdf') and os.path.abspath(os.path.join(dirpath, n)) not in keep:
                    orphans.append(os.path.join(dirpath, n))

    if args.check:
        for p in stale:
            print('УСТАРЕЛО: %s' % os.path.relpath(p, repo_root()))
        for p in orphans:
            print('ЛИШНИЙ:   %s' % os.path.relpath(p, repo_root()))
        if stale or orphans:
            print('\nПредставления не соответствуют схемам. Выполните: '
                  'python3 tools/archi_export_pdf.py')
            return 1
        print('Все представления актуальны (%d шт.).' % len(expected))
        return 0

    if not args.quiet:
        print('\nОбновлено: %d из %d' % (len(written), len(expected)))
        for p in orphans:
            print('ЛИШНИЙ (нет такого представления): %s' % os.path.relpath(p, repo_root()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
