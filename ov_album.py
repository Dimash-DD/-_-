#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: проверки комплекта чертежей целиком.

  • основная надпись: шифр, наименование объекта, стадия, номера листов, подписи;
  • состав комплекта по ГОСТ 21.602 (общие данные, схемы, спецификация);
  • обозначения систем по ГОСТ 21.602, табл. 1;
  • соотношение сторон воздуховодов по СП РК 4.02-101, прил. Ж (размеры не ограничиваются);
  • выноски оборудования против спецификации;
  • масштабы по ГОСТ 21.602, табл. 3.

Каждое замечание — словарь:
  {group, norm, clause, sheet, page, file, severity, text, fix, source}
severity: error — нарушение, warn — требует внимания, info — к сведению.
"""

import difflib
import os
import re

import ov_config

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

MM = 72 / 25.4

GOST = "ГОСТ 21.602-2016"
SPRK = "СП РК 4.02-101-2012"
GOST101 = "ГОСТ 21.101"

# ---------------------------------------------------------------- основная надпись

LABEL_WORDS = re.compile(r"^(Изм\.?|Кол\.?\s*уч\.?|Лист|№\s*док\.?|Подп\.?|Дата|Стадия|Листов|Формат.*|"
                         r"Инв\.?.*|Взам\.?.*|Согласовано|Подп\.? и дата)$", re.I)
ROLE_RE = re.compile(r"^(Разраб|Разработ|Пров|Провер|Н\.?\s*контр|Нач\.?|ГИП|ГАП|Утв|Рук|Исполн|Т\.?\s*контр)", re.I)
ROLE_SPLIT = re.compile(r"^((?:Разраб\w*|Пров\w*|Н\.?\s*контр\w*|Нач\.?(?:\s*отд\.?)?|ГИП|ГАП|Утв\w*|Рук\w*|"
                        r"Исполн\w*|Т\.?\s*контр\w*)\.?)\s+(\S.*)$", re.I)
CODE_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё.]+(?:[-–][0-9A-Za-zА-Яа-яЁё.]+){1,6}")


def frame_corner(page):
    """Правый нижний угол рамки (= угол основной надписи) в координатах листа, как его видит человек.

    Обычно рамка отстоит от края листа на 5 мм, но при печати «вписать в лист», с полями
    принтера или со смещённой рамкой это не так. Угол находится по подписям граф штампа
    «Стадия» и «Листов» (форма 3 ГОСТ 21.101: их центры в 42,5 и 10 мм левее и 27,5 мм выше угла).
    Возвращает (x, y) или None, если подписей нет — тогда используется правило 5 мм.
    """
    W, H = page.rect.width, page.rect.height
    m = page.rotation_matrix
    cand = {"Стадия": [], "Листов": []}
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(sp["text"] for sp in ln["spans"]).strip()
            if t in cand:
                r = pymupdf.Rect(ln["bbox"]) * m
                if r.x0 > W * 0.5 and r.y0 > H * 0.5:          # только правая нижняя четверть листа
                    cand[t].append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    pts = [(x + 42.5 * MM, y + 27.5 * MM) for x, y in cand["Стадия"]]
    pts += [(x + 10 * MM, y + 27.5 * MM) for x, y in cand["Листов"]]
    pts = [(x, y) for x, y in pts if x < W + 3 * MM and y < H + 3 * MM]
    if not pts:
        return None
    x, y = max(pts, key=lambda q: q[0] + q[1])       # самый правый нижний штамп
    return min(x, W), min(y, H)


def stamp_lines(page, corner=None):
    """Строки текста в зоне основной надписи (форма 3: 185×55 мм в правом нижнем углу рамки).
    Координаты — в мм от левого верхнего угла штампа, как лист видит человек."""
    W, H = page.rect.width, page.rect.height
    cx, cy = corner or frame_corner(page) or (W - 5 * MM, H - 5 * MM)
    x0, y0 = cx - 185 * MM, cy - 55 * MM
    m = page.rotation_matrix
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            t = " ".join("".join(s["text"] for s in ln["spans"]).split())
            if not t:
                continue
            r = pymupdf.Rect(ln["bbox"]) * m
            cx, cy = (r.x0 - x0) / MM, ((r.y0 + r.y1) / 2 - y0) / MM
            if -3 <= cx <= 188 and -4 <= cy <= 57:
                out.append({"t": t, "x": cx, "x1": (r.x1 - x0) / MM, "y": cy,
                            "size": max(s["size"] for s in ln["spans"])})
    return out


def form6_corner(page):
    """Угол короткой основной надписи (форма 6 ГОСТ 21.101, высота 15 мм — последующие листы
    спецификации и текстовых документов). Ищется по подписи графы «Лист» у правого края
    (центр в 5 мм левее и 11,5 мм выше угла), иначе по «Изм.» (180 мм левее, 2,5 мм выше).
    None, если такой надписи нет."""
    W, H = page.rect.width, page.rect.height
    m = page.rotation_matrix
    pts = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(sp["text"] for sp in ln["spans"]).strip()
            if t not in ("Лист", "Изм.", "Изм"):
                continue
            r = pymupdf.Rect(ln["bbox"]) * m
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            x, y = (cx + 5 * MM, cy + 11.5 * MM) if t == "Лист" else (cx + 180 * MM, cy + 2.5 * MM)
            if W - 15 * MM < x < W + 3 * MM and H - 15 * MM < y < H + 3 * MM:
                pts.append((min(x, W), min(y, H), t == "Лист"))
    if not pts:
        return None
    best = max(pts, key=lambda q: (q[2], q[0] + q[1]))    # подпись «Лист» надёжнее
    return best[0], best[1]


def parse_form6(page, corner, st):
    """Шифр и номер листа из короткой основной надписи (форма 6)."""
    x0, y0 = corner[0] - 185 * MM, corner[1] - 15 * MM
    m = page.rotation_matrix
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            t = " ".join("".join(sp["text"] for sp in ln["spans"]).split())
            r = pymupdf.Rect(ln["bbox"]) * m
            cx, cy = (r.x0 - x0) / MM, ((r.y0 + r.y1) / 2 - y0) / MM
            if not t or not (-2 <= cy <= 17):
                continue
            if 63 <= cx < 172 and CODE_RE.search(t) and not LABEL_WORDS.match(t):
                st["code"] = st["code"] or t
            elif 172 <= cx <= 188 and cy > 6 and re.fullmatch(r"\d+(?:\.\d+)?", t):
                st["sheet"] = t
    st["found"] = bool(st["code"] or st["sheet"])
    st["form"] = "6"
    return st


def parse_stamp(page):
    W, H = page.rect.width, page.rect.height
    corner = frame_corner(page)
    st = {"found": False, "code": "", "project": "", "building": "", "title": "", "stage": "",
          "sheet": "", "sheets": "", "org": "", "roles": {}, "format": "", "form": "3" if corner else ""}
    if not corner:
        c6 = form6_corner(page)
        if c6:
            p = pymupdf.Point(c6[0] - 60 * MM, c6[1] - 10 * MM) * page.derotation_matrix
            st["xy"] = (p.x, p.y)
            return parse_form6(page, c6, st)
    corner = corner or (W - 5 * MM, H - 5 * MM)
    lines = stamp_lines(page, corner)
    st["found"] = bool(lines)
    # точка внутри штампа (над графой шифра) — в системе неповёрнутой страницы, для отметок в PDF
    cx, cy = corner
    p = pymupdf.Point(cx - 60 * MM, cy - 50 * MM) * page.derotation_matrix
    st["xy"] = (p.x, p.y)
    if not lines:
        return st
    zone = lambda l, xa, xb, ya, yb: xa <= l["x"] < xb and ya <= l["y"] < yb and not LABEL_WORDS.match(l["t"])
    code = [l for l in lines if zone(l, 60, 188, -4, 10.5) and CODE_RE.search(l["t"])]
    if code:
        st["code"] = max(code, key=lambda l: l["size"])["t"]
    join = lambda ls: " ".join(l["t"] for l in sorted(ls, key=lambda l: (round(l["y"]), l["x"])))
    st["project"] = join([l for l in lines if zone(l, 60, 188, 10.5, 25)])
    st["building"] = join([l for l in lines if zone(l, 60, 134, 25, 40)])
    st["title"] = join([l for l in lines if zone(l, 60, 134, 40, 57)])
    st["org"] = join([l for l in lines if zone(l, 134, 188, 40, 57)])
    st["stage"] = join([l for l in lines if zone(l, 134, 150, 28, 40)])
    st["sheet"] = join([l for l in lines if zone(l, 150, 165, 28, 40)])
    st["sheets"] = join([l for l in lines if zone(l, 165, 188, 28, 40)])
    rows = {}
    for l in lines:
        if l["x"] < 62 and l["y"] >= 22:
            rows.setdefault(round(l["y"] / 2.5), []).append(l)
    for row in rows.values():
        row.sort(key=lambda l: l["x"])
        if ROLE_RE.match(row[0]["t"]):
            role, rest = row[0]["t"], ""
            m = ROLE_SPLIT.match(role)          # «Н.контроль Халитов И.» — должность и фамилия одной строкой
            if m:
                role, rest = m.group(1), m.group(2)
            name = " ".join([rest] + [l["t"] for l in row[1:] if l["x"] < 45]).strip()
            st["roles"][role] = name
    m = re.search(r"Формат\s*([AА]\d)", page.get_text())
    st["format"] = m.group(1) if m else ""
    return st


def sheet_kind(title, text):
    t = (title or "").lower()
    text = " ".join(text.split())                  # переносы строк внутри заголовков таблиц
    if "общие данные" in t or ("общие данные" in text.lower()[:3000] and "ведомость" in text.lower()):
        return "general"
    if "спецификац" in t or ("наименование и техническая характеристика" in text.lower()):
        return "spec"
    if "схем" in t or "аксонометр" in t:
        return "scheme"
    if "разрез" in t:
        return "section"
    if "план" in t:
        return "plan"
    if "узел" in t or "узлы" in t:
        return "node"
    return "other"


def norm_name(s):
    s = re.sub(r"[«»\"“”„']", "", s.lower())
    return " ".join(re.sub(r"[^\w\s,.-]", " ", s).split())


# ---------------------------------------------------------------- обозначения систем

# марки по ГОСТ 21.602, табл. 1, а также старые / латинские варианты для замечаний
SYS_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])(ДПЕ|ДВЕ|ПД|ВД|ДП|ДВ|ПУ|ПЕ|ВЕ|П|В|У|К|B|P|DP|DV)"
                    r"(\d{1,2})(?![\d.,xхХ×*]|\s*[xхХ×*]\s*\d)")
GOST_MARKS = {"П", "В", "У", "А", "К", "ДП", "ДВ", "ПУ", "ПЕ", "ВЕ", "ДПЕ", "ДВЕ"}


def systems_in(text):
    return [(m.group(1), m.group(2)) for m in SYS_RE.finditer(text)]


# ---------------------------------------------------------------- оборудование в выносках

# марки оборудования и их названия в спецификации — из справочника «Справочники\Оборудование.csv»
SIZE_RE = re.compile(r"(\d{2,4})\s*[xXхХ×*]\s*(\d{2,4})|[ØøØ∅⌀]\s*(\d{2,4})")
SPEC_SIZE_RE = re.compile(r"(\d{2,4})\s*(?:мм)?\s*[xXхХ×*]\s*(\d{2,4})|[ØøØ∅⌀]\s*(\d{2,4})|диаметр\w*\s*(\d{2,4})", re.I)


def equip_base(typ):
    return ov_config.equip_code(typ) or re.sub(r"^\d", "", typ)


def spec_matches(spec, text):
    """Упоминания оборудования в спецификации. Марки (заглавными) ищутся целым словом,
    названия (строчными) — без учёта регистра и как начало слова."""
    out = []
    for w in (x.strip() for x in spec.split("|")):
        if not w:
            continue
        if w.upper() == w:
            rx = re.compile(r"(?<![А-ЯЁA-Zа-яёa-z])" + re.escape(w) + r"(?![А-ЯЁA-Zа-яёa-z])")
        else:
            rx = re.compile(r"(?<![а-яёa-z])" + w, re.I)
        out += list(rx.finditer(text))
    return out


def spec_index(spec_text, types):
    """{тип: множество размеров} по тексту спецификации: размеры берутся из позиции, где упомянут тип."""
    eq = ov_config.get()["equip"]
    idx = {}
    for typ in types:
        spec = eq[typ]["spec"] if typ in eq else typ
        sizes, found = set(), False
        for m in spec_matches(spec, spec_text):
            found = True
            # позиция спецификации: от упоминания типа до следующего наименования
            tail = spec_text[m.start():m.start() + 700]
            nxt = re.search(r"\n\d{1,3}\n[А-ЯЁA-Z][а-яё]", tail[40:])
            tail = tail[:40 + nxt.start()] if nxt else tail
            for s in SPEC_SIZE_RE.finditer(tail):
                if s.group(1):
                    a, b = int(s.group(1)), int(s.group(2))
                    sizes |= {f"{a}x{b}", f"{b}x{a}"}
                else:
                    sizes.add(f"Ø{s.group(3) or s.group(4)}")
        if found:
            idx[typ] = sizes
    return idx


def size_key(m):
    return f"{m.group(1)}x{m.group(2)}" if m.group(1) else f"Ø{m.group(3)}"


def norm_size_text(s):
    return re.sub(r"\s*[xXхХ×*]\s*", "x", s.replace("∅", "Ø").replace("⌀", "Ø").replace("ø", "Ø"))


# ---------------------------------------------------------------- проверки

DESIGN_GROUPS = {"Основная надпись", "Шифр", "Наименование", "Номера листов", "Состав комплекта",
                 "Обозначения систем", "Наименования листов", "Масштабы", "Орфография",
                 "Обозначения трубопроводов", "Обозначения элементов"}


def finding(group, norm, clause, severity, text, fix="", sheet=None, page=None, file=None, locs=None):
    """kind: design — оформление, tech — технические; locs — места на листах для отметок в PDF."""
    return {"group": group, "norm": norm, "clause": clause, "severity": severity, "text": text,
            "fix": fix, "sheet": sheet, "page": page, "file": file, "source": "авто",
            "kind": "design" if group in DESIGN_GROUPS else "tech", "locs": locs or []}


class Album:
    """Комплект: листы из одного или нескольких PDF."""

    def __init__(self, paths):
        self.sheets = []
        for path in paths:
            doc = pymupdf.open(path)
            for p in doc:
                text = p.get_text()
                st = parse_stamp(p)
                kind = sheet_kind(st["title"], text)
                if len(text.strip()) < 30:
                    kind = "image"              # скан или картинка — текста нет
                elif not st["form"] and p.number < 3 and re.search(
                        r"РАБОЧИЙ\s+ПРОЕКТ|РАБОЧАЯ\s+ДОКУМЕНТАЦИЯ|ПРОЕКТНАЯ\s+ДОКУМЕНТАЦИЯ|Альбом|Том\s+\d", text, re.I):
                    kind = "cover"              # обложка, титульный лист — основной надписи нет по ГОСТ
                elif st["form"] == "6" and kind != "spec":
                    kind = "text"               # последующий лист текстового документа
                self.sheets.append({"file": os.path.basename(path), "path": path, "page": p.number,
                                    "stamp": st, "text": text,
                                    "kind": kind,
                                    "label": st["sheet"] or f"стр. {p.number + 1}"})

    def project_guess(self):
        """Шифр и наименование, которые встречаются чаще всего."""
        main = [s for s in self.sheets if s["kind"] not in ("spec", "text", "cover", "image")
                and s["stamp"].get("form") != "6"]           # шифр комплекта — по чертежам, не по спецификации
        codes = [s["stamp"]["code"] for s in main if s["stamp"]["code"]]
        names = [s["stamp"]["project"] for s in main if s["stamp"]["project"]]
        most = lambda xs: max(set(xs), key=xs.count) if xs else ""
        return {"code": most(codes), "name": most(names),
                "building": most([s["stamp"]["building"] for s in main if s["stamp"]["building"]])}


def check_album(album, expected_code="", expected_name="", sheet_info=None, callouts=None, labels=None):
    """sheet_info: {(file, page): {'scale': .., 'scale_how': ..}} из проверки сечений;
    callouts: [(file, page, sheet, тип, размер, текст)] выноски оборудования;
    labels: [(file, page, sheet, kind, w, h)] подписи сечений воздуховодов."""
    F = []
    S = album.sheets
    guess = album.project_guess()
    code_ref = expected_code.strip() or guess["code"]
    name_ref = expected_name.strip() or guess["name"]
    def where(s, needle=None):
        xy = s["stamp"].get("xy") or (None, None)
        return {"sheet": s["label"], "page": s["page"] + 1, "file": s["file"],
                "locs": [{"file": s["file"], "path": s["path"], "page": s["page"], "sheet": s["label"],
                          "x": None if needle else xy[0], "y": None if needle else xy[1], "needle": needle}]}

    # --- основная надпись
    images = [s for s in S if s["kind"] == "image"]
    if images:
        F.append(finding("Основная надпись", GOST101, "", "info",
                         "Листы без текста (скан или картинка): основная надпись и содержание не проверялись.",
                         "Если это чертежи комплекта, выведите их в PDF из CAD-программы с текстом.",
                         **where(images[0]) | {"sheet": ", ".join(s["label"] for s in images)}))
    for s in S:
        st = s["stamp"]
        if s["kind"] in ("spec", "cover", "image"):  # у спецификации своя форма — проверяется отдельно ниже
            continue
        if st.get("form") == "6":        # короткая надпись последующих листов: только шифр
            if st["code"] and code_ref and not st["code"].replace(" ", "").startswith(code_ref.replace(" ", "")):
                F.append(finding("Шифр", GOST101, "", "error",
                                 f"Шифр «{st['code']}» не совпадает с шифром комплекта «{code_ref}».",
                                 f"Исправьте шифр на «{code_ref}».", **where(s)))
            continue
        if not st["found"]:
            F.append(finding("Основная надпись", GOST101, "", "warn",
                             "Основная надпись не найдена (нет текста в правом нижнем углу листа).",
                             "Проверьте рамку и штамп листа; текст штампа должен быть текстом, а не графикой.",
                             **where(s)))
            continue
        if not st["code"]:
            F.append(finding("Шифр", GOST101, "", "error", "В основной надписи нет обозначения (шифра) документа.",
                             f"Укажите шифр «{code_ref}».", **where(s)))
        elif code_ref and st["code"].replace(" ", "") != code_ref.replace(" ", ""):
            F.append(finding("Шифр", GOST101, "", "error",
                             f"Шифр «{st['code']}» не совпадает с шифром комплекта «{code_ref}».",
                             f"Исправьте шифр на «{code_ref}».", **where(s)))
        if st["code"] and not re.search(r"[-.]ОВ\d*$|ОВиК$", st["code"]):
            F.append(finding("Шифр", GOST, "4.2, 4.3", "warn",
                             f"Шифр «{st['code']}» не заканчивается маркой основного комплекта «ОВ».",
                             "Основной комплект рабочих чертежей отопления и вентиляции имеет марку ОВ.",
                             **where(s)))
        if not st["project"]:
            F.append(finding("Наименование", GOST101, "", "error",
                             "Не заполнено наименование объекта строительства в основной надписи.",
                             f"Укажите «{name_ref}».", **where(s)))
        elif name_ref:
            ratio = difflib.SequenceMatcher(None, norm_name(st["project"]), norm_name(name_ref)).ratio()
            if ratio < 0.97:
                F.append(finding("Наименование", GOST101, "", "error" if ratio < 0.8 else "warn",
                                 f"Наименование объекта отличается от эталона (совпадение {ratio:.0%}): "
                                 f"«{st['project']}».", f"Должно быть: «{name_ref}».", **where(s)))
        if guess["building"] and st["building"] and norm_name(st["building"]) != norm_name(guess["building"]):
            F.append(finding("Наименование", GOST101, "", "warn",
                             f"Наименование здания «{st['building']}» отличается от остальных листов "
                             f"(«{guess['building']}»).", "", **where(s)))
        if not st["title"]:
            F.append(finding("Основная надпись", GOST101, "", "error",
                             "Не заполнено наименование изображений на листе (графа 4/5).", "", **where(s)))
        if not st["org"]:
            F.append(finding("Основная надпись", GOST101, "", "warn",
                             "Не заполнено наименование организации-разработчика (графа 9).",
                             "Заполните графу так же, как на остальных листах.", **where(s)))
        if not st["stage"]:
            F.append(finding("Основная надпись", GOST101, "", "warn", "Не указана стадия.", "", **where(s)))
        roles = " ".join(st["roles"]).lower()
        if st["roles"] and not re.search(r"н\.?\s*контр", roles):
            F.append(finding("Основная надпись", GOST101, "", "warn",
                             "В основной надписи нет строки «Н. контр.» (нормоконтроль).",
                             "Добавьте фамилию нормоконтролёра и подпись.", **where(s)))
        for role, name in st["roles"].items():
            if not name.strip():
                F.append(finding("Основная надпись", GOST101, "", "warn",
                                 f"В строке «{role}» не указана фамилия.", "", **where(s)))
        t = st["title"].lower()
        if s["kind"] == "plan" and "план-схем" not in t and not re.search(r"отм|этаж|кровл|подвал|разрез|\d+\s*-\s*\d+", t):
            F.append(finding("Наименования листов", GOST, "6.1.12", "warn",
                             f"В наименовании плана «{st['title']}» нет отметки или номера этажа.",
                             "Пример: «План на отм. 0,000», «План 4-го этажа».", **where(s)))
        if s["kind"] == "scheme" and "вент" in t and not systems_in(st["title"]):
            F.append(finding("Наименования листов", GOST, "6.2.9", "warn",
                             f"В наименовании схем «{st['title']}» не указаны обозначения систем.",
                             "Пример: «Схемы систем П5, В8, К3».", **where(s)))
        info = (sheet_info or {}).get((s["file"], s["page"]))
        if (info and s["kind"] == "plan" and info.get("scale_how") == "не определён"
                and re.search(r"вент|воздух", st["title"], re.I)):
            # двухлинейные воздуховоды не найдены: это может быть и схема, и непривычные слои/линии в PDF
            F.append(finding("Сечения: подпись и чертёж", GOST, "6.1.12, 6.2.9", "info",
                             f"На листе «{st['title']}» не найдены двухлинейные воздуховоды — масштаб не определён, "
                             "сечения на этом листе не сверялись.",
                             "Если воздуховоды показаны двумя линиями, проверьте справочник «Слои» "
                             "(имена слоёв воздуховодов в этом PDF).", **where(s)))
        if info and info.get("scale") and s["kind"] in ("plan", "section") and info["scale"] not in (50, 100, 200):
            F.append(finding("Масштабы", GOST, "4.13, табл. 3", "warn",
                             f"Масштаб листа 1:{info['scale']} не входит в рекомендуемые для планов "
                             "(1:50; 1:100; 1:200).", "", **where(s)))

    # --- номера листов
    seen = {}
    prev = None
    for s in S:
        n = s["stamp"]["sheet"]
        if not s["stamp"]["found"] or s["kind"] in ("spec", "cover", "image", "text"):
            continue
        if not n:
            F.append(finding("Номера листов", GOST101, "", "warn", "Не указан номер листа.", "", **where(s)))
            continue
        if not n.isdigit():
            continue
        k = (s["file"], int(n))
        if k in seen:
            F.append(finding("Номера листов", GOST101, "", "error",
                             f"Номер листа {n} повторяется (также стр. {seen[k] + 1} файла).", "", **where(s)))
        seen[k] = s["page"]
        if prev is not None and prev[0] == s["file"] and int(n) != prev[1] + 1:
            F.append(finding("Номера листов", GOST101, "", "warn",
                             f"Нарушена нумерация: после листа {prev[1]} идёт лист {n}.",
                             "Проверьте, не пропущен ли лист и совпадает ли ведомость рабочих чертежей.",
                             **where(s)))
        prev = (s["file"], int(n))

    # --- состав комплекта
    kinds = {s["kind"] for s in S}
    general = [s for s in S if s["kind"] == "general"]
    first = S[0] if S else None
    if "general" not in kinds:
        F.append(finding("Состав комплекта", GOST, "4.3", "warn",
                         "В проверяемых файлах нет листа «Общие данные».",
                         "Основной комплект марки ОВ должен включать общие данные по рабочим чертежам. "
                         "Добавьте PDF с листами общих данных, чтобы проверить их состав.",
                         **(where(first) if first else {})))
    else:
        # пробелы и переносы схлопываются, ё → е, латинские буквы-двойники → русские («CП РК» с латинской C)
        gtext = " ".join(" ".join(s["text"] for s in general).split()).lower().replace("ё", "е")
        gtext = gtext.translate(str.maketrans("aceopxyk", "асеорхук"))
        req = [(r"ведомость рабочих чертежей", GOST101, "", "Ведомость рабочих чертежей основного комплекта"),
               (r"ведомость ссылочных", GOST101, "", "Ведомость ссылочных и прилагаемых документов"),
               (r"характеристик\w* систем", GOST, "5.1, 5.4", "Характеристика систем (форма 1)"),
               (r"основные показатели", GOST, "5.1, 5.5", "Основные показатели систем ОВ (форма 2)"),
               (r"расчетн\w* (?:параметр\w* |температур\w* )?наружн", GOST, "5.6",
                "Расчётные параметры наружного и внутреннего воздуха в общих указаниях"),
               (r"сп\s*рк\s*4\.02-101", GOST, "5.6", "Ссылка на СП РК 4.02-101-2012 в общих указаниях")]
        for key, norm, cl, what in req:
            if not re.search(key, gtext):
                F.append(finding("Состав комплекта", norm, cl, "warn",
                                 f"В общих данных не найдено: {what}.", "", **where(general[0])))
        if "ведомость спецификаций" in gtext:
            F.append(finding("Состав комплекта", GOST, "5.2", "warn",
                             "В общих данных марки ОВ приведена ведомость спецификаций — её не выполняют.",
                             "Удалите ведомость спецификаций из общих данных.", **where(general[0])))
    has_vent_plans = any(s["kind"] == "plan" and "вент" in s["stamp"]["title"].lower() for s in S)
    if has_vent_plans and "scheme" not in kinds:
        F.append(finding("Состав комплекта", GOST, "4.3, 6.2.8", "warn",
                         "Есть планы вентиляции, но нет листов со схемами систем вентиляции.",
                         "Добавьте схемы систем (с сечениями воздуховодов и расходами L).",
                         **(where(first) if first else {})))
    if "spec" not in kinds:
        F.append(finding("Состав комплекта", GOST, "4.2, 9.1", "info",
                         "Спецификация оборудования в проверку не добавлена — выноски оборудования "
                         "со спецификацией не сверялись.",
                         "Добавьте PDF спецификации в список файлов.", **(where(first) if first else {})))

    # --- обозначения систем
    by_sys = {}
    for s in S:
        if s["kind"] in ("spec",):
            continue
        for mark, num in systems_in(s["text"]):
            by_sys.setdefault((mark, num), []).append(s)
    old = {"ПД": "ДП", "ВД": "ДВ", "DP": "ДП", "DV": "ДВ", "B": "В", "P": "П"}
    reported = set()
    for (mark, num), sheets in sorted(by_sys.items()):
        if mark in GOST_MARKS or (mark, num) in reported:
            continue
        reported.add((mark, num))
        right = old.get(mark, "")
        if mark in ("B", "P"):
            text = (f"Обозначение системы «{mark}{num}» набрано латинской буквой "
                    f"(«{mark}» вместо кириллической «{right}»).")
            fix = f"Замените на «{right}{num}» — иначе поиск и сверка по системам не работают."
            sev = "warn"
        else:
            text = (f"Обозначение «{mark}{num}» не соответствует таблице 1: для "
                    f"{'приточной' if right == 'ДП' else 'вытяжной'} противодымной вентиляции марка «{right}».")
            fix = f"Замените «{mark}{num}» на «{right}{num}» на всех листах и в спецификации."
            sev = "error"
        labels_ = sorted({x["label"] for x in sheets}, key=lambda v: (len(str(v)), str(v)))
        f = finding("Обозначения систем", GOST, "4.5, табл. 1", sev,
                    text + f" Листы: {', '.join(labels_[:8])}{'…' if len(labels_) > 8 else ''}.", fix,
                    **where(sheets[0], needle=f"{mark}{num}"))
        seen_pages = set()
        f["locs"] = []
        for x in sheets:
            if (x["path"], x["page"]) not in seen_pages:
                seen_pages.add((x["path"], x["page"]))
                f["locs"] += where(x, needle=f"{mark}{num}")["locs"]
        F.append(f)
    scheme_text = " ".join(s["text"] for s in S if s["kind"] in ("scheme", "spec"))
    if any(s["kind"] == "scheme" for s in S):
        plan_sys = {(m, n) for (m, n), ss in by_sys.items() if any(x["kind"] == "plan" for x in ss)
                    and m in ("П", "В", "ДП", "ДВ", "ПД", "ВД", "К", "B", "P")}
        on_schemes = set(systems_in(scheme_text))
        missing = sorted(f"{m}{n}" for m, n in plan_sys - on_schemes)
        if missing:
            F.append(finding("Обозначения систем", GOST, "4.3, 6.2.8", "warn",
                             f"Системы есть на планах, но не найдены на схемах/в спецификации: "
                             f"{', '.join(missing[:30])}{'…' if len(missing) > 30 else ''}.",
                             "Проверьте, что для каждой системы выполнена схема.",
                             **where(next(s for s in S if s["kind"] == "scheme"))))

    # --- размеры воздуховодов (прил. Ж СП РК)
    # размеры воздуховодов сейчас изготавливают любые, поэтому проверяется только соотношение сторон
    bad_ratio = {}
    for file, page, sheet, kind, w, h, x, y, path in labels or []:
        l = {"file": file, "path": path, "page": page, "sheet": sheet, "x": x, "y": y}
        if kind == "rect" and h and max(w, h) / min(w, h) > 6.3:
            bad_ratio.setdefault(f"{w}×{h}", []).append(l)
    sheets_of = lambda ls: ", ".join(sorted({str(l["sheet"]) for l in ls}, key=lambda v: (len(v), v)))
    for size, ls in bad_ratio.items():
        F.append(finding("Сечения воздуховодов", SPRK, "прил. Ж, Ж.1", "error",
                         f"Сечение {size}: соотношение сторон больше 6,3. Листы: {sheets_of(ls)}.",
                         "Измените сечение так, чтобы большая сторона была не более 6,3 меньшей.",
                         sheet=sheets_of(ls), locs=ls[:30]))

    # --- выноски оборудования против спецификации
    spec_sheets = [s for s in S if s["kind"] == "spec"]
    spec_text = " ".join(s["text"] for s in spec_sheets)
    if spec_sheets and code_ref:
        codes = set(re.findall(r"\S+-ОВ[\w.]*\.С\w*", spec_text))
        if not codes:
            F.append(finding("Шифр", "ГОСТ 21.110", "", "warn",
                             "В спецификации не найдено обозначение вида «<шифр>.СО».",
                             f"Обозначение спецификации: «{code_ref}.СО».", **where(spec_sheets[0])))
        for c in codes:
            if not c.startswith(code_ref):
                bad = [s for s in spec_sheets if c in s["text"]] or spec_sheets[:1]
                F.append(finding("Шифр", "ГОСТ 21.110", "", "error",
                                 f"Обозначение спецификации «{c}» не соответствует шифру комплекта «{code_ref}».",
                                 f"Должно быть «{code_ref}.СО».", **where(bad[0], needle=c)
                                 | {"sheet": ", ".join(s["label"] for s in bad)}))
    if spec_text.strip() and callouts:
        groups = {}
        for file, page, sheet, typ, size, text, x, y, path in callouts:
            groups.setdefault((equip_base(typ), size), []).append(
                {"file": file, "path": path, "page": page, "sheet": sheet, "x": x, "y": y, "typ": typ})
        idx = spec_index(spec_text, {t for t, _ in groups})
        for (typ, size), items in sorted(groups.items()):
            sheets_ = ", ".join(sorted({str(i["sheet"]) for i in items}, key=lambda v: (len(v), v)))
            shown = f"{items[0]['typ']} {size}".strip()
            if typ not in idx:
                F.append(finding("Выноски и спецификация", GOST, "4.2, 9.3", "error",
                                 f"Оборудование «{shown}» есть на чертежах ({len(items)} шт., листы {sheets_}), "
                                 "но в спецификации такой позиции нет.",
                                 "Добавьте позицию в спецификацию или исправьте марку в выноске.",
                                 sheet=sheets_, locs=items[:30]))
            elif size and idx[typ] and size not in idx[typ] and ov_config.get()["equip"].get(typ, {}).get("check_size", True):
                have = ", ".join(sorted(x for x in idx[typ] if "x" not in x or int(x.split("x")[0]) >= int(x.split("x")[1]))[:12])
                F.append(finding("Выноски и спецификация", GOST, "4.2, 9.3", "warn",
                                 f"Типоразмер «{shown}» с чертежей ({len(items)} шт., листы {sheets_}) "
                                 f"не найден в спецификации. В спецификации для этого типа: {have or '—'}.",
                                 "Сверьте размер в выноске и в спецификации.",
                                 sheet=sheets_, locs=items[:30]))
    return merge(F)


def merge(F):
    """Одинаковые замечания с разных листов — одной строкой со списком листов."""
    out, idx = [], {}
    for f in F:
        key = (f["group"], f["norm"], f["clause"], f["severity"], f["text"], f["fix"], f["file"])
        if key in idx:
            g = idx[key]
            if f["sheet"] and str(f["sheet"]) not in g["sheets"]:
                g["sheets"].append(str(f["sheet"]))
            g["locs"] = g["locs"] + f.get("locs", [])
            continue
        g = dict(f, sheets=[str(f["sheet"])] if f["sheet"] else [])
        idx[key] = g
        out.append(g)
    for g in out:
        if len(g["sheets"]) > 1:
            g["sheet"] = ", ".join(g["sheets"][:12]) + ("…" if len(g["sheets"]) > 12 else "")
        g.pop("sheets")
    return out


def collect_callouts(sheet_obj, file, page, sheet_label, path=None):
    """Выноски оборудования с листа (по строкам текста рядом с размером)."""
    out = []
    for lab in sheet_obj.labels:
        ctx = lab.get("context", lab["text"])
        m = ov_config.get()["equip_re"].search(ctx)
        if not m:
            continue
        sm = SIZE_RE.search(lab["text"])
        size = size_key(sm) if sm else ""
        out.append((file, page, sheet_label, m.group(1), size, ctx, lab["cx"], lab["cy"], path))
    return out
