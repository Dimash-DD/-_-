#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОВ-Проверка, версия 0.1 (прототип)

Проверка №1: подпись сечения воздуховода против нарисованной ширины.
  Находит подписи вида 400х200, 400x200, Ø160 (%%c160) в TEXT / MTEXT /
  ATTRIB / MULTILEADER, определяет, к какому воздуховоду они относятся,
  измеряет расстояние между его стенками на плане и сравнивает.

Запуск:
    python ov_check.py план.dxf
    python ov_check.py план.dxf --tol 5 --layers "возд|ОВ"

На выходе рядом с чертежом:
    <имя>_отчет.html     отчёт с планом и списком замечаний
    <имя>_отчет.csv      то же для Excel
    <имя>_ПРОВЕРКА.dxf   копия чертежа, замечания на слое ПРОВЕРКА

Работает только со стандартной библиотекой Python 3.8+.
DWG нужно сохранить как DXF (в AutoCAD/nanoCAD: Сохранить как -> DXF).
"""

import argparse
import csv
import html
import json
import math
import os
import re
import sys

VERSION = "0.1"

# ---------------------------------------------------------------- настройки

DEFAULTS = {
    # слои воздуховодов (регулярное выражение, без учёта регистра)
    "duct_layers": r"возд|вент|duct|vent|(?<![а-яa-z])ов(?![а-яa-z])",
    # допуск по размеру, единиц чертежа (мм)
    "tol": 5.0,
    # радиус поиска воздуховода вокруг подписи
    "search_radius": 1500.0,
    # подписи с этими словами не проверяются (решётки, клапаны и т.п.)
    "skip_words": r"АМН|АДН|АРН|АДР|решет|диффуз|ДПУ|ДКУ|клапан|КВК|КПУ|КЛОП|"
                  r"фильтр|шумоглуш|вентилятор|L\s*=",
    # типы линий, которые не являются стенками воздуховода (оси)
    "axis_linetypes": r"center|осев|dashdot|штрихпункт|axis|phantom",
    # разумные пределы ширины воздуховода
    "min_width": 50.0,
    "max_width": 3000.0,
}

# ---------------------------------------------------------------- чтение DXF


class DXF:
    """Минимальный разбор ASCII DXF: секции, таблица слоёв, примитивы."""

    def __init__(self, path):
        raw = open(path, "rb").read()
        if raw.startswith(b"AutoCAD Binary DXF"):
            raise SystemExit("Это бинарный DXF. Сохраните чертёж как ASCII DXF.")
        if raw.startswith(b"AC10"):
            raise SystemExit("Это файл DWG. Сохраните его как DXF (Сохранить как -> DXF).")
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
            self.encoding = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("cp1251", errors="replace")
            self.encoding = "cp1251"
        self.newline = "\r\n" if "\r\n" in text[:2000] else "\n"
        lines = text.splitlines()
        if len(lines) % 2:
            lines = lines[:-1]
        self.tags = []
        for i in range(0, len(lines), 2):
            try:
                code = int(lines[i].strip())
            except ValueError:
                raise SystemExit(f"Не удалось разобрать DXF (строка {i + 1}).")
            self.tags.append((code, lines[i + 1]))
        self._split()

    def _split(self):
        """Делит теги на секции и примитивы."""
        self.header = {}
        self.layers = {}            # имя -> {'ltype':..., 'color':...}
        self.entities = []          # (тип, [теги]) из секции ENTITIES
        self.ent_section = None     # (индекс начала, индекс ENDSEC) в self.tags
        section = None
        i, n = 0, len(self.tags)
        cur = None
        while i < n:
            code, val = self.tags[i]
            v = val.strip()
            if code == 0 and v == "SECTION":
                section = self.tags[i + 1][1].strip() if i + 1 < n else None
                sec_start = i
                i += 2
                continue
            if code == 0 and v == "ENDSEC":
                if cur is not None:
                    self._store(section, cur)
                    cur = None
                if section == "ENTITIES":
                    self.ent_section = (sec_start, i)
                section = None
                i += 1
                continue
            if section == "HEADER":
                if code == 9:
                    name = v
                    vals = []
                    j = i + 1
                    while j < n and self.tags[j][0] not in (0, 9):
                        vals.append(self.tags[j])
                        j += 1
                    self.header[name] = vals
                    i = j
                    continue
            elif section in ("TABLES", "ENTITIES"):
                if code == 0:
                    if cur is not None:
                        self._store(section, cur)
                    cur = (v, [], i)
                elif cur is not None:
                    cur[1].append((code, val))
            i += 1

    def _store(self, section, ent):
        etype, tags, idx = ent
        if section == "TABLES" and etype == "LAYER":
            d = tagdict(tags)
            name = d.get(2, "").strip()
            if name:
                self.layers[name.lower()] = {
                    "name": name,
                    "ltype": d.get(6, "CONTINUOUS").strip(),
                    "color": int(d.get(62, "7")),
                }
        elif section == "ENTITIES":
            self.entities.append((etype, tags, idx))

    @property
    def version(self):
        v = self.header.get("$ACADVER")
        return v[0][1].strip() if v else "AC1009"

    @property
    def is_r12(self):
        return self.version <= "AC1009"

    @property
    def units_name(self):
        v = self.header.get("$INSUNITS")
        code = int(v[0][1]) if v else 4
        return {1: "дюймы", 2: "футы", 4: "мм", 5: "см", 6: "м"}.get(code, "мм")


def tagdict(tags):
    """Первое значение для каждого кода."""
    d = {}
    for c, v in tags:
        if c not in d:
            d[c] = v
    return d


def f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- геометрия

class Seg:
    __slots__ = ("x1", "y1", "x2", "y2", "layer", "len", "ux", "uy")

    def __init__(self, x1, y1, x2, y2, layer):
        self.x1, self.y1, self.x2, self.y2, self.layer = x1, y1, x2, y2, layer
        dx, dy = x2 - x1, y2 - y1
        self.len = math.hypot(dx, dy)
        self.ux, self.uy = (dx / self.len, dy / self.len) if self.len else (1.0, 0.0)


def point_seg_dist(px, py, s):
    if s.len == 0:
        return math.hypot(px - s.x1, py - s.y1)
    t = ((px - s.x1) * s.ux + (py - s.y1) * s.uy)
    t = max(0.0, min(s.len, t))
    return math.hypot(px - (s.x1 + s.ux * t), py - (s.y1 + s.uy * t))


# ---------------------------------------------------------------- тексты

MTEXT_CODES = re.compile(r"\\[ACFHQTWfcpa][^;\\{}]*;|\\[LlOoKkNX]|\\S[^;]*;")
UNICODE_ESC = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def clean_text(s):
    s = UNICODE_ESC.sub(lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace("\\P", " ").replace("\\~", " ")
    s = MTEXT_CODES.sub("", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"%%[cC]", "Ø", s)
    s = re.sub(r"%%[dD]", "°", s)
    s = re.sub(r"%%[pP]", "±", s)
    s = re.sub(r"%%[uUoO]", "", s)
    return " ".join(s.split())


RE_RECT = re.compile(r"(?<![\d.,])(\d{2,4})\s*[xXхХ×*]\s*(\d{2,4})(?![\d.,])")
RE_ROUND = re.compile(r"[Ø∅⌀]\s*(\d{2,4})(?![\d.,])")


# ---------------------------------------------------------------- извлечение

def extract(dxf, cfg):
    """Возвращает (отрезки воздуховодов, все отрезки для картинки, подписи, выноски)."""
    duct_re = re.compile(cfg["duct_layers"], re.I)
    axis_re = re.compile(cfg["axis_linetypes"], re.I)

    layer_names = {k: v["name"] for k, v in dxf.layers.items()}
    used_layers = set()
    for etype, tags, _ in dxf.entities:
        lay = tagdict(tags).get(8, "0").strip()
        used_layers.add(lay)
    duct_layers = {l for l in used_layers if duct_re.search(l)}
    notes = []
    if not duct_layers:
        notes.append("Слои воздуховодов не найдены по шаблону, анализирую все слои.")

    def is_duct(layer):
        return (not duct_layers) or layer in duct_layers

    def ltype_of(d, layer):
        lt = d.get(6, "BYLAYER").strip()
        if lt.upper() == "BYLAYER":
            lt = dxf.layers.get(layer.lower(), {}).get("ltype", "CONTINUOUS")
        return lt

    segs, all_segs, labels, leaders = [], [], [], []

    ents = dxf.entities
    i = 0
    while i < len(ents):
        etype, tags, idx = ents[i]
        d = tagdict(tags)
        layer = d.get(8, "0").strip()
        paper = d.get(67, "0").strip() == "1"
        i += 1
        if paper:
            continue

        pts, closed = None, False
        if etype == "LINE":
            pts = [(f(d.get(10)), f(d.get(20))), (f(d.get(11)), f(d.get(21)))]
        elif etype == "LWPOLYLINE":
            pts = []
            x = None
            for c, v in tags:
                if c == 10:
                    x = f(v)
                elif c == 20 and x is not None:
                    pts.append((x, f(v)))
                    x = None
            closed = int(d.get(70, "0")) & 1 == 1
        elif etype == "POLYLINE":
            flags = int(d.get(70, "0"))
            closed = flags & 1 == 1
            pts = []
            while i < len(ents) and ents[i][0] in ("VERTEX", "SEQEND"):
                if ents[i][0] == "VERTEX":
                    vd = tagdict(ents[i][1])
                    pts.append((f(vd.get(10)), f(vd.get(20))))
                i += 1
            if flags & (16 | 64):   # сети и многогранники пропускаем
                pts = None
        elif etype in ("TEXT", "ATTRIB", "MTEXT"):
            lab = text_label(etype, tags, d, layer)
            if lab:
                labels.append(lab)
        elif etype in ("MULTILEADER", "MLEADER"):
            lab = mleader_label(tags, layer)
            if lab:
                labels.append(lab)
        elif etype == "LEADER":
            vs = []
            x = None
            for c, v in tags:
                if c == 10:
                    x = f(v)
                elif c == 20 and x is not None:
                    vs.append((x, f(v)))
                    x = None
            if len(vs) >= 2:
                leaders.append({"pts": vs, "handle": d.get(5, "").strip(),
                                "assoc": d.get(340, "").strip()})

        if pts and len(pts) >= 2:
            lt = ltype_of(d, layer)
            pairs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if closed else [])
            for (x1, y1), (x2, y2) in pairs:
                s = Seg(x1, y1, x2, y2, layer)
                if s.len == 0:
                    continue
                all_segs.append((s, is_duct(layer)))
                if is_duct(layer) and not axis_re.search(lt) and s.len >= 100:
                    segs.append(s)

    return segs, all_segs, labels, leaders, sorted(duct_layers), notes


def text_label(etype, tags, d, layer):
    if etype == "MTEXT":
        chunks = [v for c, v in tags if c == 3] + [d.get(1, "")]
        raw = "".join(chunks)
    else:
        raw = d.get(1, "")
    txt = clean_text(raw)
    if not (RE_RECT.search(txt) or RE_ROUND.search(txt)):
        return None
    h = f(d.get(40), 250.0) or 250.0
    x, y = f(d.get(10)), f(d.get(20))
    rot = f(d.get(50))
    if etype == "MTEXT" and 11 in d:
        rot = math.degrees(math.atan2(f(d.get(21)), f(d.get(11))))
    if etype != "MTEXT" and (int(d.get(72, "0")) or int(d.get(73, "0"))) and 11 in d:
        x, y = f(d.get(11)), f(d.get(21))
        just = "center"
    else:
        just = "left"
    if etype == "MTEXT":
        ap = int(d.get(71, "1"))
        just = {1: "left", 4: "left", 7: "left"}.get(ap, "center")
    # центр текста (приблизительно)
    w = 0.75 * h * len(txt)
    a = math.radians(rot)
    if just == "left":
        cx = x + math.cos(a) * w / 2 - math.sin(a) * h / 2
        cy = y + math.sin(a) * w / 2 + math.cos(a) * h / 2
    else:
        cx, cy = x, y
    return {"text": txt, "x": x, "y": y, "cx": cx, "cy": cy, "h": h, "rot": rot,
            "layer": layer, "handle": d.get(5, "").strip(), "arrow": None}


def mleader_label(tags, layer):
    texts, arrow, loc = [], None, None
    in_line = False
    x = None
    for c, v in tags:
        s = v.strip()
        if c == 304 and s == "LEADER_LINE{":
            in_line = True
            continue
        if c == 305 and s == "}":
            in_line = False
            continue
        if c == 304 and not s.endswith("{"):
            texts.append(v)
        if c == 12:
            x12 = f(v)
        if c == 22 and loc is None:
            loc = (x12, f(v))
        if in_line and arrow is None:
            if c == 10:
                x = f(v)
            elif c == 20 and x is not None:
                arrow = (x, f(v))
    txt = clean_text(" ".join(texts))
    if not (RE_RECT.search(txt) or RE_ROUND.search(txt)) or loc is None:
        return None
    return {"text": txt, "x": loc[0], "y": loc[1], "cx": loc[0], "cy": loc[1],
            "h": 250.0, "rot": 0.0, "layer": layer, "handle": "", "arrow": arrow}


# ---------------------------------------------------------------- анализ

def attach_leaders(labels, leaders):
    """Для простого текста ищет выноску LEADER, которая к нему подходит."""
    by_handle = {l["assoc"]: l for l in leaders if l["assoc"]}
    for lab in labels:
        if lab["arrow"]:
            continue
        ld = by_handle.get(lab["handle"]) if lab["handle"] else None
        if ld is None:
            best, bd = None, None
            for l in leaders:
                ex, ey = l["pts"][-1]
                dist = min(math.hypot(ex - lab["x"], ey - lab["y"]),
                           math.hypot(ex - lab["cx"], ey - lab["cy"]))
                if dist <= 3 * lab["h"] and (bd is None or dist < bd):
                    best, bd = l, dist
            ld = best
        if ld:
            lab["arrow"] = ld["pts"][0]


def find_duct(px, py, segs, cfg, prefer_angle=None, strict=False):
    """Ищет пару параллельных стенок воздуховода возле точки (px, py)."""
    R = cfg["search_radius"] if not strict else max(300.0, cfg["search_radius"] / 3)
    near = [s for s in segs if point_seg_dist(px, py, s) <= R]
    near.sort(key=lambda s: point_seg_dist(px, py, s))
    near = near[:120]
    sin_tol = math.sin(math.radians(1.5))
    best = None
    for a in range(len(near)):
        s1 = near[a]
        for b in range(a + 1, len(near)):
            s2 = near[b]
            if abs(s1.ux * s2.uy - s1.uy * s2.ux) > sin_tol:
                continue
            # расстояние между прямыми
            d1 = (s2.x1 - s1.x1) * -s1.uy + (s2.y1 - s1.y1) * s1.ux
            d2 = (s2.x2 - s1.x1) * -s1.uy + (s2.y2 - s1.y1) * s1.ux
            width = abs((d1 + d2) / 2)
            if not (cfg["min_width"] <= width <= cfg["max_width"]):
                continue
            # перекрытие по длине
            t1 = sorted([0.0, s1.len])
            p = sorted([(s2.x1 - s1.x1) * s1.ux + (s2.y1 - s1.y1) * s1.uy,
                        (s2.x2 - s1.x1) * s1.ux + (s2.y2 - s1.y1) * s1.uy])
            lo, hi = max(t1[0], p[0]), min(t1[1], p[1])
            overlap = hi - lo
            if overlap < min(0.3 * min(s1.len, s2.len), 2 * width):
                continue
            # положение точки относительно пары
            tp = (px - s1.x1) * s1.ux + (py - s1.y1) * s1.uy
            sp = (px - s1.x1) * -s1.uy + (py - s1.y1) * s1.ux
            mid = (d1 + d2) / 2
            inside = (0 <= sp <= mid) if mid > 0 else (mid <= sp <= 0)
            off_side = 0.0 if inside else min(abs(sp), abs(sp - mid))
            off_len = 0.0 if lo <= tp <= hi else min(abs(tp - lo), abs(tp - hi))
            score = off_side + off_len
            if prefer_angle is not None:
                ang = math.degrees(math.atan2(s1.uy, s1.ux))
                diff = abs((ang - prefer_angle + 90) % 180 - 90)
                if diff > 5:
                    score += R / 2
            # между стенками не должно быть третьей параллельной линии
            for s3 in near:
                if s3 is s1 or s3 is s2:
                    continue
                if abs(s1.ux * s3.uy - s1.uy * s3.ux) > sin_tol:
                    continue
                d3 = ((s3.x1 + s3.x2) / 2 - s1.x1) * -s1.uy + ((s3.y1 + s3.y2) / 2 - s1.y1) * s1.ux
                if min(0, mid) + 1 < d3 < max(0, mid) - 1:
                    t3 = ((s3.x1 + s3.x2) / 2 - s1.x1) * s1.ux + ((s3.y1 + s3.y2) / 2 - s1.y1) * s1.uy
                    if lo <= t3 <= hi:
                        score += R
                        break
            if score > R:
                continue
            if best is None or score < best[0] or (abs(score - best[0]) < 1 and width < best[1]):
                tcl = max(lo, min(hi, tp))
                mx = s1.x1 + s1.ux * tcl + -s1.uy * mid / 2
                my = s1.y1 + s1.uy * tcl + s1.ux * mid / 2
                best = (score, width, (mx, my), s1, s2)
    return best


def check_sections(dxf, cfg):
    segs, all_segs, labels, leaders, duct_layers, notes = extract(dxf, cfg)
    attach_leaders(labels, leaders)
    skip_re = re.compile(cfg["skip_words"], re.I)
    tol = cfg["tol"]
    results = []
    for lab in labels:
        txt = lab["text"]
        if skip_re.search(txt):
            continue
        sizes = [("rect", int(m.group(1)), int(m.group(2))) for m in RE_RECT.finditer(txt)]
        sizes += [("round", int(m.group(1)), None) for m in RE_ROUND.finditer(txt)]
        if not sizes:
            continue
        kind, w, h = sizes[0]
        if lab["arrow"]:
            ax, ay = lab["arrow"]
            found = find_duct(ax, ay, segs, cfg, strict=True)
            via = "выноска"
        else:
            ax, ay = lab["cx"], lab["cy"]
            found = find_duct(ax, ay, segs, cfg, prefer_angle=lab["rot"])
            via = "текст рядом"
        r = {"text": txt, "kind": kind, "w": w, "h": h, "layer": lab["layer"],
             "tx": lab["x"], "ty": lab["y"], "via": via}
        label_str = f"{w}×{h}" if kind == "rect" else f"Ø{w}"
        if not found:
            r.update(status="notfound", measured=None, mx=ax, my=ay,
                     msg=f"Рядом с подписью {label_str} не найден воздуховод")
        else:
            _, width, (mx, my), _, _ = found
            width = round(width, 1)
            r.update(measured=width, mx=mx, my=my)
            if abs(width - w) <= tol:
                r.update(status="ok", msg=f"{label_str}: на чертеже {width:g}, совпадает")
            elif kind == "rect" and h is not None and abs(width - h) <= tol:
                r.update(status="warn",
                         msg=f"Подпись {label_str}, на плане ширина {width:g}. "
                             f"Возможно перепутаны ширина и высота")
            else:
                r.update(status="error",
                         msg=f"Подпись {label_str}, а нарисовано {width:g} "
                             f"(разница {width - w:+g})")
        results.append(r)
    order = {"error": 0, "warn": 1, "notfound": 2, "ok": 3}
    results.sort(key=lambda r: (order[r["status"]], -r["my"], r["mx"]))
    for k, r in enumerate(results, 1):
        r["n"] = k
    return results, all_segs, duct_layers, notes, len(labels)


# ---------------------------------------------------------------- DXF с отметками

def write_marked(dxf, results, out_path):
    issues = [r for r in results if r["status"] != "ok"]
    color = {"error": 1, "warn": 30, "notfound": 5}
    nl = dxf.newline
    tags = list(dxf.tags)
    new_ents = []

    if dxf.is_r12:
        for r in issues:
            rad = max(400.0, (r["measured"] or r["w"]) * 1.2)
            c = color[r["status"]]
            new_ents += [(0, "CIRCLE"), (8, "ПРОВЕРКА"), (62, str(c)),
                         (10, f"{r['mx']:.3f}"), (20, f"{r['my']:.3f}"), (30, "0.0"),
                         (40, f"{rad:.3f}")]
            new_ents += [(0, "TEXT"), (8, "ПРОВЕРКА"), (62, str(c)),
                         (10, f"{r['mx'] + rad * 0.75:.3f}"), (20, f"{r['my'] + rad * 0.75:.3f}"),
                         (30, "0.0"), (40, f"{rad * 0.6:.3f}"), (1, f"!{r['n']}")]
        layer_rec = [(0, "LAYER"), (2, "ПРОВЕРКА"), (70, "0"), (62, "1"), (6, "CONTINUOUS")]
    else:
        # R2000+: нужны уникальные дескрипторы и владелец
        seed_tags = dxf.header.get("$HANDSEED")
        seed = int(seed_tags[0][1].strip(), 16) if seed_tags else 0x10000

        def new_handle():
            nonlocal seed
            h = format(seed, "X")
            seed += 1
            return h

        ms_handle, layer_tbl_handle, layer0 = find_owners(dxf)
        for r in issues:
            rad = max(400.0, (r["measured"] or r["w"]) * 1.2)
            c = color[r["status"]]
            new_ents += [(0, "CIRCLE"), (5, new_handle()), (330, ms_handle),
                         (100, "AcDbEntity"), (8, "ПРОВЕРКА"), (62, str(c)),
                         (100, "AcDbCircle"), (10, f"{r['mx']:.3f}"), (20, f"{r['my']:.3f}"),
                         (30, "0.0"), (40, f"{rad:.3f}")]
            new_ents += [(0, "TEXT"), (5, new_handle()), (330, ms_handle),
                         (100, "AcDbEntity"), (8, "ПРОВЕРКА"), (62, str(c)),
                         (100, "AcDbText"), (10, f"{r['mx'] + rad * 0.75:.3f}"),
                         (20, f"{r['my'] + rad * 0.75:.3f}"), (30, "0.0"),
                         (40, f"{rad * 0.6:.3f}"), (1, f"!{r['n']}"), (100, "AcDbText")]
        layer_rec = None
        if layer0 is not None:
            layer_rec = []
            for c, v in layer0:
                if c == 5:
                    v = new_handle()
                elif c == 2:
                    v = "ПРОВЕРКА"
                elif c == 62:
                    v = "1"
                elif c == 6:
                    v = "Continuous"
                layer_rec.append((c, v))
        if seed_tags:
            hidx = header_tag_index(tags, "$HANDSEED")
            if hidx is not None:
                tags[hidx] = (5, format(seed, "X"))

    # вставка примитивов перед ENDSEC секции ENTITIES
    if dxf.ent_section is None:
        raise SystemExit("В чертеже нет секции ENTITIES.")
    end_idx = dxf.ent_section[1]
    tags = tags[:end_idx] + new_ents + tags[end_idx:]

    # слой в таблицу слоёв (если такого ещё нет)
    if layer_rec and "проверка" not in dxf.layers:
        tags = insert_layer(tags, layer_rec)

    with open(out_path, "w", encoding=dxf.encoding, errors="replace", newline="") as fo:
        for c, v in tags:
            fo.write(f"{c:>3}{nl}{v}{nl}")
    return len(issues)


def header_tag_index(tags, var):
    for i, (c, v) in enumerate(tags):
        if c == 9 and v.strip() == var:
            return i + 1
        if c == 0 and v.strip() == "ENDSEC":
            return None
    return None


def find_owners(dxf):
    """Дескрипторы *Model_Space, таблицы слоёв и запись слоя 0."""
    ms, ltbl, layer0 = "1F", None, None
    tags = dxf.tags
    in_tbl = None
    for i, (c, v) in enumerate(tags):
        if c == 0 and v.strip() == "TABLE":
            in_tbl = tags[i + 1][1].strip() if i + 1 < len(tags) else None
            if in_tbl == "LAYER":
                for c2, v2 in tags[i + 1:i + 8]:
                    if c2 == 5:
                        ltbl = v2.strip()
                        break
        if c == 0 and v.strip() == "BLOCK_RECORD":
            j, h, name = i + 1, None, None
            while j < len(tags) and tags[j][0] != 0:
                if tags[j][0] == 5:
                    h = tags[j][1].strip()
                if tags[j][0] == 2:
                    name = tags[j][1].strip()
                j += 1
            if name and name.upper() == "*MODEL_SPACE" and h:
                ms = h
        if c == 0 and v.strip() == "LAYER" and in_tbl == "LAYER":
            j, rec = i + 1, [(0, "LAYER")]
            while j < len(tags) and tags[j][0] != 0:
                rec.append(tags[j])
                j += 1
            if tagdict(rec[1:]).get(2, "").strip() == "0" and layer0 is None:
                layer0 = rec
    return ms, ltbl, layer0


def insert_layer(tags, rec):
    in_layer_tbl = False
    for i, (c, v) in enumerate(tags):
        if c == 0 and v.strip() == "TABLE" and i + 1 < len(tags) and tags[i + 1][1].strip() == "LAYER":
            in_layer_tbl = True
        elif in_layer_tbl and c == 70:
            # счётчик записей в заголовке таблицы
            try:
                tags[i] = (70, str(int(v) + 1))
            except ValueError:
                pass
            in_layer_tbl = "count_done"
        if in_layer_tbl and c == 0 and v.strip() == "ENDTAB":
            return tags[:i] + rec + tags[i:]
    return tags


# ---------------------------------------------------------------- отчёты

def write_csv(results, path):
    status_ru = {"error": "Ошибка", "warn": "Внимание", "notfound": "Не найдено", "ok": "ОК"}
    with open(path, "w", encoding="utf-8-sig", newline="") as fo:
        w = csv.writer(fo, delimiter=";")
        w.writerow(["№", "Статус", "Подпись", "Размер по подписи", "Нарисовано",
                    "Замечание", "Слой подписи", "X", "Y"])
        for r in results:
            lab = f"{r['w']}x{r['h']}" if r["kind"] == "rect" else f"Ø{r['w']}"
            w.writerow([r["n"], status_ru[r["status"]], r["text"], lab,
                        "" if r["measured"] is None else f"{r['measured']:g}",
                        r["msg"], r["layer"], f"{r['mx']:.0f}", f"{r['my']:.0f}"])


def write_html(results, all_segs, info, path):
    xs = [v for s, _ in all_segs for v in (s.x1, s.x2)] + [r["mx"] for r in results]
    ys = [v for s, _ in all_segs for v in (s.y1, s.y2)] + [r["my"] for r in results]
    if not xs:
        xs, ys = [0, 1000], [0, 1000]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = max(maxx - minx, maxy - miny) * 0.03 + 500
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    W, H = maxx - minx, maxy - miny
    sw = max(W, H) / 1400

    lines = []
    for s, duct in all_segs:
        cls = "d" if duct else "o"
        lines.append(f'<line class="{cls}" x1="{s.x1:.0f}" y1="{-s.y1:.0f}" '
                     f'x2="{s.x2:.0f}" y2="{-s.y2:.0f}"/>')
    marks = []
    for r in results:
        rad = max(400.0, (r["measured"] or r["w"]) * 1.2)
        marks.append(
            f'<g class="m {r["status"]}" data-n="{r["n"]}">'
            f'<circle cx="{r["mx"]:.0f}" cy="{-r["my"]:.0f}" r="{rad:.0f}"/>'
            f'<text x="{r["mx"] + rad * 0.8:.0f}" y="{-r["my"] - rad * 0.8:.0f}" '
            f'font-size="{rad * 0.9:.0f}">{r["n"]}</text></g>')

    status_ru = {"error": "Ошибка", "warn": "Внимание", "notfound": "Не найдено", "ok": "ОК"}
    rows = []
    for r in results:
        meas = "—" if r["measured"] is None else format(r["measured"], "g")
        rows.append(
            f'<tr class="{r["status"]}" data-n="{r["n"]}" data-x="{r["mx"]:.0f}" '
            f'data-y="{-r["my"]:.0f}"><td>{r["n"]}</td>'
            f'<td><span class="tag {r["status"]}">{status_ru[r["status"]]}</span></td>'
            f'<td>{html.escape(r["text"])}</td>'
            f'<td>{meas}</td>'
            f'<td>{html.escape(r["msg"])}</td></tr>')

    cnt = {k: sum(1 for r in results if r["status"] == k) for k in status_ru}
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in info["notes"])

    page = HTML_TEMPLATE
    repl = {
        "{{FILE}}": html.escape(info["file"]),
        "{{VER}}": VERSION,
        "{{DXFVER}}": html.escape(info["dxfver"]),
        "{{UNITS}}": html.escape(info["units"]),
        "{{LAYERS}}": html.escape(", ".join(info["duct_layers"]) or "все слои"),
        "{{NLAB}}": str(info["nlabels"]),
        "{{TOL}}": f"{info['tol']:g}",
        "{{E}}": str(cnt["error"]), "{{W}}": str(cnt["warn"]),
        "{{N}}": str(cnt["notfound"]), "{{OK}}": str(cnt["ok"]),
        "{{NOTES}}": f"<ul class='notes'>{notes}</ul>" if notes else "",
        "{{VB}}": f"{minx:.0f} {-maxy:.0f} {W:.0f} {H:.0f}",
        "{{SW}}": f"{sw:.1f}",
        "{{LINES}}": "\n".join(lines),
        "{{MARKS}}": "\n".join(marks),
        "{{ROWS}}": "\n".join(rows) or "<tr><td colspan=5>Подписи сечений не найдены</td></tr>",
    }
    for k, v in repl.items():
        page = page.replace(k, v)
    with open(path, "w", encoding="utf-8") as fo:
        fo.write(page)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ОВ-Проверка: {{FILE}}</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1f23;--mute:#6b6f76;--line:#e3e1dc;
--err:#c8372d;--warn:#d98a00;--nf:#3b6fd6;--ok:#2f8a4c;--duct:#2b6c8f;--oth:#b9b6ae}
@media (prefers-color-scheme:dark){:root{--bg:#16181b;--card:#1f2226;--ink:#e8e6e1;
--mute:#9a9ea5;--line:#33373c;--duct:#6fb3d6;--oth:#4a4e54}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:20px 24px 8px}h1{font-size:20px;margin:0 0 4px}
.meta{color:var(--mute);font-size:13px}
.sum{display:flex;gap:10px;flex-wrap:wrap;padding:8px 24px 16px}
.k{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:120px}
.k b{display:block;font-size:24px}.k.error b{color:var(--err)}.k.warn b{color:var(--warn)}
.k.notfound b{color:var(--nf)}.k.ok b{color:var(--ok)}
.wrap{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:16px;padding:0 24px 24px}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.card h2{font-size:14px;margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
svg{width:100%;height:70vh;display:block;background:var(--card);cursor:grab}
line{stroke-width:{{SW}};vector-effect:non-scaling-stroke}
line.d{stroke:var(--duct);stroke-width:1.4px}line.o{stroke:var(--oth);stroke-width:1px}
.m circle{fill:none;stroke-width:2.5px;vector-effect:non-scaling-stroke}
.m text{font-weight:700;font-family:system-ui}
.m.error circle{stroke:var(--err)}.m.error text{fill:var(--err)}
.m.warn circle{stroke:var(--warn)}.m.warn text{fill:var(--warn)}
.m.notfound circle{stroke:var(--nf)}.m.notfound text{fill:var(--nf)}
.m.ok circle{stroke:var(--ok);opacity:.35}.m.ok text{fill:var(--ok);opacity:.5}
.m.hl circle{stroke-width:5px;opacity:1}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr{cursor:pointer}tr:hover{background:color-mix(in srgb,var(--ink) 5%,transparent)}
.tag{font-size:11px;font-weight:600;padding:2px 7px;border-radius:99px;white-space:nowrap;color:#fff}
.tag.error{background:var(--err)}.tag.warn{background:var(--warn)}
.tag.notfound{background:var(--nf)}.tag.ok{background:var(--ok)}
.scroll{max-height:70vh;overflow:auto}
.notes{margin:0 24px 12px;color:var(--warn)}
.tools{float:right;font-weight:400}.tools button{font:inherit;font-size:12px;border:1px solid var(--line);
background:var(--bg);color:var(--ink);border-radius:6px;padding:2px 8px;cursor:pointer}
footer{color:var(--mute);font-size:12px;padding:0 24px 24px}
</style></head><body>
<header><h1>Проверка сечений воздуховодов</h1>
<div class="meta">Файл: <b>{{FILE}}</b> · DXF {{DXFVER}} · единицы: {{UNITS}} · допуск ±{{TOL}} ·
слои воздуховодов: {{LAYERS}} · найдено подписей с размерами: {{NLAB}}</div></header>
<div class="sum">
<div class="k error"><b>{{E}}</b>ошибок</div><div class="k warn"><b>{{W}}</b>внимание</div>
<div class="k notfound"><b>{{N}}</b>не найден воздуховод</div><div class="k ok"><b>{{OK}}</b>совпадает</div></div>
{{NOTES}}
<div class="wrap">
<div class="card"><h2>План <span class="tools"><button id="fit">Весь план</button></span></h2>
<svg id="plan" viewBox="{{VB}}" preserveAspectRatio="xMidYMid meet">
<g>{{LINES}}</g><g>{{MARKS}}</g></svg></div>
<div class="card"><h2>Замечания (нажмите строку, чтобы показать на плане)</h2>
<div class="scroll"><table><tbody>{{ROWS}}</tbody></table></div></div></div>
<footer>ОВ-Проверка {{VER}} · прототип · колёсико мыши — масштаб, перетаскивание — сдвиг</footer>
<script>
const svg=document.getElementById('plan');const full=svg.getAttribute('viewBox').split(' ').map(Number);
let vb=full.slice();const set=()=>svg.setAttribute('viewBox',vb.join(' '));
document.getElementById('fit').onclick=()=>{vb=full.slice();set()};
svg.addEventListener('wheel',e=>{e.preventDefault();const r=svg.getBoundingClientRect();
const k=e.deltaY>0?1.2:1/1.2;const s=Math.max(vb[2]/r.width,vb[3]/r.height);
const ox=(r.width-vb[2]/s)/2,oy=(r.height-vb[3]/s)/2;
const mx=vb[0]+(e.clientX-r.left-ox)*s,my=vb[1]+(e.clientY-r.top-oy)*s;
vb=[mx-(mx-vb[0])*k,my-(my-vb[1])*k,vb[2]*k,vb[3]*k];set()},{passive:false});
let drag=null;svg.addEventListener('mousedown',e=>{drag=[e.clientX,e.clientY,vb[0],vb[1]]});
window.addEventListener('mouseup',()=>drag=null);
window.addEventListener('mousemove',e=>{if(!drag)return;const r=svg.getBoundingClientRect();
const s=Math.max(vb[2]/r.width,vb[3]/r.height);vb[0]=drag[2]-(e.clientX-drag[0])*s;
vb[1]=drag[3]-(e.clientY-drag[1])*s;set()});
document.querySelectorAll('tr[data-n]').forEach(tr=>{tr.onclick=()=>{
const x=+tr.dataset.x,y=+tr.dataset.y,w=full[2]/6,h=full[3]/6;vb=[x-w/2,y-h/2,w,h];set();
document.querySelectorAll('.m').forEach(m=>m.classList.toggle('hl',m.dataset.n===tr.dataset.n))}});
</script></body></html>"""


# ---------------------------------------------------------------- запуск

def main(argv=None):
    ap = argparse.ArgumentParser(description="ОВ-Проверка: подписи сечений воздуховодов против геометрии")
    ap.add_argument("dxf", help="чертёж в формате DXF")
    ap.add_argument("--tol", type=float, default=DEFAULTS["tol"], help="допуск, мм (по умолчанию 5)")
    ap.add_argument("--layers", default=DEFAULTS["duct_layers"],
                    help="шаблон имён слоёв воздуховодов (регулярное выражение)")
    ap.add_argument("--radius", type=float, default=DEFAULTS["search_radius"],
                    help="радиус поиска воздуховода от подписи, мм (по умолчанию 1500)")
    ap.add_argument("--out", help="папка для результатов (по умолчанию рядом с чертежом)")
    a = ap.parse_args(argv)

    cfg = dict(DEFAULTS, tol=a.tol, duct_layers=a.layers, search_radius=a.radius)
    dxf = DXF(a.dxf)
    results, all_segs, duct_layers, notes, nlab = check_sections(dxf, cfg)

    base = os.path.splitext(os.path.basename(a.dxf))[0]
    outdir = a.out or os.path.dirname(os.path.abspath(a.dxf))
    os.makedirs(outdir, exist_ok=True)
    p_html = os.path.join(outdir, base + "_отчет.html")
    p_csv = os.path.join(outdir, base + "_отчет.csv")
    p_dxf = os.path.join(outdir, base + "_ПРОВЕРКА.dxf")

    info = {"file": os.path.basename(a.dxf), "dxfver": dxf.version, "units": dxf.units_name,
            "duct_layers": duct_layers, "nlabels": nlab, "tol": cfg["tol"], "notes": notes}
    write_html(results, all_segs, info, p_html)
    write_csv(results, p_csv)
    n_marked = write_marked(dxf, results, p_dxf)

    cnt = {k: sum(1 for r in results if r["status"] == k) for k in ("error", "warn", "notfound", "ok")}
    print(f"Проверено подписей: {len(results)}  |  ошибок: {cnt['error']}  внимание: {cnt['warn']}  "
          f"не найдено: {cnt['notfound']}  ОК: {cnt['ok']}")
    for r in results:
        if r["status"] != "ok":
            print(f"  !{r['n']:<3} {r['msg']}   (X={r['mx']:.0f}, Y={r['my']:.0f})")
    print(f"\nОтчёт:   {p_html}\nТаблица: {p_csv}\nЧертёж:  {p_dxf} (отметок: {n_marked}, слой ПРОВЕРКА)")
    return results


if __name__ == "__main__":
    main()
