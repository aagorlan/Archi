#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Минимальный генератор PDF без внешних зависимостей (только стандартная библиотека).

Умеет ровно то, что нужно для выгрузки схем Archi:
  - страницы произвольного размера с системой координат «y вниз» (как в Archi);
  - векторные примитивы: линии, ломаные, прямоугольники (в т.ч. скруглённые),
    многоугольники, дуги/окружности, пунктир, заливка и обводка;
  - текст кириллицей — TrueType-шрифт встраивается подмножеством (subset),
    кодировка Identity-H, плюс ToUnicode для поиска и копирования текста;
  - растровые картинки PNG (RGB/RGBA/Gray/палитра) с прозрачностью через SMask.

Модуль самодостаточен намеренно: выгрузка PDF должна работать на любой машине
и в CI без `pip install`.
"""
import os
import struct
import zlib

__all__ = ['TrueTypeFont', 'PDFDocument', 'Page', 'find_font']


# --------------------------------------------------------------------------
#  TrueType: разбор и подмножество
# --------------------------------------------------------------------------

FONT_SEARCH = [
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',
    '/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    '/Library/Fonts/Arial Unicode.ttf',
    'C:/Windows/Fonts/segoeui.ttf',
    'C:/Windows/Fonts/arial.ttf',
]

FONT_SEARCH_BOLD = [
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    '/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
    'C:/Windows/Fonts/segoeuib.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
]


def find_font(bold=False):
    """Первый существующий системный TTF из списка поиска."""
    for p in (FONT_SEARCH_BOLD if bold else FONT_SEARCH):
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        'Не найден TrueType-шрифт с кириллицей. Укажите его через --font/--font-bold.')


class TrueTypeFont(object):
    """Разбор TTF: cmap, метрики, подмножество глифов."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as fh:
            self.data = fh.read()
        self.tables = self._read_directory()
        self._read_head()
        self._read_hhea_maxp()
        self._read_hmtx()
        self._read_loca()
        self._read_cmap()
        self._read_os2()
        self.base_name = os.path.splitext(os.path.basename(path))[0].replace(' ', '')

    # --- служебное чтение ---------------------------------------------------

    def _tbl(self, tag):
        t = self.tables.get(tag)
        if not t:
            return None
        off, ln = t
        return self.data[off:off + ln]

    def _read_directory(self):
        tag, num = struct.unpack('>4sH', self.data[0:6])
        if tag == b'ttcf':
            raise ValueError('TTC-коллекции не поддерживаются: %s' % self.path)
        tables = {}
        for i in range(num):
            rec = self.data[12 + 16 * i:28 + 16 * i]
            name, _cs, off, ln = struct.unpack('>4sIII', rec)
            tables[name.decode('latin-1')] = (off, ln)
        return tables

    def _read_head(self):
        head = self._tbl('head')
        self.upem = struct.unpack('>H', head[18:20])[0] or 1000
        self.xmin, self.ymin, self.xmax, self.ymax = struct.unpack('>hhhh', head[36:44])
        self.index_to_loc = struct.unpack('>h', head[50:52])[0]

    def _read_hhea_maxp(self):
        hhea = self._tbl('hhea')
        self.ascent, self.descent = struct.unpack('>hh', hhea[4:8])
        self.num_hmetrics = struct.unpack('>H', hhea[34:36])[0]
        self.num_glyphs = struct.unpack('>H', self._tbl('maxp')[4:6])[0]

    def _read_hmtx(self):
        hmtx = self._tbl('hmtx')
        self.advances = []
        last = 0
        for i in range(self.num_glyphs):
            if i < self.num_hmetrics:
                last = struct.unpack('>H', hmtx[4 * i:4 * i + 2])[0]
            self.advances.append(last)

    def _read_loca(self):
        loca = self._tbl('loca')
        n = self.num_glyphs + 1
        if self.index_to_loc == 0:
            self.loca = [2 * v for v in struct.unpack('>%dH' % n, loca[:2 * n])]
        else:
            self.loca = list(struct.unpack('>%dI' % n, loca[:4 * n]))

    def _read_os2(self):
        os2 = self._tbl('OS/2')
        self.cap_height = self.ascent
        self.italic_angle = 0
        self.stem_v = 80
        if os2 and len(os2) >= 90:
            version = struct.unpack('>H', os2[0:2])[0]
            if version >= 2 and len(os2) >= 90:
                ch = struct.unpack('>h', os2[88:90])[0]
                if ch:
                    self.cap_height = ch
        post = self._tbl('post')
        if post and len(post) >= 8:
            raw = struct.unpack('>i', post[4:8])[0]
            self.italic_angle = raw / 65536.0

    def _read_cmap(self):
        cmap = self._tbl('cmap')
        self.cmap = {}
        if not cmap:
            return
        n = struct.unpack('>H', cmap[2:4])[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack('>HHI', cmap[4 + 8 * i:12 + 8 * i])
            rank = {(3, 10): 5, (0, 4): 4, (0, 6): 4, (3, 1): 3,
                    (0, 3): 3, (0, 2): 2, (0, 1): 2, (3, 0): 1}.get((pid, eid), 0)
            if rank and (best is None or rank > best[0]):
                best = (rank, off)
        if best is None:
            return
        self._parse_cmap_subtable(cmap, best[1])

    def _parse_cmap_subtable(self, cmap, off):
        fmt = struct.unpack('>H', cmap[off:off + 2])[0]
        if fmt == 4:
            segx2 = struct.unpack('>H', cmap[off + 6:off + 8])[0]
            seg = segx2 // 2
            base = off + 14
            ends = struct.unpack('>%dH' % seg, cmap[base:base + segx2])
            base += segx2 + 2
            starts = struct.unpack('>%dH' % seg, cmap[base:base + segx2])
            base += segx2
            deltas = struct.unpack('>%dh' % seg, cmap[base:base + segx2])
            range_off_pos = base + segx2
            offsets = struct.unpack('>%dH' % seg, cmap[range_off_pos:range_off_pos + segx2])
            for i in range(seg):
                for c in range(starts[i], min(ends[i], 0xFFFF) + 1):
                    if offsets[i] == 0:
                        g = (c + deltas[i]) & 0xFFFF
                    else:
                        p = range_off_pos + 2 * i + offsets[i] + 2 * (c - starts[i])
                        if p + 2 > len(cmap):
                            continue
                        g = struct.unpack('>H', cmap[p:p + 2])[0]
                        if g:
                            g = (g + deltas[i]) & 0xFFFF
                    if g:
                        self.cmap.setdefault(c, g)
        elif fmt == 12:
            ngroups = struct.unpack('>I', cmap[off + 12:off + 16])[0]
            for i in range(ngroups):
                s, e, gi = struct.unpack('>III', cmap[off + 16 + 12 * i:off + 28 + 12 * i])
                for c in range(s, min(e, s + 0x10000) + 1):
                    self.cmap.setdefault(c, gi + (c - s))
        elif fmt == 6:
            first, count = struct.unpack('>HH', cmap[off + 6:off + 10])
            for i in range(count):
                g = struct.unpack('>H', cmap[off + 10 + 2 * i:off + 12 + 2 * i])[0]
                if g:
                    self.cmap.setdefault(first + i, g)
        elif fmt == 0:
            for c in range(256):
                g = cmap[off + 6 + c]
                if g:
                    self.cmap.setdefault(c, g)

    # --- метрики ------------------------------------------------------------

    def gid(self, ch):
        return self.cmap.get(ord(ch), 0)

    def glyph_width(self, gid):
        """Ширина глифа в 1/1000 em."""
        adv = self.advances[gid] if gid < len(self.advances) else 0
        return adv * 1000.0 / self.upem

    def text_width(self, text, size):
        w = 0.0
        for ch in text:
            w += self.glyph_width(self.gid(ch))
        return w * size / 1000.0

    # --- подмножество -------------------------------------------------------

    def _glyph_data(self, gid):
        glyf = self.tables.get('glyf')
        if not glyf or gid + 1 >= len(self.loca):
            return b''
        start, end = self.loca[gid], self.loca[gid + 1]
        if end <= start:
            return b''
        off = glyf[0]
        return self.data[off + start:off + end]

    def _composite_deps(self, gid, acc):
        d = self._glyph_data(gid)
        if len(d) < 10:
            return
        ncont = struct.unpack('>h', d[0:2])[0]
        if ncont >= 0:
            return
        p = 10
        while p + 4 <= len(d):
            flags, comp_gid = struct.unpack('>HH', d[p:p + 4])
            p += 4
            if comp_gid not in acc:
                acc.add(comp_gid)
                self._composite_deps(comp_gid, acc)
            p += 4 if (flags & 1) else 2           # ARG_1_AND_2_ARE_WORDS
            if flags & 8:                          # WE_HAVE_A_SCALE
                p += 2
            elif flags & 0x40:                     # X_AND_Y_SCALE
                p += 4
            elif flags & 0x80:                     # TWO_BY_TWO
                p += 8
            if not (flags & 0x20):                 # MORE_COMPONENTS
                break

    def subset(self, gids):
        """Урезанный шрифт: нумерация глифов сохраняется (CIDToGIDMap /Identity)."""
        keep = set(gids) | {0}
        for g in list(keep):
            self._composite_deps(g, keep)
        keep = {g for g in keep if 0 <= g < self.num_glyphs}

        glyf, loca = bytearray(), []
        for gid in range(self.num_glyphs):
            loca.append(len(glyf))
            if gid in keep:
                d = self._glyph_data(gid)
                glyf += d
                while len(glyf) % 4:               # выравнивание глифов
                    glyf += b'\x00'
        loca.append(len(glyf))

        head = bytearray(self._tbl('head'))
        head[8:12] = b'\x00\x00\x00\x00'           # checkSumAdjustment
        head[50:52] = struct.pack('>h', 1)         # длинный формат loca

        out = {
            'head': bytes(head),
            'hhea': self._tbl('hhea'),
            'maxp': self._tbl('maxp'),
            'hmtx': self._tbl('hmtx'),
            'loca': struct.pack('>%dI' % len(loca), *loca),
            'glyf': bytes(glyf),
        }
        for opt in ('cvt ', 'fpgm', 'prep', 'gasp'):
            t = self._tbl(opt)
            if t:
                out[opt] = t
        return _build_sfnt(out)


def _checksum(data):
    data = data + b'\x00' * ((4 - len(data) % 4) % 4)
    return sum(struct.unpack('>%dI' % (len(data) // 4), data)) & 0xFFFFFFFF


def _build_sfnt(tables):
    tags = sorted(tables)
    num = len(tags)
    search = 1
    entry = 0
    while search * 2 <= num:
        search *= 2
        entry += 1
    out = bytearray(struct.pack('>IHHHH', 0x00010000, num, search * 16, entry,
                                num * 16 - search * 16))
    offset = 12 + 16 * num
    body = bytearray()
    for tag in tags:
        d = tables[tag]
        pad = (4 - len(d) % 4) % 4
        out += struct.pack('>4sIII', tag.encode('latin-1'), _checksum(d),
                           offset + len(body), len(d))
        body += d + b'\x00' * pad
    return bytes(out) + bytes(body)


# --------------------------------------------------------------------------
#  Шрифт в документе
# --------------------------------------------------------------------------

class _Font(object):
    def __init__(self, ttf, res_name):
        self.ttf = ttf
        self.res_name = res_name
        self.used = {}          # gid -> unicode

    def encode(self, text):
        """Текст -> hex-строка идентификаторов глифов (Identity-H)."""
        out = []
        for ch in text:
            gid = self.ttf.gid(ch)
            if gid == 0 and ch not in ('\u00a0',):
                gid = self.ttf.gid('?') if ch.strip() else self.ttf.gid(' ')
            self.used[gid] = ch
            out.append('%04X' % gid)
        return ''.join(out)

    def width(self, text, size):
        return self.ttf.text_width(text, size)


# --------------------------------------------------------------------------
#  Страница и рисование
# --------------------------------------------------------------------------

def _num(v):
    if v == int(v):
        return str(int(v))
    return ('%.3f' % v).rstrip('0').rstrip('.')


def _rgb(color):
    """'#rrggbb' или (r, g, b) 0..255 -> строка компонентов PDF."""
    if isinstance(color, str):
        c = color.lstrip('#')
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    else:
        r, g, b = color
    return '%s %s %s' % (_num(r / 255.0), _num(g / 255.0), _num(b / 255.0))


class Page(object):
    """Холст страницы. Координаты — пиксельные, ось y направлена вниз."""

    def __init__(self, doc, width, height):
        self.doc = doc
        self.width = float(width)
        self.height = float(height)
        self.ops = ['1 0 0 -1 0 %s cm' % _num(self.height)]   # переворот оси y
        self.fonts = {}
        self.images = {}

    # --- состояние графики ---

    def save_state(self):
        self.ops.append('q')

    def restore_state(self):
        self.ops.append('Q')

    def clip_rect(self, x, y, w, h):
        self.ops.append('%s %s %s %s re W n' % (_num(x), _num(y), _num(w), _num(h)))

    def set_line_width(self, w):
        self.ops.append('%s w' % _num(w))

    def set_dash(self, pattern=None, phase=0):
        if pattern:
            self.ops.append('[%s] %s d' % (' '.join(_num(p) for p in pattern), _num(phase)))
        else:
            self.ops.append('[] 0 d')

    def set_alpha(self, alpha):
        self.ops.append('/GS%d gs' % self.doc.alpha_state(alpha))

    # --- примитивы ---

    def _paint(self, fill, stroke):
        if fill is not None and stroke is not None:
            self.ops.append('B')
        elif fill is not None:
            self.ops.append('f')
        else:
            self.ops.append('S')

    def _colors(self, fill, stroke):
        if fill is not None:
            self.ops.append('%s rg' % _rgb(fill))
        if stroke is not None:
            self.ops.append('%s RG' % _rgb(stroke))

    def rect(self, x, y, w, h, fill=None, stroke=None):
        if fill is None and stroke is None:
            return
        self._colors(fill, stroke)
        self.ops.append('%s %s %s %s re' % (_num(x), _num(y), _num(w), _num(h)))
        self._paint(fill, stroke)

    def round_rect(self, x, y, w, h, r, fill=None, stroke=None):
        if fill is None and stroke is None:
            return
        r = max(0.0, min(r, w / 2.0, h / 2.0))
        k = r * 0.5523
        self._colors(fill, stroke)
        self.ops.append('%s %s m' % (_num(x + r), _num(y)))
        self.ops.append('%s %s l' % (_num(x + w - r), _num(y)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(x + w - r + k), _num(y),
                                                 _num(x + w), _num(y + r - k),
                                                 _num(x + w), _num(y + r)))
        self.ops.append('%s %s l' % (_num(x + w), _num(y + h - r)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(x + w), _num(y + h - r + k),
                                                 _num(x + w - r + k), _num(y + h),
                                                 _num(x + w - r), _num(y + h)))
        self.ops.append('%s %s l' % (_num(x + r), _num(y + h)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(x + r - k), _num(y + h),
                                                 _num(x), _num(y + h - r + k),
                                                 _num(x), _num(y + h - r)))
        self.ops.append('%s %s l' % (_num(x), _num(y + r)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(x), _num(y + r - k),
                                                 _num(x + r - k), _num(y),
                                                 _num(x + r), _num(y)))
        self.ops.append('h')
        self._paint(fill, stroke)

    def polygon(self, points, fill=None, stroke=None, close=True):
        if not points or (fill is None and stroke is None):
            return
        self._colors(fill, stroke)
        self.ops.append('%s %s m' % (_num(points[0][0]), _num(points[0][1])))
        for x, y in points[1:]:
            self.ops.append('%s %s l' % (_num(x), _num(y)))
        if close:
            self.ops.append('h')
        self._paint(fill, stroke)

    def polyline(self, points, stroke='#000000'):
        self.polygon(points, fill=None, stroke=stroke, close=False)

    def line(self, x1, y1, x2, y2, stroke='#000000'):
        self.polyline([(x1, y1), (x2, y2)], stroke)

    def ellipse(self, cx, cy, rx, ry, fill=None, stroke=None):
        if fill is None and stroke is None:
            return
        kx, ky = rx * 0.5523, ry * 0.5523
        self._colors(fill, stroke)
        self.ops.append('%s %s m' % (_num(cx - rx), _num(cy)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(cx - rx), _num(cy - ky),
                                                 _num(cx - kx), _num(cy - ry),
                                                 _num(cx), _num(cy - ry)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(cx + kx), _num(cy - ry),
                                                 _num(cx + rx), _num(cy - ky),
                                                 _num(cx + rx), _num(cy)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(cx + rx), _num(cy + ky),
                                                 _num(cx + kx), _num(cy + ry),
                                                 _num(cx), _num(cy + ry)))
        self.ops.append('%s %s %s %s %s %s c' % (_num(cx - kx), _num(cy + ry),
                                                 _num(cx - rx), _num(cy + ky),
                                                 _num(cx - rx), _num(cy)))
        self.ops.append('h')
        self._paint(fill, stroke)

    # --- текст ---

    def text(self, x, y, string, font, size, color='#000000'):
        """Текст базовой линией в точке (x, y) при оси y, направленной вниз."""
        if not string:
            return
        f = self._use_font(font)
        self.ops.append('BT /%s %s Tf %s rg 1 0 0 -1 %s %s Tm <%s> Tj ET'
                        % (f.res_name, _num(size), _rgb(color),
                           _num(x), _num(y), f.encode(string)))

    def text_width(self, string, font, size):
        return font.ttf.text_width(string, size)

    def _use_font(self, font):
        self.fonts[font.res_name] = font
        return font

    # --- картинки ---

    def image(self, name, x, y, w, h):
        self.images[name] = True
        self.ops.append('q %s 0 0 %s %s %s cm /%s Do Q'
                        % (_num(w), _num(-h), _num(x), _num(y + h), name))

    def content(self):
        return ('\n'.join(self.ops)).encode('latin-1')


# --------------------------------------------------------------------------
#  Документ
# --------------------------------------------------------------------------

class PDFDocument(object):
    def __init__(self, title='', author='', subject='', compress=True):
        self.objects = [None]            # индекс = номер объекта
        self.pages = []
        self.fonts = {}
        self.images = {}
        self.alphas = {}
        self.title = title
        self.author = author
        self.subject = subject
        self.compress = compress

    # --- ресурсы ---

    def font(self, path):
        key = os.path.abspath(path)
        if key not in self.fonts:
            self.fonts[key] = _Font(TrueTypeFont(path), 'F%d' % (len(self.fonts) + 1))
        return self.fonts[key]

    def png_image(self, data, key=None):
        """Регистрирует PNG, возвращает имя XObject."""
        key = key or ('img%d' % len(self.images))
        if key in self.images:
            return self.images[key][0]
        name = 'Im%d' % (len(self.images) + 1)
        self.images[key] = (name, _png_parts(data))
        return name

    def alpha_state(self, alpha):
        if alpha not in self.alphas:
            self.alphas[alpha] = len(self.alphas) + 1
        return self.alphas[alpha]

    def add_page(self, width, height):
        p = Page(self, width, height)
        self.pages.append(p)
        return p

    # --- запись ---

    def _obj(self, data):
        self.objects.append(data)
        return len(self.objects) - 1

    def _stream(self, extra, data, compress=None):
        compress = self.compress if compress is None else compress
        if compress:
            data = zlib.compress(data, 9)
            extra = extra + ' /Filter /FlateDecode'
        head = '<< /Length %d%s >>' % (len(data), (' ' + extra) if extra else '')
        return head.encode('latin-1') + b'\nstream\n' + data + b'\nendstream'

    def save(self, path):
        self.objects = [None]
        catalog = self._obj(None)
        pages_obj = self._obj(None)

        font_refs = {}
        for f in self.fonts.values():
            font_refs[f.res_name] = self._write_font(f)

        image_refs = {}
        for name, parts in self.images.values():
            image_refs[name] = self._write_image(parts)

        gs_refs = {}
        for alpha, idx in self.alphas.items():
            gs_refs[idx] = self._obj(('<< /Type /ExtGState /ca %s /CA %s >>'
                                      % (_num(alpha), _num(alpha))).encode('latin-1'))

        page_refs = []
        for page in self.pages:
            content = self._obj(self._stream('', page.content()))
            res = ['/ProcSet [/PDF /Text /ImageC]']
            if page.fonts:
                res.append('/Font << %s >>' % ' '.join(
                    '/%s %d 0 R' % (n, font_refs[n]) for n in sorted(page.fonts)))
            if page.images:
                res.append('/XObject << %s >>' % ' '.join(
                    '/%s %d 0 R' % (n, image_refs[n]) for n in sorted(page.images)))
            if gs_refs:
                res.append('/ExtGState << %s >>' % ' '.join(
                    '/GS%d %d 0 R' % (i, r) for i, r in sorted(gs_refs.items())))
            page_refs.append(self._obj(
                ('<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] '
                 '/Resources << %s >> /Contents %d 0 R >>'
                 % (pages_obj, _num(page.width), _num(page.height),
                    ' '.join(res), content)).encode('latin-1')))

        self.objects[pages_obj] = ('<< /Type /Pages /Count %d /Kids [%s] >>'
                                   % (len(page_refs),
                                      ' '.join('%d 0 R' % r for r in page_refs))
                                   ).encode('latin-1')
        self.objects[catalog] = ('<< /Type /Catalog /Pages %d 0 R >>'
                                 % pages_obj).encode('latin-1')
        info = self._obj(('<< /Title %s /Author %s /Subject %s /Producer %s /Creator %s >>'
                          % (_pdf_text(self.title), _pdf_text(self.author),
                             _pdf_text(self.subject), _pdf_text('tools/minipdf.py'),
                             _pdf_text('tools/archi_export_pdf.py'))).encode('latin-1'))

        out = bytearray(b'%PDF-1.5\n%\xe2\xe3\xcf\xd3\n')
        offsets = [0] * len(self.objects)
        for num in range(1, len(self.objects)):
            offsets[num] = len(out)
            out += ('%d 0 obj\n' % num).encode('latin-1') + self.objects[num] + b'\nendobj\n'
        xref = len(out)
        out += ('xref\n0 %d\n' % len(self.objects)).encode('latin-1')
        out += b'0000000000 65535 f \n'
        for num in range(1, len(self.objects)):
            out += ('%010d 00000 n \n' % offsets[num]).encode('latin-1')
        out += ('trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n'
                % (len(self.objects), catalog, info, xref)).encode('latin-1')

        with open(path, 'wb') as fh:
            fh.write(bytes(out))

    # --- составные объекты ---

    def _write_font(self, font):
        ttf = font.ttf
        gids = sorted(font.used)
        subset = ttf.subset(gids)
        scale = 1000.0 / ttf.upem
        ff = self._obj(self._stream('/Length1 %d' % len(subset), subset))
        tag = _subset_tag(font.res_name + ttf.base_name)
        name = '%s+%s' % (tag, ttf.base_name)
        desc = self._obj((
            '<< /Type /FontDescriptor /FontName /%s /Flags 4 '
            '/FontBBox [%d %d %d %d] /ItalicAngle %s /Ascent %d /Descent %d '
            '/CapHeight %d /StemV %d /FontFile2 %d 0 R >>'
            % (name, int(ttf.xmin * scale), int(ttf.ymin * scale),
               int(ttf.xmax * scale), int(ttf.ymax * scale), _num(ttf.italic_angle),
               int(ttf.ascent * scale), int(ttf.descent * scale),
               int(ttf.cap_height * scale), ttf.stem_v, ff)).encode('latin-1'))

        widths, run, start = [], [], None
        for gid in gids:
            w = int(round(ttf.glyph_width(gid)))
            if start is not None and gid == start + len(run):
                run.append(w)
            else:
                if run:
                    widths.append('%d [%s]' % (start, ' '.join(str(v) for v in run)))
                start, run = gid, [w]
        if run:
            widths.append('%d [%s]' % (start, ' '.join(str(v) for v in run)))

        cid = self._obj((
            '<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s '
            '/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> '
            '/FontDescriptor %d 0 R /DW 1000 /W [%s] /CIDToGIDMap /Identity >>'
            % (name, desc, ' '.join(widths))).encode('latin-1'))
        tounicode = self._obj(self._stream('', _tounicode_cmap(font.used)))
        return self._obj((
            '<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H '
            '/DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>'
            % (name, cid, tounicode)).encode('latin-1'))

    def _write_image(self, parts):
        w, h, cs, bpc, rgb, alpha = parts
        smask = None
        if alpha:
            smask = self._obj(self._stream(
                '/Type /XObject /Subtype /Image /Width %d /Height %d '
                '/ColorSpace /DeviceGray /BitsPerComponent 8' % (w, h), alpha))
        extra = ('/Type /XObject /Subtype /Image /Width %d /Height %d '
                 '/ColorSpace /%s /BitsPerComponent %d' % (w, h, cs, bpc))
        if smask:
            extra += ' /SMask %d 0 R' % smask
        return self._obj(self._stream(extra, rgb))


def _pdf_text(s):
    """Строка в PDF: UTF-16BE с BOM, чтобы не терять кириллицу."""
    data = b'\xfe\xff' + (s or '').encode('utf-16-be')
    return '<' + data.hex().upper() + '>'


def _subset_tag(seed):
    h = 0
    for ch in seed:
        h = (h * 131 + ord(ch)) & 0xFFFFFF
    letters = ''
    for _ in range(6):
        letters += chr(ord('A') + h % 26)
        h //= 26
    return letters


def _tounicode_cmap(used):
    lines = []
    for gid, ch in sorted(used.items()):
        lines.append('<%04X> <%s>' % (gid, ch.encode('utf-16-be').hex().upper()))
    body = []
    for i in range(0, len(lines), 100):
        chunk = lines[i:i + 100]
        body.append('%d beginbfchar\n%s\nendbfchar' % (len(chunk), '\n'.join(chunk)))
    return ('/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n'
            '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
            '/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n'
            '1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n'
            + '\n'.join(body) +
            '\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend'
            ).encode('latin-1')


# --------------------------------------------------------------------------
#  PNG -> данные для XObject
# --------------------------------------------------------------------------

def _png_parts(data):
    """PNG -> (ширина, высота, цветовое пространство, бит/канал, пиксели, маска).

    Пиксели и маска возвращаются несжатыми: сжатие делает сам поток PDF.
    """
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('не PNG')
    pos, idat, palette, trns = 8, bytearray(), None, None
    w = h = bpc = ctype = interlace = 0
    while pos < len(data):
        ln = struct.unpack('>I', data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b'IHDR':
            w, h, bpc, ctype, _comp, _filt, interlace = struct.unpack('>IIBBBBB', body)
        elif typ == b'IDAT':
            idat += body
        elif typ == b'PLTE':
            palette = body
        elif typ == b'tRNS':
            trns = body
        elif typ == b'IEND':
            break
        pos += 12 + ln
    if interlace:
        raise ValueError('чересстрочный PNG не поддерживается')
    if bpc != 8:
        raise ValueError('поддерживается только 8 бит на канал')

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    rows = _png_unfilter(raw, w, h, channels)

    alpha = None
    if ctype == 6 or ctype == 4:
        colour = bytearray()
        mask = bytearray()
        cn = channels - 1
        for row in rows:
            for i in range(w):
                px = row[i * channels:(i + 1) * channels]
                colour += px[:cn]
                mask.append(px[cn])
        cs = 'DeviceRGB' if cn == 3 else 'DeviceGray'
        return (w, h, cs, 8, bytes(colour), bytes(mask))

    if ctype == 3:
        colour = bytearray()
        mask = bytearray() if trns else None
        for row in rows:
            for i in range(w):
                idx = row[i]
                colour += palette[idx * 3:idx * 3 + 3]
                if mask is not None:
                    mask.append(trns[idx] if idx < len(trns) else 255)
        return (w, h, 'DeviceRGB', 8, bytes(colour),
                bytes(mask) if mask is not None else None)

    flat = bytearray()
    for row in rows:
        flat += row
    cs = 'DeviceRGB' if ctype == 2 else 'DeviceGray'
    return (w, h, cs, 8, bytes(flat), None)


def _png_unfilter(raw, w, h, channels):
    stride = w * channels
    rows, prev, pos = [], bytearray(stride), 0
    for _ in range(h):
        ft = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ft == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(line)
        prev = line
    return rows
