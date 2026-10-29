#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare, версия 0.2 (прототип, PDF)

Проверка №1: подпись сечения воздуховода против нарисованной ширины.
  Читает векторный PDF (печать из AutoCAD / nanoCAD / Revit), находит подписи
  вида 400х200, 400x200, Ø160, определяет по выноске или по положению текста,
  к какому воздуховоду они относятся, измеряет расстояние между его стенками
  и сравнивает. Масштаб каждого листа подбирается автоматически
  (1:50, 1:100, 1:200 ...), его можно задать вручную ключом --scale.

Запуск:
    python ov_check.py альбом.pdf
    python ov_check.py альбом.pdf --pages 9-15 --scale 100 --tol 5

На выходе рядом с PDF:
    <имя>_отчет.html     отчёт: листы с отметками и список замечаний
    <имя>_отчет.csv      то же для Excel
    <имя>_ПРОВЕРКА.pdf   копия PDF, замечания нанесены аннотациями

Нужен Python 3.8+ и PyMuPDF:  python -m pip install pymupdf
PDF должен быть векторным и с текстом (не скан). Если текст выведен
SHX-шрифтом как линии, подписи не найдутся — в отчёте будет предупреждение.
Предыдущая версия для DXF лежит рядом: ov_check_dxf_0.1.py
"""

import argparse
import base64
import csv
import html
import math
import os
import re
import sys

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        raise SystemExit("Нужна библиотека PyMuPDF: python -m pip install pymupdf")

VERSION = "0.2"
PT_MM = 25.4 / 72           # 1 пункт PDF в миллиметрах бумаги

# ---------------------------------------------------------------- настройки

DEFAULTS = {
    # слои воздуховодов (регулярное выражение, без учёта регистра)
    "duct_layers": r"возд|вент|duct|vent|приток|вытяж|подпор|подача|дым|pritok|vityazh|^h-|"
                   r"(?<![а-яa-z])ов(?![а-яa-z])",
    # слои, которые точно не воздуховоды, даже если подходят по шаблону выше
    "exclude_layers": r"text|текст|dim|разм|выноск|трубопр|pipe|san_|отверст|оборуд|oborud|"
                      r"воздухораспр|решет|grid|hatch|штрих",
    # допуск по размеру, мм
    "tol": 5.0,
    # радиус поиска воздуховода вокруг подписи, мм в натуре
    "search_radius": 1500.0,
    # подписи с этими словами не проверяются (решётки, клапаны и т.п.)
    # (ищутся и в соседних строках той же выноски)
    "skip_words": r"АМН|АДН|АРН|АДР|АПН|RA[GRD]|SA[DGR]|EAL|KSG|ДПУ|ДКУ|КВК|КП[А-Я]|КЛОП|ОЗК|"
                  r"(?<![А-ЯA-Z])(ДК|КД|КО)(?![А-ЯA-Z])|EI\s*\d|решет|диффуз|клапан|"
                  r"фильтр|шумоглуш|вентилятор|подключ|L\s*=",
    # разумные пределы ширины воздуховода, мм
    "min_width": 50.0,
    "max_width": 3000.0,
    # масштабы, из которых выбирается масштаб листа (1:10 и 1:20 — только через --scale)
    "scales": (25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500),
    # доля совпавших подписей, при которой масштаб считается найденным
    "scale_min_share": 0.3,
}

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


class Grid:
    """Сетка для быстрого поиска объектов рядом с точкой."""

    def __init__(self, cell):
        self.cell = cell
        self.cells = {}

    def add(self, obj, x0, y0, x1, y1):
        c = self.cell
        for i in range(int(x0 // c), int(x1 // c) + 1):
            for j in range(int(y0 // c), int(y1 // c) + 1):
                self.cells.setdefault((i, j), []).append(obj)

    def query(self, x0, y0, x1, y1):
        c = self.cell
        seen, out = set(), []
        for i in range(int(x0 // c), int(x1 // c) + 1):
            for j in range(int(y0 // c), int(y1 // c) + 1):
                for o in self.cells.get((i, j), ()):
                    if id(o) not in seen:
                        seen.add(id(o))
                        out.append(o)
        return out


# ---------------------------------------------------------------- тексты

UNICODE_ESC = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def clean_text(s):
    s = UNICODE_ESC.sub(lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"%%[cC]", "Ø", s)
    s = re.sub(r"%%[dD]", "°", s)
    s = re.sub(r"%%[pP]", "±", s)
    s = re.sub(r"%%[uUoO]", "", s)
    return " ".join(s.split())


# перед размером не должно быть букв (RAG150x100 — марка решётки),
# после диаметра — «x» (Ø16x2.0 — труба)
RE_RECT = re.compile(r"(?<![\w.,])(\d{2,4})\s*[xXхХ×*]\s*(\d{2,4})(?![\d.,])")
RE_ROUND = re.compile(r"[ØøØ∅⌀]\s*(\d{2,4})(?![\d.,xXхХ×*])")


# ---------------------------------------------------------------- чтение PDF

def dash_count(dashes):
    """Число элементов в шаблоне штриховки: 4 и больше — штрихпунктир (оси)."""
    return len(re.findall(r"[\d.]+", (dashes or "").split("]")[0]))


class Sheet:
    """Геометрия и подписи одного листа PDF, в пунктах (система неповёрнутой страницы)."""

    def __init__(self, page, cfg):
        self.page = page
        self.pno = page.number
        duct_re = re.compile(cfg["duct_layers"], re.I)
        excl_re = re.compile(cfg["exclude_layers"], re.I)

        lines = []                  # (x1, y1, x2, y2, слой, осевая)
        polys = []                  # ломаные-кандидаты в выноски: (точки, слой)
        circles = []                # (cx, cy, радиус, слой) — круглые сечения
        used_layers = set()
        for d in page.get_drawings():
            if d.get("type") == "f":            # заливки и штриховки
                continue
            layer = (d.get("layer") or "").strip()
            used_layers.add(layer)
            items = d["items"]
            if len(items) >= 4 and all(it[0] == "c" for it in items):
                r = d["rect"]
                if r.width > 0.5 and abs(r.width - r.height) < 0.03 * r.width:
                    circles.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, r.width / 2, layer))
            ndash = dash_count(d.get("dashes"))
            axis = ndash >= 4
            stroke_only = d.get("type") == "s" and ndash == 0
            chain = []

            def flush():
                if stroke_only and 2 <= len(chain) <= 7:
                    polys.append((list(chain), layer))
                if len(chain) >= 4:
                    arc = fit_arc(chain)
                    if arc:
                        circles.append(arc + (layer,))
                chain.clear()

            for it in d["items"]:
                op = it[0]
                if op == "l":
                    p1, p2 = it[1], it[2]
                    if chain and (abs(chain[-1][0] - p1.x) > 0.05 or abs(chain[-1][1] - p1.y) > 0.05):
                        flush()
                    if not chain:
                        chain.append((p1.x, p1.y))
                    chain.append((p2.x, p2.y))
                    lines.append((p1.x, p1.y, p2.x, p2.y, layer, axis))
                elif op in ("re", "qu"):
                    flush()
                    if op == "re":
                        r = it[1]
                        pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                    else:
                        q = it[1]
                        pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                        lines.append((x1, y1, x2, y2, layer, axis))
                else:
                    flush()
            flush()

        self.duct_layers = sorted(l for l in used_layers
                                  if l and duct_re.search(l) and not excl_re.search(l))
        dl = set(self.duct_layers)
        self.notes = []
        if not dl:
            self.notes.append("Слои воздуховодов не найдены по шаблону (или в PDF нет слоёв), "
                              "анализирую все линии листа.")

        self.grid = Grid(25.0)
        self.nsegs = 0
        for x1, y1, x2, y2, layer, axis in lines:
            if axis or (dl and layer not in dl):
                continue
            s = Seg(x1, y1, x2, y2, layer)
            if s.len < 0.2:
                continue
            self.grid.add(s, min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            self.nsegs += 1

        self.cgrid = Grid(25.0)
        for cx, cy, rad, layer in circles:
            if not dl or layer in dl:
                self.cgrid.add((cx, cy, rad), cx - rad, cy - rad, cx + rad, cy + rad)

        self.lgrid = Grid(25.0)
        for pts, layer in polys:
            if dl and layer in dl:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if max(xs) - min(xs) > 400 or max(ys) - min(ys) > 400:
                continue
            self.lgrid.add(pts, min(xs), min(ys), max(xs), max(ys))

        self.texts = []             # все строки текста листа
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                txt = clean_text("".join(s["text"] for s in ln["spans"]))
                if not txt:
                    continue
                x0, y0, x1, y1 = ln["bbox"]
                dx, dy = ln["dir"]
                self.texts.append({
                    "text": txt, "bbox": (x0, y0, x1, y1),
                    "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                    "h": max(s["size"] for s in ln["spans"]) or 2.5,
                    "dir": (dx, dy), "rot": math.degrees(math.atan2(dy, dx)), "arrow": None})
        self.labels = [t for t in self.texts if RE_RECT.search(t["text"]) or RE_ROUND.search(t["text"])]
        if not self.texts and lines:
            self.notes.append("На листе нет текста — вероятно, он выведен SHX-шрифтом как линии. "
                              "Подписи прочитать нельзя.")
        for lab in self.labels:
            lab["half"] = text_half(lab)
            lab["context"] = " ".join(t["text"] for t in self.texts if same_callout(lab, t))
        attach_leaders(self)


def fit_arc(pts):
    """Окружность через ломаную из коротких отрезков (так в PDF часто выводятся дуги).
    Возвращает (cx, cy, радиус) или None, если точки на окружность не ложатся."""
    n = len(pts)
    (x1, y1), (x2, y2), (x3, y3) = pts[0], pts[n // 3], pts[2 * n // 3]
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None
    s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
    cx = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d
    cy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d
    rad = math.hypot(x1 - cx, y1 - cy)
    if rad < 0.5 or any(abs(math.hypot(x - cx, y - cy) - rad) > 0.04 * rad for x, y in pts):
        return None
    # дуга должна быть заметной (не почти прямая линия)
    span = max(math.hypot(x - x1, y - y1) for x, y in pts)
    if span < 0.7 * rad:
        return None
    return cx, cy, rad


def text_half(t):
    """Половина длины строки вдоль направления текста."""
    ux, uy = t["dir"]
    x0, y0, x1, y1 = t["bbox"]
    return (abs((x1 - x0) * ux) + abs((y1 - y0) * uy)) / 2


def same_callout(lab, t):
    """Строка t стоит над или под подписью lab (та же выноска)."""
    ux, uy = lab["dir"]
    if abs(ux * t["dir"][1] - uy * t["dir"][0]) > 0.05:
        return False
    dx, dy = t["cx"] - lab["cx"], t["cy"] - lab["cy"]
    if abs(dx * -uy + dy * ux) > 1.8 * lab["h"]:
        return False
    return abs(dx * ux + dy * uy) < lab["half"] + text_half(t)


def attach_leaders(sheet):
    """Ищет выноску: ломаную, у которой полка идёт вдоль текста (или которая начинается
    у края текста), а другой конец уходит в сторону."""
    for lab in sheet.labels:
        ux, uy = lab["dir"]
        cx, cy, h, half = lab["cx"], lab["cy"], lab["h"], lab["half"]
        x0, y0, x1, y1 = lab["bbox"]
        ends = ((cx + ux * half, cy + uy * half), (cx - ux * half, cy - uy * half))
        best = None
        for pts in sheet.lgrid.query(x0 - 2 * h, y0 - 2 * h, x1 + 2 * h, y1 + 2 * h):
            # выноска без полки: начинается у начала или конца строки
            for e, far in ((pts[0], pts[-1]), (pts[-1], pts[0])):
                de = min(math.hypot(e[0] - q[0], e[1] - q[1]) for q in ends)
                if de <= h and math.hypot(far[0] - cx, far[1] - cy) > half + 2 * h:
                    if best is None or 10 * h + de < best[0]:
                        best = (10 * h + de, far)
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                L = math.hypot(bx - ax, by - ay)
                if L < half:
                    continue
                vx, vy = (bx - ax) / L, (by - ay) / L
                if abs(vx * uy - vy * ux) > 0.05:
                    continue
                off = (ax - cx) * -uy + (ay - cy) * ux
                if abs(off) > 1.2 * h:
                    continue
                ta = (ax - cx) * ux + (ay - cy) * uy
                tb = (bx - cx) * ux + (by - cy) * uy
                if min(max(ta, tb), half) - max(min(ta, tb), -half) < half:
                    continue
                far = max((pts[0], pts[-1]), key=lambda p: math.hypot(p[0] - cx, p[1] - cy))
                # конец выноски должен уходить с полки: вбок от строки или дальше её края
                fo = (far[0] - cx) * -uy + (far[1] - cy) * ux
                ft = abs((far[0] - cx) * ux + (far[1] - cy) * uy)
                if abs(fo - off) < h and ft <= half + 1.5 * h:
                    continue
                if best is None or abs(off) < best[0]:
                    best = (abs(off), far)
        if best:
            lab["arrow"] = best[1]


# ---------------------------------------------------------------- анализ

def params(cfg, scale):
    """Пороги из мм в натуре в пункты листа для данного масштаба."""
    k = PT_MM * scale
    return {"k": k, "R": cfg["search_radius"] / k, "R_strict": max(300.0, cfg["search_radius"] / 3) / k,
            "minlen": 100.0 / k, "min_width": cfg["min_width"] / k, "max_width": cfg["max_width"] / k,
            "eps": 1.0 / k}


def find_duct(px, py, grid, P, prefer_angle=None, strict=False, same_layer=True, text_h=None):
    """Ищет пару параллельных стенок воздуховода возле точки (px, py). Всё в пунктах.
    strict — точка это конец выноски, она должна лежать на воздуховоде.
    same_layer — обе стенки должны быть на одном слое (не меряем от притока до вытяжки)."""
    R = P["R_strict"] if strict else P["R"]
    eps = P["eps"]
    near = []
    for s in grid.query(px - R, py - R, px + R, py + R):
        if s.len < P["minlen"]:
            continue
        dist = point_seg_dist(px, py, s)
        if dist <= R:
            near.append((dist, s))
    near.sort(key=lambda t: t[0])
    near = [s for _, s in near[:120]]
    sin_tol = math.sin(math.radians(1.5))
    best = None
    for a in range(len(near)):
        s1 = near[a]
        for b in range(a + 1, len(near)):
            s2 = near[b]
            if abs(s1.ux * s2.uy - s1.uy * s2.ux) > sin_tol:
                continue
            if same_layer and s1.layer != s2.layer:
                continue
            # расстояние между прямыми
            d1 = (s2.x1 - s1.x1) * -s1.uy + (s2.y1 - s1.y1) * s1.ux
            d2 = (s2.x2 - s1.x1) * -s1.uy + (s2.y2 - s1.y1) * s1.ux
            width = abs((d1 + d2) / 2)
            if not (P["min_width"] <= width <= P["max_width"]):
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
            # подпись без выноски может стоять внутри воздуховода, только если в него помещается
            if inside and text_h and width < 1.2 * text_h:
                continue
            if strict and (off_side > 0.3 * width + 50 * eps or off_len > width + 50 * eps):
                continue
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
                if min(0, mid) + eps < d3 < max(0, mid) - eps:
                    t3 = ((s3.x1 + s3.x2) / 2 - s1.x1) * s1.ux + ((s3.y1 + s3.y2) / 2 - s1.y1) * s1.uy
                    if lo <= t3 <= hi:
                        score += R
                        break
            if score > R:
                continue
            if best is None or score < best[0] or (abs(score - best[0]) < eps and width < best[1]):
                tcl = max(lo, min(hi, tp))
                mx = s1.x1 + s1.ux * tcl + -s1.uy * mid / 2
                my = s1.y1 + s1.uy * tcl + s1.ux * mid / 2
                best = (score, width, (mx, my), s1, s2, overlap)
    return best


def label_sizes(lab, skip_re):
    txt = lab["text"]
    if skip_re.search(lab["context"]):
        return None
    sizes = [("rect", int(m.group(1)), int(m.group(2))) for m in RE_RECT.finditer(txt)]
    sizes += [("round", int(m.group(1)), None) for m in RE_ROUND.finditer(txt)]
    return sizes[0] if sizes else None


def find_circle(px, py, sheet, P):
    """Окружность, на которую (или внутрь которой) указывает выноска."""
    best = None
    tol = 50 * P["eps"]
    for cx, cy, rad in sheet.cgrid.query(px - tol, py - tol, px + tol, py + tol):
        if not (P["min_width"] <= 2 * rad <= P["max_width"]):
            continue
        d = math.hypot(px - cx, py - cy)
        if d > rad + tol:
            continue
        miss = abs(d - rad)
        if best is None or miss < best[0]:
            best = (miss, cx, cy, rad)
    return best


def evaluate(lab, size, sheet, P, tol):
    kind, w, h = size
    if kind == "round" and lab["arrow"]:
        circ = find_circle(lab["arrow"][0], lab["arrow"][1], sheet, P)
        if circ:
            _, cx, cy, rad = circ
            dia = round(2 * rad * P["k"], 1)
            r = {"text": lab["text"], "kind": kind, "w": w, "h": h, "via": "выноска",
                 "page": sheet.pno, "lx": lab["cx"], "ly": lab["cy"],
                 "measured": dia, "wpt": 2 * rad, "px": cx, "py": cy}
            if abs(dia - w) <= tol:
                r.update(status="ok", msg=f"Ø{w}: на чертеже окружность Ø{dia:g}, совпадает")
            else:
                r.update(status="error", msg=f"Подпись Ø{w}, а нарисована окружность Ø{dia:g} "
                                             f"(разница {dia - w:+g})")
            return r
    if lab["arrow"]:
        ax, ay = lab["arrow"]
        found = find_duct(ax, ay, sheet.grid, P, strict=True, same_layer=bool(sheet.duct_layers))
        via = "выноска"
    else:
        ax, ay = lab["cx"], lab["cy"]
        found = find_duct(ax, ay, sheet.grid, P, prefer_angle=lab["rot"],
                          same_layer=bool(sheet.duct_layers), text_h=lab["h"])
        via = "текст рядом"
    r = {"text": lab["text"], "kind": kind, "w": w, "h": h, "via": via,
         "page": sheet.pno, "lx": lab["cx"], "ly": lab["cy"]}
    label_str = f"{w}×{h}" if kind == "rect" else f"Ø{w}"
    if not found:
        r.update(status="notfound", measured=None, wpt=None, px=ax, py=ay,
                 msg=f"Рядом с подписью {label_str} не найден воздуховод")
        return r
    _, wpt, (mx, my), _, _, overlap = found
    width = round(wpt * P["k"], 1)
    length = round(overlap * P["k"], 1)
    r.update(measured=width, wpt=wpt, px=mx, py=my)
    # выноска на короткий замкнутый прямоугольник — сечение стояка, у него видны обе стороны
    riser = (lab["arrow"] and kind == "rect" and abs(width - w) > tol
             and length <= 1.6 * max(w, h) and length <= 1500)
    if abs(width - w) <= tol:
        r.update(status="ok", msg=f"{label_str}: на чертеже {width:g}, совпадает")
    elif riser:
        # стояки часто рисуют с запасом на фланцы — допуск 10 %
        drawn = sorted((width, length), reverse=True)
        if all(abs(a - b) <= max(tol, 0.1 * b) for a, b in zip(drawn, sorted((w, h), reverse=True))):
            r.update(status="ok", msg=f"{label_str}: стояк {drawn[0]:g}×{drawn[1]:g}, совпадает")
        else:
            r.update(status="warn",
                     msg=f"Стояк: подпись {label_str}, нарисован прямоугольник "
                         f"{drawn[0]:g}×{drawn[1]:g}")
    elif kind == "rect" and h is not None and abs(width - h) <= tol:
        r.update(status="warn",
                 msg=f"Подпись {label_str}, на плане ширина {width:g}. "
                     f"Возможно перепутаны ширина и высота")
    else:
        r.update(status="error",
                 msg=f"Подпись {label_str}, а нарисовано {width:g} "
                     f"(разница {width - w:+g})")
    return r


def detect_scale(items, sheet, cfg):
    """Масштаб, при котором совпадает больше всего подписей."""
    step = max(1, len(items) // 40)
    sample = items[::step][:40]
    prefer = (100, 50, 200, 25, 150, 75, 250, 20, 125, 300, 400, 500, 10)
    best = None
    for S in cfg["scales"]:
        P = params(cfg, S)
        st = [evaluate(lab, size, sheet, P, cfg["tol"])["status"] for lab, size in sample]
        ok, err = st.count("ok"), st.count("error")
        rank = prefer.index(S) if S in prefer else len(prefer)
        key = (-ok, err, rank)
        if best is None or key < best[0]:
            best = (key, S, ok)
    return best[1], best[2]


def check_sheet(sheet, cfg, scale=None):
    skip_re = re.compile(cfg["skip_words"], re.I)
    items = [(lab, s) for lab in sheet.labels for s in [label_sizes(lab, skip_re)] if s]
    info = {"page": sheet.pno, "nlabels": len(items), "duct_layers": sheet.duct_layers,
            "notes": list(sheet.notes), "scale": None, "scale_how": ""}
    if not items:
        info["notes"] = [n for n in info["notes"] if "SHX" in n]
        return [], info
    if scale:
        S, how = scale, "задан"
    else:
        S, ok = detect_scale(items, sheet, cfg)
        how = "авто"
        if ok < max(3, cfg["scale_min_share"] * min(len(items), 40)):
            info["notes"].append(
                "Масштаб не определился: мало подписей совпало ни при одном масштабе "
                "(возможно, это схема, а не план). Лист не проверялся — задайте масштаб ключом --scale.")
            info["scale_how"] = "не определён"
            return [], info
    info["scale"], info["scale_how"] = S, how
    P = params(cfg, S)
    results = [evaluate(lab, size, sheet, P, cfg["tol"]) for lab, size in items]
    for r in results:
        r["scale"] = S
    return results, info


# ---------------------------------------------------------------- PDF с отметками

COLORS = {"error": (0.78, 0.22, 0.18), "warn": (0.85, 0.54, 0.0), "notfound": (0.23, 0.44, 0.84)}


def mark_radius(r):
    wpt = r["wpt"] if r["wpt"] else r["w"] / (PT_MM * r["scale"])
    return max(8.0, wpt * 0.9 + 4.0)


def on_sheet(page, x, y):
    """Точка неповёрнутой страницы -> точка листа, как его видит человек."""
    p = pymupdf.Point(x, y) * page.rotation_matrix
    return p.x, p.y


def write_marked(doc, results, out_path):
    issues = [r for r in results if r["status"] != "ok"]
    for r in issues:
        page = doc[r["page"]]
        # прямоугольники аннотаций задаются в системе неповёрнутой страницы,
        # а смещение подписи номера считаем так, как лист видит человек
        x, y = r["px"], r["py"]
        rad = mark_radius(r)
        col = COLORS[r["status"]]
        a = page.add_circle_annot(pymupdf.Rect(x - rad, y - rad, x + rad, y + rad))
        a.set_colors(stroke=col)
        a.set_border(width=1.5)
        a.set_info(title="Ordinem servare", content=f"!{r['n']}  {r['msg']}  [{r['text']}]")
        a.update()
        fs = 11
        sx, sy = on_sheet(page, x, y)
        box = pymupdf.Rect(sx + rad * 0.7, sy - rad * 0.7 - fs * 1.6,
                           sx + rad * 0.7 + fs * 3.2, sy - rad * 0.7) * page.derotation_matrix
        t = page.add_freetext_annot(box, f"!{r['n']}", fontsize=fs, fontname="helv",
                                    text_color=col, rotate=page.rotation)
        t.update()
    # битые ссылки в исходном PDF дают при сохранении безвредные сообщения MuPDF
    pymupdf.TOOLS.mupdf_display_errors(False)
    doc.save(out_path, garbage=1, deflate=True)
    pymupdf.TOOLS.mupdf_display_errors(True)
    return len(issues)


# ---------------------------------------------------------------- отчёты

STATUS_RU = {"error": "Ошибка", "warn": "Внимание", "notfound": "Не найдено", "ok": "ОК"}


def write_csv(results, doc, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as fo:
        w = csv.writer(fo, delimiter=";")
        w.writerow(["№", "Лист", "Статус", "Подпись", "Размер по подписи", "Нарисовано",
                    "Замечание", "Масштаб", "Привязка", "X на листе, мм", "Y на листе, мм"])
        for r in results:
            lab = f"{r['w']}x{r['h']}" if r["kind"] == "rect" else f"Ø{r['w']}"
            x, y = on_sheet(doc[r["page"]], r["px"], r["py"])
            w.writerow([r["n"], r["page"] + 1, STATUS_RU[r["status"]], r["text"], lab,
                        "" if r["measured"] is None else f"{r['measured']:g}",
                        r["msg"], f"1:{r['scale']}", r["via"], f"{x * PT_MM:.0f}", f"{y * PT_MM:.0f}"])


def render_page(page, width_px=2000):
    z = width_px / max(page.rect.width, 1)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
    return base64.b64encode(pix.tobytes("jpeg", jpg_quality=72)).decode("ascii")


def write_html(results, infos, doc, file_info, path):
    by_page = {}
    for r in results:
        by_page.setdefault(r["page"], []).append(r)

    views, opts = [], []
    for info in infos:
        pno = info["page"]
        if pno not in by_page:
            continue
        page = doc[pno]
        W, H = page.rect.width, page.rect.height
        marks = []
        for r in by_page[pno]:
            x, y = on_sheet(page, r["px"], r["py"])
            rad = mark_radius(r)
            fs = min(max(rad * 0.9, 10), 18)
            marks.append(
                f'<g class="m {r["status"]}" data-n="{r["n"]}">'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rad:.1f}"/>'
                f'<text x="{x + rad * 0.8:.1f}" y="{y - rad * 0.8:.1f}" font-size="{fs:.1f}">{r["n"]}</text></g>')
        img = render_page(page)
        views.append(
            f'<svg class="plan" data-p="{pno + 1}" viewBox="0 0 {W:.1f} {H:.1f}" '
            f'preserveAspectRatio="xMidYMid meet">'
            f'<image href="data:image/jpeg;base64,{img}" x="0" y="0" width="{W:.1f}" height="{H:.1f}"/>'
            f'<g>{"".join(marks)}</g></svg>')
        cnt = {k: sum(1 for r in by_page[pno] if r["status"] == k) for k in STATUS_RU}
        bad = cnt["error"] + cnt["warn"] + cnt["notfound"]
        opts.append(f'<option value="{pno + 1}">Лист {pno + 1} — замечаний {bad}</option>')

    rows = []
    for r in results:
        x, y = on_sheet(doc[r["page"]], r["px"], r["py"])
        meas = "—" if r["measured"] is None else format(r["measured"], "g")
        rows.append(
            f'<tr class="{r["status"]}" data-n="{r["n"]}" data-p="{r["page"] + 1}" '
            f'data-x="{x:.1f}" data-y="{y:.1f}"><td>{r["n"]}</td><td>{r["page"] + 1}</td>'
            f'<td><span class="tag {r["status"]}">{STATUS_RU[r["status"]]}</span></td>'
            f'<td>{html.escape(r["text"])}</td>'
            f'<td>{meas}</td>'
            f'<td>{html.escape(r["msg"])}</td></tr>')

    sheets = []
    for info in infos:
        if not info["nlabels"] and not info["notes"]:
            continue
        sc = f'1:{info["scale"]} ({info["scale_how"]})' if info["scale"] else (info["scale_how"] or "—")
        lay = html.escape(", ".join(info["duct_layers"]) or "все линии")
        notes = "".join(f'<div class="note">{html.escape(n)}</div>' for n in info["notes"])
        sheets.append(f'<tr><td>{info["page"] + 1}</td><td>{sc}</td><td>{info["nlabels"]}</td>'
                      f'<td>{lay}{notes}</td></tr>')

    cnt = {k: sum(1 for r in results if r["status"] == k) for k in STATUS_RU}
    repl = {
        "{{FILE}}": html.escape(file_info["file"]),
        "{{VER}}": VERSION,
        "{{NPAGES}}": str(file_info["npages"]),
        "{{NLAB}}": str(len(results)),
        "{{TOL}}": f"{file_info['tol']:g}",
        "{{E}}": str(cnt["error"]), "{{W}}": str(cnt["warn"]),
        "{{N}}": str(cnt["notfound"]), "{{OK}}": str(cnt["ok"]),
        "{{SHEETS}}": "\n".join(sheets) or "<tr><td colspan=4>Подписи сечений не найдены</td></tr>",
        "{{OPTS}}": "\n".join(opts),
        "{{VIEWS}}": "\n".join(views) or '<div class="empty">Нет листов с подписями сечений</div>',
        "{{ROWS}}": "\n".join(rows) or "<tr><td colspan=6>Подписи сечений не найдены</td></tr>",
    }
    page_html = HTML_TEMPLATE
    for k, v in repl.items():
        page_html = page_html.replace(k, v)
    with open(path, "w", encoding="utf-8") as fo:
        fo.write(page_html)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ordinem servare: {{FILE}}</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1f23;--mute:#6b6f76;--line:#e3e1dc;
--err:#c8372d;--warn:#d98a00;--nf:#3b6fd6;--ok:#2f8a4c}
@media (prefers-color-scheme:dark){:root{--bg:#16181b;--card:#1f2226;--ink:#e8e6e1;
--mute:#9a9ea5;--line:#33373c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:20px 24px 8px}h1{font-size:20px;margin:0 0 4px}
.meta{color:var(--mute);font-size:13px}
.sum{display:flex;gap:10px;flex-wrap:wrap;padding:8px 24px 16px}
.k{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:120px}
.k b{display:block;font-size:24px}.k.error b{color:var(--err)}.k.warn b{color:var(--warn)}
.k.notfound b{color:var(--nf)}.k.ok b{color:var(--ok)}
.wrap{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:16px;padding:0 24px 16px}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.card h2{font-size:14px;margin:0;padding:8px 14px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}
svg.plan{width:100%;height:72vh;display:none;background:#fff;cursor:grab}
.m circle{fill:none;stroke-width:2.5px;vector-effect:non-scaling-stroke}
.m text{font-weight:700;font-family:system-ui;paint-order:stroke;stroke:#fff;stroke-width:3px;vector-effect:non-scaling-stroke}
.m.error circle{stroke:var(--err)}.m.error text{fill:var(--err)}
.m.warn circle{stroke:var(--warn)}.m.warn text{fill:var(--warn)}
.m.notfound circle{stroke:var(--nf)}.m.notfound text{fill:var(--nf)}
.m.ok circle{stroke:var(--ok);opacity:.45}.m.ok text{fill:var(--ok);opacity:.6}
.m.hl circle{stroke-width:5px;opacity:1}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
#list tr{cursor:pointer}#list tr:hover{background:color-mix(in srgb,var(--ink) 5%,transparent)}
#list tr.sel{background:color-mix(in srgb,var(--nf) 12%,transparent)}
.tag{font-size:11px;font-weight:600;padding:2px 7px;border-radius:99px;white-space:nowrap;color:#fff}
.tag.error{background:var(--err)}.tag.warn{background:var(--warn)}
.tag.notfound{background:var(--nf)}.tag.ok{background:var(--ok)}
.scroll{max-height:72vh;overflow:auto}
.tools{margin-left:auto;display:flex;gap:6px}
button,select{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--bg);color:var(--ink);
border-radius:6px;padding:2px 8px;cursor:pointer}
label.f{font-weight:400;font-size:12px;color:var(--mute)}
.sheets{margin:0 24px 16px}.sheets td{font-size:12px}.sheets th{font-size:12px;text-align:left;
padding:6px 10px;color:var(--mute);font-weight:500;border-bottom:1px solid var(--line)}
.note{color:var(--warn)}.empty{padding:40px;color:var(--mute);text-align:center}
footer{color:var(--mute);font-size:12px;padding:0 24px 24px}
</style></head><body>
<header><h1>Проверка сечений воздуховодов</h1>
<div class="meta">Файл: <b>{{FILE}}</b> · листов проверено: {{NPAGES}} · допуск ±{{TOL}} мм ·
подписей с размерами: {{NLAB}}</div></header>
<div class="sum">
<div class="k error"><b>{{E}}</b>ошибок</div><div class="k warn"><b>{{W}}</b>внимание</div>
<div class="k notfound"><b>{{N}}</b>не найден воздуховод</div><div class="k ok"><b>{{OK}}</b>совпадает</div></div>
<div class="wrap">
<div class="card"><h2><select id="pg">{{OPTS}}</select>
<span class="tools"><button id="fit">Весь лист</button></span></h2>
<div id="views">{{VIEWS}}</div></div>
<div class="card"><h2>Замечания <span class="tools"><label class="f"><input type="checkbox" id="showok"> показывать ОК</label></span></h2>
<div class="scroll"><table id="list"><tbody>{{ROWS}}</tbody></table></div></div></div>
<div class="card sheets"><table><thead><tr><th>Лист</th><th>Масштаб</th><th>Подписей</th>
<th>Слои воздуховодов / примечания</th></tr></thead><tbody>{{SHEETS}}</tbody></table></div>
<footer>Ordinem servare {{VER}} · прототип · колёсико мыши — масштаб, перетаскивание — сдвиг, строка списка — показать на листе</footer>
<script>
const views=[...document.querySelectorAll('svg.plan')],sel=document.getElementById('pg');let cur=null;
views.forEach(v=>{v._full=v.getAttribute('viewBox').split(' ').map(Number);v._vb=v._full.slice()});
const set=(v,vb)=>{v._vb=vb;v.setAttribute('viewBox',vb.join(' '))};
function show(p){views.forEach(v=>v.style.display=v.dataset.p===p?'block':'none');
cur=views.find(v=>v.dataset.p===p)||null;sel.value=p}
if(views.length)show(views[0].dataset.p);
sel.onchange=()=>show(sel.value);
document.getElementById('fit').onclick=()=>{if(cur)set(cur,cur._full.slice())};
views.forEach(svg=>{
svg.addEventListener('wheel',e=>{e.preventDefault();const vb=svg._vb,r=svg.getBoundingClientRect();
const k=e.deltaY>0?1.2:1/1.2;const s=Math.max(vb[2]/r.width,vb[3]/r.height);
const ox=(r.width-vb[2]/s)/2,oy=(r.height-vb[3]/s)/2;
const mx=vb[0]+(e.clientX-r.left-ox)*s,my=vb[1]+(e.clientY-r.top-oy)*s;
set(svg,[mx-(mx-vb[0])*k,my-(my-vb[1])*k,vb[2]*k,vb[3]*k])},{passive:false});
svg.addEventListener('mousedown',e=>{svg._drag=[e.clientX,e.clientY,svg._vb[0],svg._vb[1]]})});
window.addEventListener('mouseup',()=>views.forEach(v=>v._drag=null));
window.addEventListener('mousemove',e=>{if(!cur||!cur._drag)return;const d=cur._drag,vb=cur._vb,
r=cur.getBoundingClientRect();const s=Math.max(vb[2]/r.width,vb[3]/r.height);
set(cur,[d[2]-(e.clientX-d[0])*s,d[3]-(e.clientY-d[1])*s,vb[2],vb[3]])});
const rows=[...document.querySelectorAll('#list tr[data-n]')],chk=document.getElementById('showok');
const filt=()=>{rows.forEach(tr=>tr.style.display=(tr.classList.contains('ok')&&!chk.checked)?'none':'');
document.querySelectorAll('.m.ok').forEach(m=>m.style.display=chk.checked?'':'none')};
chk.onchange=filt;filt();
rows.forEach(tr=>{tr.onclick=()=>{show(tr.dataset.p);if(!cur)return;
const x=+tr.dataset.x,y=+tr.dataset.y,w=cur._full[2]/8,h=cur._full[3]/8;set(cur,[x-w/2,y-h/2,w,h]);
rows.forEach(r=>r.classList.toggle('sel',r===tr));
document.querySelectorAll('.m').forEach(m=>m.classList.toggle('hl',m.dataset.n===tr.dataset.n))}});
</script></body></html>"""


# ---------------------------------------------------------------- запуск

def parse_pages(spec, n):
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out += range(int(a) - 1, min(int(b), n))
        else:
            out.append(int(part) - 1)
    return [p for p in out if 0 <= p < n]


def output_paths(path, outdir=None):
    """Пути к отчёту HTML, таблице CSV и PDF с отметками."""
    base = os.path.splitext(os.path.basename(path))[0]
    outdir = outdir or os.path.dirname(os.path.abspath(path))
    return (os.path.join(outdir, base + "_отчет.html"),
            os.path.join(outdir, base + "_отчет.csv"),
            os.path.join(outdir, base + "_ПРОВЕРКА.pdf"))


def run_file(path, cfg, a):
    doc = pymupdf.open(path)
    if doc.needs_pass:
        raise SystemExit(f"{path}: PDF защищён паролем.")
    pages = parse_pages(a.pages, doc.page_count)

    results, infos = [], []
    for pno in pages:
        sheet = Sheet(doc[pno], cfg)
        res, info = check_sheet(sheet, cfg, a.scale)
        results += res
        infos.append(info)
        if info["scale"]:
            c = {k: sum(1 for r in res if r["status"] == k) for k in STATUS_RU}
            print(f"  лист {pno + 1:>3}: масштаб 1:{info['scale']} ({info['scale_how']}), "
                  f"подписей {info['nlabels']}: ошибок {c['error']}, внимание {c['warn']}, "
                  f"не найдено {c['notfound']}, ОК {c['ok']}")
        for note in info["notes"]:
            print(f"  лист {pno + 1:>3}: {note}")

    order = {"error": 0, "warn": 1, "notfound": 2, "ok": 3}
    results.sort(key=lambda r: (order[r["status"]], r["page"], r["py"], r["px"]))
    for k, r in enumerate(results, 1):
        r["n"] = k

    p_html, p_csv, p_pdf = output_paths(path, a.out)
    os.makedirs(os.path.dirname(p_html), exist_ok=True)

    file_info = {"file": os.path.basename(path), "npages": len(pages), "tol": cfg["tol"]}
    write_html(results, infos, doc, file_info, p_html)
    write_csv(results, doc, p_csv)
    n_marked = write_marked(doc, results, p_pdf)

    cnt = {k: sum(1 for r in results if r["status"] == k) for k in order}
    print(f"Проверено подписей: {len(results)}  |  ошибок: {cnt['error']}  внимание: {cnt['warn']}  "
          f"не найдено: {cnt['notfound']}  ОК: {cnt['ok']}")
    for r in results:
        if r["status"] in ("error", "warn"):
            print(f"  !{r['n']:<4} лист {r['page'] + 1:<3} {r['msg']}")
    print(f"\nОтчёт:   {p_html}\nТаблица: {p_csv}\nPDF:     {p_pdf} (отметок: {n_marked})")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ordinem servare: подписи сечений воздуховодов против геометрии (PDF)")
    ap.add_argument("pdf", nargs="+", help="один или несколько PDF")
    ap.add_argument("--pages", help="листы, например 9-15 или 3,5,9-12 (по умолчанию все)")
    ap.add_argument("--scale", type=int, help="масштаб листов, например 100 для 1:100 (по умолчанию — авто)")
    ap.add_argument("--tol", type=float, default=DEFAULTS["tol"], help="допуск, мм (по умолчанию 5)")
    ap.add_argument("--layers", default=DEFAULTS["duct_layers"],
                    help="шаблон имён слоёв воздуховодов (регулярное выражение)")
    ap.add_argument("--exclude", default=DEFAULTS["exclude_layers"],
                    help="шаблон слоёв, которые не считаются воздуховодами")
    ap.add_argument("--radius", type=float, default=DEFAULTS["search_radius"],
                    help="радиус поиска воздуховода от подписи, мм в натуре (по умолчанию 1500)")
    ap.add_argument("--out", help="папка для результатов (по умолчанию рядом с PDF)")
    a = ap.parse_args(argv)

    cfg = dict(DEFAULTS, tol=a.tol, duct_layers=a.layers, exclude_layers=a.exclude,
               search_radius=a.radius)
    all_results = []
    for path in a.pdf:
        if not path.lower().endswith(".pdf"):
            print(f"{path}: пропущен — это не PDF. Версия для DXF: ov_check_dxf_0.1.py")
            continue
        print(f"\n{os.path.basename(path)}")
        all_results += run_file(path, cfg, a)
    return all_results


if __name__ == "__main__":
    main()
