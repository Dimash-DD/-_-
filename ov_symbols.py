#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: условные и буквенно-цифровые обозначения.

Справочник — «Справочники\\Обозначения.csv» (из ГОСТ 21.205-2016 и ГОСТ 21.602-2016).
Проверяется по тексту листов:

  • трубопроводы (ГОСТ 21.205, табл. 17, 18, 20): Т1/Т2, Т11–Т29, Т3/Т4, …, Х1–Х5, Г1–Г7:
      – несуществующие номера (Т9, Т10, Т20, Т30, …);
      – латинские буквы вместо русских (T1, X1);
      – подающий без обратного: Т1 без Т2, Т11 без Т21, Т3 без Т4, Х1 без Х2;
  • марки элементов систем (ГОСТ 21.602, табл. 2): Ст, ГСт, ГВ, КП, КР, ЛП, ЛВ, О —
      неправильное написание: «СТ1», «Ст.1», «Ст-1», «Ст 1», «Cт1» с латинской C.

Буквы В и К в ОВ — это системы вентиляции и кондиционирования (ГОСТ 21.602, табл. 1),
поэтому трубопроводы водопровода и канализации (В1, К1) здесь не проверяются.
Графические условные обозначения (значки на схемах) по тексту PDF не распознаются.
"""

import csv
import os
import re

import ov_config

CSV = os.path.join(ov_config.FOLDER, "Обозначения.csv")
G205, G602 = "ГОСТ 21.205-2016", "ГОСТ 21.602-2016"

PIPE = re.compile(r"(?<![\wА-Яа-яЁё.])([ТХГ])(\d{1,3})(Н)?(?:\.\d+)?(?![\wА-Яа-яЁё\d,]|\.\d|\s*[xхХ×]\s*\d)")
PIPE_LAT = re.compile(r"(?<![\w.])([TX])(\d{1,2})(?![\w\d,]|\.\d)")
ELEM_BAD = re.compile(r"(?<![\wА-Яа-яЁё])(?:(СТ|ГСТ)(\d{1,3}[А-Я]?)|(Ст|ГСт)[.\-\s](\d{1,3}[А-Я]?)|(C[тТ]|ГC[тТ])(\d{1,3}[А-Я]?))(?![\d])")
PIPE_CONTEXT = re.compile(r"подающ|обратн|теплонос|отоплен|трубопров|циркуляц|холодонос|хладаг", re.I)

# допустимые номера трубопроводов
T_OK = set(range(1, 9)) | {n for d in range(1, 10) for n in range(d * 10 + 1, d * 10 + 10)}
PAIRS = [(1, 2), (3, 4), (5, 6)] + [(10 + i, 20 + i) for i in range(1, 10)] + \
        [(30 + i, 40 + i) for i in range(1, 10)] + [(50 + i, 60 + i) for i in range(1, 10)]


def names():
    """Обозначение → наименование из справочника (для текста замечаний)."""
    out = {}
    try:
        with open(CSV, encoding="utf-8-sig") as fo:
            for row in csv.DictReader(fo, delimiter=";"):
                out[row["Обозначение"].strip()] = row["Наименование"].strip()
    except (OSError, KeyError):
        pass
    return out


def _name(code, nm):
    if code in nm:
        return nm[code]
    m = re.match(r"([ТХГ])(\d+)", code)
    if m and m.group(1) == "Т":
        n = int(m.group(2))
        for key in nm:
            r = re.match(r"Т(\d+)–Т(\d+)", key)
            if r and int(r.group(1)) <= n <= int(r.group(2)):
                return nm[key]
    return ""


def check(album, log=print):
    from ov_album import finding
    nm = names()
    sheets = [s for s in album.sheets if s["kind"] not in ("image", "cover")]
    found, latin, elem = {}, {}, {}
    for s in sheets:
        text = s["text"]
        for m in PIPE.finditer(text):
            found.setdefault((m.group(1), int(m.group(2))), []).append(s)
        if PIPE_CONTEXT.search(text):
            for m in PIPE_LAT.finditer(text):
                latin.setdefault(m.group(0), []).append(s)
        for m in ELEM_BAD.finditer(text):
            elem.setdefault(m.group(0), []).append(s)

    def uniq(ss):
        return list({(x["file"], x["page"]): x for x in ss}.values())

    def loc(ss, needle):
        ss = uniq(ss)
        labels = ", ".join(str(x["label"]) for x in ss[:10]) + ("…" if len(ss) > 10 else "")
        return {"sheet": labels, "page": ss[0]["page"] + 1, "file": ss[0]["file"],
                "locs": [{"file": x["file"], "path": x["path"], "page": x["page"], "sheet": x["label"],
                          "x": None, "y": None, "needle": needle} for x in ss[:30]]}

    F = []
    # несуществующие номера теплопроводов
    for (L, n), ss in sorted(found.items()):
        if L == "Т" and n not in T_OK:
            F.append(finding("Обозначения трубопроводов", G205, "табл. 17", "error",
                             f"Обозначение «Т{n}» не предусмотрено ГОСТ 21.205: теплопроводы — Т1…Т8, "
                             f"Т11…Т19, Т21…Т29 и т. д. (номера, кратные 10, и Т9 не применяются).",
                             "Примите обозначение по таблице 17 ГОСТ 21.205 (для прочих теплопроводов — Т91…Т99).",
                             **loc(ss, f"Т{n}")))
    # подающий без обратного
    have = {(L, n) for L, n in found}
    for a, b in PAIRS:
        if ("Т", a) in have and ("Т", b) not in have:
            gvs = a == 3 or 31 <= a <= 39                   # ГВС бывает без циркуляции — только к сведению
            what = "циркуляционного" if gvs else "обратного"
            F.append(finding("Обозначения трубопроводов", G205, "табл. 17", "info" if gvs else "warn",
                             f"Есть «Т{a}» ({_name(f'Т{a}', nm)}), но нет парного {what} «Т{b}».",
                             f"Проверьте обозначения: к «Т{a}» обычно идёт «Т{b}».", **loc(found[("Т", a)], f"Т{a}")))
    if ("Х", 1) in have and ("Х", 2) not in have:
        F.append(finding("Обозначения трубопроводов", G205, "табл. 18", "warn",
                         "Есть «Х1» (холодоноситель подающий), но нет обратного «Х2».", "",
                         **loc(found[("Х", 1)], "Х1")))
    # латинские буквы
    for tok, ss in sorted(latin.items()):
        ru = {"T": "Т", "X": "Х"}[tok[0]] + tok[1:]
        F.append(finding("Обозначения трубопроводов", G205, "табл. 14", "warn",
                         f"«{tok}» набрано латинской буквой — по ГОСТ 21.205 обозначение трубопровода «{ru}» "
                         f"(русская буква).",
                         f"Замените на «{ru}», иначе поиск по обозначениям не работает.", **loc(ss, tok)))
    # марки элементов
    for tok, ss in sorted(elem.items()):
        m = ELEM_BAD.match(tok)
        num = m.group(2) or m.group(4) or m.group(6)
        base = "ГСт" if tok.upper().startswith(("ГСТ", "ГC")) else "Ст"
        right = base + num
        why = "латинская «C»" if "C" in tok else "лишний знак между маркой и номером" if m.group(4) else "прописная «Т»"
        F.append(finding("Обозначения элементов", G602, "4.6, табл. 2", "warn",
                         f"Марка «{tok}» записана не по ГОСТ 21.602 ({why}): должно быть «{right}».",
                         f"Исправьте на «{right}».", **loc(ss, tok)))
    log(f"  обозначения трубопроводов и элементов: замечаний {len(F)}")
    return F
