#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: технические проверки по СП РК 4.02-101-2012.

  • огнезадерживающие клапаны на поэтажных воздуховодах многоэтажных систем (7.10.1 1);
  • пересечение воздуховодами противопожарных преград без клапана (7.10.9 3, 7.10.13);
  • транзитные воздуховоды через лестничные клетки (7.10.14 1);
  • дымовые клапаны и нормально закрытые клапаны систем противодымной вентиляции (3.5, 9.13 3, 9.19 5);
  • класс плотности и огнестойкость воздуховодов противодымных систем (7.10.7, 9.13 2, 9.19 2);
  • толщина стали воздуховодов по спецификации (прил. Ж.2);
  • сечения с планов против воздуховодов в спецификации.

Проверки по геометрии — эвристики: замечания с ними помечены «проверить» и требуют
взгляда инженера. Координаты — в пунктах неповёрнутой страницы PDF (как в ov_check).
"""

import math
import re

import ov_config

SPRK = "СП РК 4.02-101-2012"
CFG = ov_config.get


def valve_rx():
    """Марки дымовых и огнезадерживающих клапанов из справочника оборудования."""
    c = CFG()
    codes = sorted(c["smoke_codes"] | c["fire_codes"], key=len, reverse=True) or ["КПУ"]
    return re.compile(r"(?<![А-ЯA-Z])(?:" + "|".join(re.escape(x) for x in codes) + r")(?![А-ЯA-Z])")

# марки систем с учётом старых и латинских вариантов -> марка по ГОСТ 21.602
MARK_NORM = {"ПД": "ДП", "ВД": "ДВ", "DP": "ДП", "DV": "ДВ", "B": "В", "P": "П"}
SYS_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])(ДПЕ|ДВЕ|ПД|ВД|ДП|ДВ|ПЕ|ВЕ|П|В|У|К|B|P|DP|DV)(\d{1,2})"
                    r"(?![\d.,xхХ×*]|\s*[xхХ×*]\s*\d)")
DAMPER_RE = re.compile(r"КПУ|КПС|КЛОП|ОЗК|КДМ|КПД|огнезадерж|дымов\w*\s+клапан|клапан\w*\s+дым|"
                       r"противопожарн\w*\s+клапан|клапан\w*\s+противопожарн", re.I)
NC_RE = re.compile(r"Н[З3]|нормально\s+закрыт|КДМ|дымов", re.I)        # нормально закрытый / дымовой
NO_RE = re.compile(r"Н[ОO](?![А-ЯA-Z])|нормально\s+открыт", re.I)       # нормально открытый
# назначение слоёв — из справочника «Справочники\Слои.csv»
def stair_layer(name):
    return bool(CFG()["stair_rx"].search(name)) and not CFG()["not_stair_rx"].search(name)


def fire_layer(name):
    return bool(CFG()["fire_rx"].search(name))


def smoke_layer(name):
    return bool(CFG()["smoke_rx"].search(name))

# СП РК 4.02-101, прил. Ж.2: толщина листовой стали, мм
THK_ROUND = [(200, 0.5), (450, 0.6), (800, 0.7), (1250, 1.0), (1600, 1.2), (2000, 1.4)]
THK_RECT = [(250, 0.5), (1000, 0.7), (2000, 0.9)]


def norm_mark(m):
    return MARK_NORM.get(m, m)


def finding(group, clause, severity, text, fix, locs=None, sheet=None, check=False):
    return {"group": group, "norm": SPRK, "clause": clause, "severity": severity,
            "text": text + (" Проверьте по чертежу — замечание найдено автоматически по геометрии." if check else ""),
            "fix": fix, "sheet": sheet, "page": None, "file": None, "source": "авто",
            "kind": "tech", "locs": locs or []}


def loc(item, x, y):
    return {"file": item["file"], "path": item["path"], "page": item["page"], "x": x, "y": y,
            "sheet": item["label"]}


def text_items(sheet_obj, regex):
    """Строки текста листа, подходящие под regex: [(match, текст, cx, cy, контекст)]."""
    out = []
    for t in sheet_obj.texts:
        for m in regex.finditer(t["text"]):
            out.append((m, t["text"], t["cx"], t["cy"], t))
    return out


def callout_text(sheet_obj, t):
    """Текст строки вместе с соседними строками той же выноски."""
    import ov_check
    t.setdefault("half", ov_check.text_half(t))
    return " ".join(x["text"] for x in sheet_obj.texts
                    if x is t or (abs(x["cy"] - t["cy"]) < 60 and abs(x["cx"] - t["cx"]) < 60
                                  and ov_check.same_callout(t, dict(x, half=ov_check.text_half(x)))))


# ---------------------------------------------------------------- геометрия

def seg_intersect(a, b):
    """Точка пересечения отрезков a=(x1,y1,x2,y2), b=(...) или None."""
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-9:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return x1 + t * (x2 - x1), y1 + t * (y2 - y1)
    return None


def layer_segments(page, is_layer, min_len):
    segs = []
    for d in page.get_drawings():
        if d.get("type") == "f":
            continue
        if not is_layer(d.get("layer") or ""):
            continue
        for it in d["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if math.hypot(b.x - a.x, b.y - a.y) >= min_len:
                    segs.append((a.x, a.y, b.x, b.y))
            elif it[0] in ("re", "qu"):
                q = it[1].quad if it[0] == "re" else it[1]
                pts = [q.ul, q.ur, q.lr, q.ll]
                for p1, p2 in zip(pts, pts[1:] + pts[:1]):
                    if math.hypot(p2.x - p1.x, p2.y - p1.y) >= min_len:
                        segs.append((p1.x, p1.y, p2.x, p2.y))
    return segs


def clusters(boxes, gap):
    """Объединяет прямоугольники (x0,y0,x1,y1), расстояние между которыми меньше gap."""
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        out = []
        while boxes:
            b = boxes.pop()
            i = 0
            while i < len(boxes):
                c = boxes[i]
                if c[0] - gap <= b[2] and b[0] - gap <= c[2] and c[1] - gap <= b[3] and b[1] - gap <= c[3]:
                    b = [min(b[0], c[0]), min(b[1], c[1]), max(b[2], c[2]), max(b[3], c[3])]
                    boxes.pop(i)
                    merged = True
                else:
                    i += 1
            out.append(b)
        boxes = out
    return boxes


def duct_segments(sheet_obj, min_len):
    seen, out = set(), []
    for cell in sheet_obj.grid.cells.values():
        for s in cell:
            if id(s) in seen or s.len < min_len:
                continue
            seen.add(id(s))
            out.append(s)
    return out


# ---------------------------------------------------------------- проверки

def check_tech(plans, spec_text="", general_text=""):
    """plans: [{'file','path','page','label','title','sheet': ov_check.Sheet, 'scale': int|None, 'pdf_page'}]"""
    F = []
    plans = [p for p in plans if p.get("scale")]       # только листы-планы с найденным масштабом
    all_text = spec_text + " " + general_text + " " + " ".join(t["text"] for p in plans for t in p["sheet"].texts)

    # --- системы и клапаны на каждом плане
    for p in plans:
        sh = p["sheet"]
        k = 25.4 / 72 * p["scale"]                      # мм в натуре на пункт
        p["k"] = k
        p["systems"] = {}
        for m, txt, cx, cy, t in text_items(sh, SYS_RE):
            p["systems"].setdefault(norm_mark(m.group(1)) + m.group(2), []).append((cx, cy))
        p["dampers"] = []
        seen = set()
        for m, txt, cx, cy, t in text_items(sh, DAMPER_RE) + text_items(sh, valve_rx()):
            if id(t) in seen:
                continue
            seen.add(id(t))
            ctx = callout_text(sh, t)
            code = ov_config.equip_code(m.group(0)) if valve_rx().fullmatch(m.group(0)) else None
            if code in CFG()["smoke_codes"] or NC_RE.search(ctx):
                kind = "nc"
            elif NO_RE.search(ctx):
                kind = "no"
            else:
                kind = "?"
            p["dampers"].append((cx, cy, kind, ctx))

    floors = {}
    for p in plans:
        for s in p["systems"]:
            floors.setdefault(s, []).append(p)

    # 7.10.1 1): многоэтажные системы общеобменной вентиляции — огнезадерживающий клапан у стояка на каждом этаже
    miss_sys, miss_locs = [], []
    for s, ps in sorted(floors.items(), key=lambda kv: (re.match(r"\D+", kv[0]).group(0), int(re.search(r"\d+", kv[0]).group(0)))):
        mark = re.match(r"\D+", s).group(0)
        if mark not in ("П", "В", "К") or len({p["label"] for p in ps}) < 2:
            continue
        miss = []
        for p in ps:
            R = 4000 / p["k"]
            near = any(math.hypot(dx - x, dy - y) <= R for x, y in p["systems"][s] for dx, dy, kd, _ in p["dampers"]
                       if kd != "nc")
            if not near:
                x, y = p["systems"][s][0]
                miss.append(loc(p, x, y))
        if miss:
            miss_sys.append(f"{s} (листы {', '.join(sorted({str(m['sheet']) for m in miss}, key=lambda v: (len(v), v)))})")
            miss_locs += miss
    if miss_sys:
        sheets = ", ".join(sorted({str(m["sheet"]) for m in miss_locs}, key=lambda v: (len(v), v)))
        F.append(finding("Огнезадерживающие клапаны", "7.10.1 1)", "warn",
                         f"{len(miss_sys)} систем(ы) общеобменной вентиляции обслуживают несколько этажей, но рядом с их "
                         f"обозначениями на планах не найдены огнезадерживающие клапаны: {'; '.join(miss_sys)}.",
                         "На поэтажных сборных воздуховодах в местах присоединения к вертикальному коллектору "
                         "установите огнезадерживающие клапаны (нормально открытые) или воздушные затворы по 7.10.1 2); "
                         "добавьте их в спецификацию.", miss_locs, sheets, check=True))

    # 9.13 3), 3.5: системы вытяжной противодымной вентиляции — дымовые (нормально закрытые) клапаны
    smoke_ex = {s: ps for s, ps in floors.items() if s.startswith("ДВ")}
    for s, ps in sorted(smoke_ex.items()):
        miss, wrong = [], []
        for p in ps:
            R = 6000 / p["k"]
            x, y = p["systems"][s][0]
            if not any(kd == "nc" for *_, kd, _ in p["dampers"]):
                miss.append(loc(p, x, y))
            for dx, dy, kd, ctx in p["dampers"]:
                if kd == "no" and any(math.hypot(dx - sx, dy - sy) <= R for sx, sy in p["systems"][s]):
                    wrong.append((loc(p, dx, dy), ctx))
        if miss:
            sheets = ", ".join(sorted({m["sheet"] for m in miss}, key=lambda v: (len(str(v)), str(v))))
            F.append(finding("Противодымная вентиляция", "9.13 3), 3.5", "error",
                             f"Система дымоудаления {s} есть на листах {sheets}, но на этих листах не найдено ни одного "
                             "дымового (нормально закрытого) клапана.",
                             "Предусмотрите дымовые клапаны с автоматическим и дистанционным приводом "
                             "(для коридоров и холлов — с пределом огнестойкости не менее 0,5 ч, EI 30) "
                             "и укажите их на планах и в спецификации.", miss, sheets))
        for l, ctx in wrong:
            F.append(finding("Противодымная вентиляция", "9.13 3)", "error",
                             f"Рядом с системой дымоудаления {s} указан нормально открытый клапан: «{ctx[:80]}».",
                             "На системах дымоудаления применяют дымовые нормально закрытые клапаны (НЗ).",
                             [l], l["sheet"], check=True))

    # 9.19 5): приточная противодымная вентиляция — нормально закрытые клапаны
    if any(s.startswith("ДП") for s in floors):
        if not NC_RE.search(spec_text + " " + " ".join(d[3] for p in plans for d in p["dampers"])):
            ps = [p for s, pp in floors.items() if s.startswith("ДП") for p in pp]
            p = ps[0]
            F.append(finding("Противодымная вентиляция", "9.19 5)", "warn",
                             f"Есть системы подпора ({', '.join(sorted(s for s in floors if s.startswith('ДП')))}), "
                             "но ни на планах, ни в спецификации не найдены противопожарные нормально закрытые клапаны.",
                             "Для систем приточной противодымной вентиляции предусмотрите нормально закрытые клапаны "
                             "с пределом огнестойкости по 9.19 5) (0,5 ч или 2 ч для систем по 9.16 2).",
                             [loc(p, *p["systems"][next(s for s in p['systems'] if s.startswith('ДП'))][0])],
                             p["label"]))

    # 7.10.7 1), 9.13 2), 9.19 2): воздуховоды противодымных систем — класс П и предел огнестойкости
    smoke_sys = sorted(s for s in floors if s.startswith(("ДП", "ДВ")))
    if smoke_sys:
        doc_text = spec_text + " " + general_text
        p = floors[smoke_sys[0]][0]
        l0 = [loc(p, *p["systems"][smoke_sys[0]][0])]
        if not re.search(r"класс\w*\s*[«\"]?П", doc_text):
            F.append(finding("Воздуховоды", "7.10.7 1), 9.13 2)", "warn",
                             f"Для воздуховодов противодымных систем ({', '.join(smoke_sys)}) не указан класс плотности П.",
                             "Укажите в спецификации/общих данных: воздуховоды из негорючих материалов класса П.",
                             l0, p["label"]))
        if not re.search(r"огнезащит|предел\w*\s+огнестойк\w*\s+воздухов|EI\s*\d{2,3}[^\n]{0,60}воздухов|"
                         r"воздухов[^\n]{0,80}EI\s*\d{2,3}", doc_text, re.I):
            F.append(finding("Воздуховоды", "9.13 2), 9.19 2)", "error",
                             f"Для воздуховодов противодымных систем ({', '.join(smoke_sys)}) не указан предел "
                             "огнестойкости (огнезащитное покрытие, EI).",
                             "Укажите предел огнестойкости воздуховодов: дымоудаление — не менее 0,5 ч в пределах "
                             "отсека (0,75 ч для вертикальных при удалении из помещений), подпор — не менее 0,5 ч; "
                             "добавьте огнезащитное покрытие в спецификацию.", l0, p["label"]))

    # 7.10.14 1): транзитные воздуховоды через лестничные клетки
    for p in plans:
        page, k = p["pdf_page"], p["k"]
        stairs = layer_segments(page, stair_layer, 300 / k)
        if len(stairs) < 6:
            continue
        # лестничная клетка — компактная группа линий лестницы: от 2×2 до 12×12 м
        zones = [z for z in clusters([(min(a, c), min(b, d), max(a, c), max(b, d)) for a, b, c, d in stairs], 1200 / k)
                 if 2000 <= (z[2] - z[0]) * k <= 12000 and 2000 <= (z[3] - z[1]) * k <= 12000]
        ducts = [s for s in duct_segments(p["sheet"], 500 / k) if not smoke_layer(s.layer)]
        for z in zones:
            m = 300 / k
            inside = [s for s in ducts if z[0] + m < s.x1 < z[2] - m and z[1] + m < s.y1 < z[3] - m
                      and z[0] + m < s.x2 < z[2] - m and z[1] + m < s.y2 < z[3] - m]
            if len(inside) >= 2:
                lay = ", ".join(sorted({s.layer for s in inside}))
                F.append(finding("Прокладка воздуховодов", "7.10.14 1)", "warn",
                                 f"Воздуховоды (слой {lay}) проходят в зоне лестничной клетки.",
                                 "Транзитные воздуховоды через лестничные клетки не прокладывают (кроме подпора "
                                 "в эти лестничные клетки). Перенесите трассу или подтвердите, что это не лестничная клетка.",
                                 [loc(p, (z[0] + z[2]) / 2, (z[1] + z[3]) / 2)], p["label"], check=True))

    # 7.10.9 3), 7.10.13: пересечение противопожарных преград без огнезадерживающего клапана
    for p in plans:
        page, k = p["pdf_page"], p["k"]
        walls = layer_segments(page, fire_layer, 800 / k)
        if not walls:
            continue
        ducts = [s for s in duct_segments(p["sheet"], 200 / k) if not smoke_layer(s.layer)]
        hits = []
        for s in ducts:
            for w in walls:
                if max(s.x1, s.x2) < min(w[0], w[2]) or max(w[0], w[2]) < min(s.x1, s.x2) \
                        or max(s.y1, s.y2) < min(w[1], w[3]) or max(w[1], w[3]) < min(s.y1, s.y2):
                    continue
                pt = seg_intersect((s.x1, s.y1, s.x2, s.y2), w)
                if pt:
                    hits.append(pt)
        groups = clusters([(x, y, x, y) for x, y in hits], 800 / k)
        for g in groups:
            cx, cy = (g[0] + g[2]) / 2, (g[1] + g[3]) / 2
            R = 1500 / k
            if any(math.hypot(dx - cx, dy - cy) <= R * 2.5 for dx, dy, kd, _ in p["dampers"]):
                continue
            F.append(finding("Огнезадерживающие клапаны", "7.10.9 3), 7.10.13", "error",
                             "Воздуховод пересекает противопожарную преграду (слой стен с нормируемым пределом "
                             "огнестойкости), огнезадерживающий клапан рядом не обозначен.",
                             "Установите огнезадерживающий клапан в месте пересечения с пределом огнестойкости по 7.10.13 "
                             "(например, 0,5 ч при преграде 0,75 ч) или обоснуйте прокладку по 7.10.9 1)–2).",
                             [loc(p, cx, cy)], p["label"], check=True))

    # прил. Ж.2: толщина стали по спецификации; сечения с планов против спецификации
    spec_ducts = parse_spec_ducts(spec_text)
    for size, thk in sorted(spec_ducts.items()):
        need = thickness_for(size)
        if need and thk and abs(thk - need) > 0.001:
            F.append(finding("Воздуховоды", "прил. Ж, Ж.2", "warn",
                             f"В спецификации воздуховод {size.replace('x', '×')} принят толщиной δ={thk:g} мм, "
                             f"по таблице Ж.2 для этого размера — {need:g} мм.",
                             "Проверьте толщину стали по прил. Ж.2 (для систем дымоудаления толщину принимают по "
                             "расчёту огнестойкости).", [], "спецификация"))
    if spec_ducts:
        import ov_check
        # решётки, клапаны, диффузоры — не воздуховоды
        skip = re.compile(ov_check.DEFAULTS["skip_words"] + "|" + CFG()["codes_rx"], re.I)
        plan_sizes = {}
        for p in plans:
            for lab in p["sheet"].labels:
                m = re.search(r"(?<![\w.,])(\d{2,4})\s*[xXхХ×*]\s*(\d{2,4})(?![\d.,])", lab["text"])
                if m and not skip.search(lab.get("context", lab["text"])):
                    a, b = int(m.group(1)), int(m.group(2))
                    plan_sizes.setdefault(f"{a}x{b}", []).append(loc(p, lab["cx"], lab["cy"]))
        have = set(spec_ducts) | {f"{s.split('x')[1]}x{s.split('x')[0]}" for s in spec_ducts if "x" in s}
        for size, ls in sorted(plan_sizes.items()):
            if size not in have:
                sheets = ", ".join(sorted({l["sheet"] for l in ls}, key=lambda v: (len(str(v)), str(v))))
                F.append(finding("Воздуховоды", "", "warn",
                                 f"Сечение {size.replace('x', '×')} есть на планах ({len(ls)} подписей, листы {sheets}), "
                                 "но воздуховода такого размера нет в спецификации.",
                                 "Добавьте воздуховод в спецификацию или исправьте сечение на плане.",
                                 ls[:20], sheets))
    return F


def parse_spec_ducts(spec_text):
    """{'500x300': 0.7, 'Ø250': 0.5}: воздуховоды из спецификации с толщиной стали."""
    out = {}
    for m in re.finditer(r"[бδ]\s*=?\s*(\d+[.,]\d+)\s*мм[^\n]{0,120}?(?:(\d{2,4})\s*[xXхХ×*]\s*(\d{2,4})|[ØøØ∅⌀]\s*(\d{2,4}))",
                         spec_text):
        thk = float(m.group(1).replace(",", "."))
        size = f"{m.group(2)}x{m.group(3)}" if m.group(2) else f"Ø{m.group(4)}"
        out[size] = thk
    return out


def thickness_for(size):
    if size.startswith("Ø"):
        d = int(size[1:])
        for lim, t in THK_ROUND:
            if d <= lim:
                return t
        return None
    a, b = map(int, size.split("x"))
    side = max(a, b)
    if side > 2000:
        return None
    for lim, t in THK_RECT:
        if side <= lim:
            return t
    return None
