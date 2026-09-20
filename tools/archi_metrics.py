#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Метрики читаемости схем Archi (.archimate, в т.ч. упакованных в ZIP).

Считает по каждому View:
  - габариты полотна (для проверки "влезает на один лист");
  - количество связей;
  - количество пересечений связей между собой (основная метрика оптимизации);
  - количество пересечений связей с блоками (проход линии "сквозь" блок);
  - долю связей, проложенных по ортогональной сетке.

Использование:
    python3 tools/archi_metrics.py                      # все модели репозитория
    python3 tools/archi_metrics.py "Склады/WMS.archimate"
    python3 tools/archi_metrics.py "Склады/WMS.archimate" --view "Целевая архитектура"
    python3 tools/archi_metrics.py ... --pairs           # выписать пересекающиеся пары
"""
import sys, os, zipfile, argparse, io
import xml.etree.ElementTree as ET

XSI = '{http://www.w3.org/2001/XMLSchema-instance}type'
EPS = 1e-9


def load_root(path):
    """.archimate бывает и plain XML, и ZIP с model.xml внутри."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return ET.fromstring(z.read('model.xml'))
    return ET.parse(path).getroot()


def views_of(root):
    for folder in root.findall('folder'):
        if folder.get('type') != 'diagrams':
            continue
        for el in folder.iter('element'):
            t = el.get(XSI) or ''
            if 'DiagramModel' in t or 'SketchModel' in t:
                yield el


def collect(view):
    """Абсолютные координаты всех объектов схемы: id -> (x, y, w, h)."""
    boxes = {}

    def walk(node, ox, oy):
        for ch in node.findall('child'):
            b = ch.find('bounds')
            if b is None:
                continue
            x = ox + int(b.get('x') or 0)
            y = oy + int(b.get('y') or 0)
            w = int(b.get('width') or 120)
            h = int(b.get('height') or 55)
            boxes[ch.get('id')] = (x, y, w, h)
            walk(ch, x, y)

    walk(view, 0, 0)
    return boxes


def centre(box):
    x, y, w, h = box
    return (x + w / 2.0, y + h / 2.0)


def polyline(view, boxes):
    """Каждая связь -> (id, точки ломаной, id источника, id цели)."""
    out = []
    for conn in view.iter('sourceConnection'):
        s, t = conn.get('source'), conn.get('target')
        if s not in boxes or t not in boxes:
            continue
        sc, tc = centre(boxes[s]), centre(boxes[t])
        pts = [sc]
        for bp in conn.findall('bendpoint'):
            pts.append((sc[0] + float(bp.get('startX') or 0),
                        sc[1] + float(bp.get('startY') or 0)))
        pts.append(tc)
        out.append((conn.get('id'), pts, s, t))
    return out


def seg_cross(p1, p2, p3, p4):
    """Строгое пересечение отрезков (общие концы не считаем)."""
    def d(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = d(p3, p4, p1), d(p3, p4, p2)
    d3, d4 = d(p1, p2, p3), d(p1, p2, p4)
    return ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
           ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS))


def segments(pts):
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def seg_hits_box(a, b, box, skip):
    """Проходит ли отрезок сквозь прямоугольник (кроме блоков-концов связи)."""
    if skip:
        return False
    x, y, w, h = box
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    return any(seg_cross(a, b, e[0], e[1]) for e in edges)


def orthogonal(pts):
    for (a, b) in segments(pts):
        if abs(a[0] - b[0]) > 2 and abs(a[1] - b[1]) > 2:
            return False
    return True


def analyse(view, with_pairs=False):
    boxes = collect(view)
    lines = polyline(view, boxes)

    xs = [b[0] for b in boxes.values()] + [b[0] + b[2] for b in boxes.values()]
    ys = [b[1] for b in boxes.values()] + [b[1] + b[3] for b in boxes.values()]
    canvas = (max(xs) - min(xs), max(ys) - min(ys)) if boxes else (0, 0)

    crossings, pairs = 0, []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            _, pi, si, ti = lines[i]
            _, pj, sj, tj = lines[j]
            if {si, ti} & {sj, tj}:          # связи из общей вершины не считаем
                continue
            hit = False
            for a in segments(pi):
                for b in segments(pj):
                    if seg_cross(a[0], a[1], b[0], b[1]):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                crossings += 1
                if with_pairs:
                    pairs.append((si, ti, sj, tj))

    through = 0
    for _, pts, s, t in lines:
        for a, b in segments(pts):
            for bid, box in boxes.items():
                if seg_hits_box(a, b, box, bid in (s, t)):
                    through += 1
                    break
            else:
                continue
            break

    ortho = sum(1 for _, pts, _, _ in lines if orthogonal(pts))
    return {
        'objects': len(boxes),
        'links': len(lines),
        'crossings': crossings,
        'through_blocks': through,
        'orthogonal': ortho,
        'canvas': canvas,
        'pairs': pairs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*')
    ap.add_argument('--view', default=None, help='имя схемы (View)')
    ap.add_argument('--pairs', action='store_true', help='вывести пересекающиеся пары')
    args = ap.parse_args()

    files = args.files
    if not files:
        for dirpath, _, names in os.walk('.'):
            if '.git' in dirpath:
                continue
            for n in names:
                if n.endswith('.archimate'):
                    files.append(os.path.join(dirpath, n))
        files.sort()

    for f in files:
        root = load_root(f)
        print('=' * 78)
        print(f'{f}  —  модель "{root.get("name")}"')
        for v in views_of(root):
            if args.view and v.get('name') != args.view:
                continue
            m = analyse(v, args.pairs)
            cw, ch = m['canvas']
            print(f'  • {v.get("name")}: объектов={m["objects"]} связей={m["links"]} '
                  f'ПЕРЕСЕЧЕНИЙ={m["crossings"]} сквозь_блоки={m["through_blocks"]} '
                  f'ортогональных={m["orthogonal"]}/{m["links"]} полотно={cw}x{ch}')
            for si, ti, sj, tj in m['pairs']:
                print(f'      × {si}->{ti}  ×  {sj}->{tj}')


if __name__ == '__main__':
    main()
