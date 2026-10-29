#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: справочники, которые инженер правит сам в Excel.

  Справочники\\Оборудование.csv — марки из выносок: как называются в спецификации,
                                  что это за оборудование, сверять ли размер;
  Справочники\\Слои.csv         — какие слои считать воздуховодами, лестницами,
                                  противопожарными стенами.

Файлы — CSV с разделителем «;» в кодировке UTF-8: открываются двойным щелчком в Excel,
сохранять как «CSV UTF-8 (разделитель — точка с запятой)». Если файла нет, он создаётся
со значениями по умолчанию.
"""

import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "Справочники")
EQUIP_CSV = os.path.join(FOLDER, "Оборудование.csv")
LAYERS_CSV = os.path.join(FOLDER, "Слои.csv")

EQUIP_HEAD = ["Марка в выноске", "Как называется в спецификации (слова через |)", "Вид",
              "Сверять размер со спецификацией (да/нет)", "Пояснение"]
# Вид: дымовой клапан / огнезадерживающий клапан — используются в проверках по СП РК
EQUIP_DEFAULT = [
    ["АПН", "АПН|диффузор", "диффузор", "да", "4АПН, 2АПН — диффузоры"],
    ["SAD", "SAD|диффузор", "диффузор", "да", ""],
    ["ДПУ", "ДПУ|диффузор", "диффузор", "да", ""],
    ["ДКУ", "ДКУ|диффузор", "диффузор", "да", ""],
    ["RAR", "RAR|решетк|решётк", "решётка", "нет", "размеры решёток бывают разные"],
    ["RAG", "RAG|решетк|решётк", "решётка", "нет", ""],
    ["RAD", "RAD|решетк|решётк", "решётка", "нет", ""],
    ["SAR", "SAR|решетк|решётк", "решётка", "нет", ""],
    ["SAG", "SAG|решетк|решётк", "решётка", "нет", ""],
    ["АМН", "АМН|решетк|решётк", "решётка", "нет", ""],
    ["АДН", "АДН|решетк|решётк", "решётка", "нет", ""],
    ["АРН", "АРН|решетк|решётк", "решётка", "нет", ""],
    ["АДР", "АДР|решетк|решётк", "решётка", "нет", ""],
    ["EAL", "EAL|жалюзи|решетк|решётк", "решётка наружная", "нет", ""],
    ["ДК", "ДК|дроссел|заслонк|КВК", "дроссель-клапан / заслонка", "да", "ДК = дроссель-клапан, заслонка, КВК"],
    ["КВК", "КВК|заслонк|дроссел|ДК", "дроссель-клапан / заслонка", "да", ""],
    ["KSG", "KSG|шумоглуш", "шумоглушитель", "да", ""],
    ["КО", "КО|клапан обратн", "обратный клапан", "да", ""],
    ["КД", "КД|клапан дым|дымов|КДМ|НЗ", "дымовой клапан", "да", "клапан дымоудаления (нормально закрытый)"],
    ["КДМ", "КДМ|клапан дым|дымов", "дымовой клапан", "да", ""],
    ["КПУ", "КПУ|клапан противопожарн|огнезадерж", "огнезадерживающий клапан", "да",
     "НО — огнезадерживающий, НЗ — дымовой (определяется по маркировке)"],
    ["КПЖ", "КПЖ|клапан противопожарн|огнезадерж", "огнезадерживающий клапан", "да", ""],
    ["КПС", "КПС|клапан противопожарн|огнезадерж", "огнезадерживающий клапан", "да", ""],
    ["КЛОП", "КЛОП|клапан противопожарн|огнезадерж", "огнезадерживающий клапан", "да", ""],
    ["ОЗК", "ОЗК|огнезадерж", "огнезадерживающий клапан", "да", ""],
]

LAYERS_HEAD = ["Назначение", "Слова в имени слоя (через |)", "Пояснение"]
LAYERS_DEFAULT = [
    ["воздуховоды", r"возд|вент|duct|vent|приток|вытяж|подпор|подача|дым|pritok|vityazh|^h-|(?<![а-яa-z])ов(?![а-яa-z])",
     "слои, на которых нарисованы стенки воздуховодов"],
    ["не воздуховоды", r"text|текст|dim|разм|выноск|трубопр|pipe|san_|отверст|оборуд|oborud|воздухораспр|решет|grid|hatch|штрих",
     "исключения из первой строки"],
    ["лестницы", r"stair|лестн|lestn", "линии лестниц — для проверки 7.10.14"],
    ["не лестницы", r"hidden|скрыт", "исключения: скрытые линии лестниц"],
    ["противопожарные стены", r"противопож|fire\s*wall|firewall|пож\w*\s*стен|EI\s*\d",
     "стены и перегородки с нормируемым пределом огнестойкости — для проверки 7.10.9, 7.10.13"],
    ["противодымные системы", r"дым|подпор|smoke|ВД|ДВ|ПД|ДП", "слои воздуховодов дымоудаления и подпора"],
]

_cache = {"mtime": None, "data": None}


def _write(path, head, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fo:
        w = csv.writer(fo, delimiter=";")
        w.writerow(head)
        w.writerows(rows)


def _read(path, head, default):
    if not os.path.exists(path):
        _write(path, head, default)
        return [list(r) for r in default]
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fo:
        sample = fo.read(2048)
        fo.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        for i, r in enumerate(csv.reader(fo, delimiter=delim)):
            if i == 0 or not r or not r[0].strip():
                continue
            rows.append([c.strip() for c in r] + [""] * (len(head) - len(r)))
    return rows


def _rx(words, flags=re.I):
    try:
        return re.compile(words, flags)
    except re.error:                                  # строка из Excel с ошибкой — ищем слова как есть
        return re.compile("|".join(re.escape(w.strip()) for w in words.split("|") if w.strip()), flags)


def get():
    """Справочники (перечитываются, если файлы изменились)."""
    for p, h, d in ((EQUIP_CSV, EQUIP_HEAD, EQUIP_DEFAULT), (LAYERS_CSV, LAYERS_HEAD, LAYERS_DEFAULT)):
        if not os.path.exists(p):
            _write(p, h, d)
    mtime = (os.path.getmtime(EQUIP_CSV), os.path.getmtime(LAYERS_CSV))
    if _cache["mtime"] == mtime:
        return _cache["data"]
    equip = []
    for r in _read(EQUIP_CSV, EQUIP_HEAD, EQUIP_DEFAULT):
        code = r[0].upper()
        equip.append({"code": code, "spec": r[1] or re.escape(code), "kind": r[2].lower(),
                      "check_size": not r[3].lower().startswith("н"), "note": r[4]})
    layers = {r[0].lower(): r[1] for r in _read(LAYERS_CSV, LAYERS_HEAD, LAYERS_DEFAULT) if r[1]}
    codes = sorted({e["code"] for e in equip}, key=len, reverse=True)
    data = {
        "equip": {e["code"]: e for e in equip},
        # марка в выноске: «4АПН», «КПУ-1Н-НО», «RAR»; цифра перед маркой допускается
        "equip_re": re.compile(r"(?<![А-ЯA-Z])\d?(" + "|".join(re.escape(c) for c in codes) + r")(?![А-ЯA-Z])"),
        "codes_rx": "|".join(r"(?<![А-ЯA-Z])\d?" + re.escape(c) + r"(?![a-zа-я])" for c in codes),
        "smoke_codes": {e["code"] for e in equip if "дымов" in e["kind"]},
        "fire_codes": {e["code"] for e in equip if "огнезадерж" in e["kind"]},
        "layers": layers,
        "duct_layers": layers.get("воздуховоды", LAYERS_DEFAULT[0][1]),
        "exclude_layers": layers.get("не воздуховоды", LAYERS_DEFAULT[1][1]),
        "stair_rx": _rx(layers.get("лестницы", "stair|лестн")),
        "not_stair_rx": _rx(layers.get("не лестницы", "hidden")),
        "fire_rx": _rx(layers.get("противопожарные стены", LAYERS_DEFAULT[4][1])),
        "smoke_rx": _rx(layers.get("противодымные системы", LAYERS_DEFAULT[5][1])),
    }
    _cache.update(mtime=mtime, data=data)
    return data


def equip_code(typ):
    """Марка из выноски («4АПН», «КПУ-1Н») -> строка справочника или None."""
    eq = get()["equip"]
    t = re.sub(r"^\d", "", typ.upper())
    for code in sorted(eq, key=len, reverse=True):
        if t.startswith(code):
            return code
    return None


if __name__ == "__main__":
    d = get()
    print("Справочники:", FOLDER)
    print("Марок оборудования:", len(d["equip"]), "| дымовые:", d["smoke_codes"], "| огнезадерживающие:", d["fire_codes"])
