#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: проверка орфографии в чертежах.

Словарь — морфологический словарь русского языка pymorphy3 (все формы слов, около 5 млн),
плюс слова из нормативов в папке нормативов (технические термины: «огнезадерживающий» и т. п.),
плюс свой словарь «Справочники\\Словарь.txt» (по слову в строке: фамилии, названия, марки).

Проверяются: основная надпись каждого листа (наименования объекта, здания, листа, организации),
весь текст общих данных, спецификации и текстовых листов, надписи на планах и схемах.
Не проверяются: сокращения из заглавных букв, слова с цифрами и латиницей, слова короче 4 букв,
слова с заглавной буквы в середине строки (фамилии, улицы, районы).

check(album, norms=None, log=print) -> список замечаний в формате ov_album.finding.
"""

import os
import re
import subprocess
import sys

import ov_config

WORD = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")
RU = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
# сокращения, которые пишут без точки в таблицах
ABBR = {"компл", "охлаж", "кмпл", "станд", "примеч", "колич"}
USER_DICT = os.path.join(ov_config.FOLDER, "Словарь.txt")
MAX_WORDS = 60                      # не больше стольких разных слов в отчёте

_morph = None


def _load_morph(log=print):
    """pymorphy3; при первом запуске ставится сам (pip, один раз)."""
    global _morph
    if _morph is not None:
        return _morph or None
    try:
        import pymorphy3
    except ImportError:
        log("Орфография: устанавливаю словарь русского языка (pymorphy3, один раз)…")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", "pymorphy3"],
                           timeout=600, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            import site, importlib
            if site.getusersitepackages() not in sys.path:
                sys.path.append(site.getusersitepackages())
            importlib.invalidate_caches()
            import pymorphy3
        except Exception:
            _morph = False
            return None
    try:
        _morph = pymorphy3.MorphAnalyzer()
    except Exception:
        _morph = False
        return None
    return _morph


DEFAULT_DICT = """# Свой словарь для проверки орфографии: по слову в строке.
# Слово со звёздочкой в конце — все слова, которые так начинаются: воздухоотводчик* = воздухоотводчик, -а, -ами…
# Сюда добавляют фамилии, названия улиц и районов, марки и термины, которых нет в общем словаре.
воздухоотводчик*
спускник*
двухтрубн*
однотрубн*
термовспучивающ*
фреонопровод*
гофротруб*
незадымлени*
резьбозажимн*
хлораторн*
"""


def user_words():
    """Свой словарь: (слова, приставки для слов со звёздочкой). Создаётся при первом запуске."""
    if not os.path.isfile(USER_DICT):
        try:
            os.makedirs(os.path.dirname(USER_DICT), exist_ok=True)
            with open(USER_DICT, "w", encoding="utf-8-sig") as fo:
                fo.write(DEFAULT_DICT)
        except OSError:
            pass
    words, prefixes = set(), []
    try:
        with open(USER_DICT, encoding="utf-8-sig") as fo:
            for l in fo:
                w = l.strip().lower().replace("ё", "е")
                if not w or w.startswith("#"):
                    continue
                if w.endswith("*"):
                    prefixes.append(w[:-1])
                else:
                    words.add(w)
    except OSError:
        pass
    return words, tuple(prefixes)


def norms_words(norms):
    """Слова из текстов нормативов — технические термины, которых нет в общем словаре.
    Берутся слова, встретившиеся не меньше 3 раз: обрывки слов на переносах строк
    («вентиляци|онного») и ошибки распознавания сканов встречаются единично."""
    from collections import Counter
    cnt = Counter()
    for d in getattr(norms, "docs", None) or []:
        text = join_breaks(d.get("text", ""))
        cnt.update(w.lower().replace("ё", "е") for w in re.findall(r"[А-Яа-яЁё]{4,}", text))
    return {w for w, n in cnt.items() if n >= 3}


def join_breaks(text):
    """Склеивает переносы: «изме-\\nрания» → «измерания», мягкий перенос."""
    text = text.replace("\xad", "")
    return re.sub(r"([А-Яа-яЁё])-[ \t]*\n[ \t]*([а-яё])", r"\1\2", text)


class Checker:
    def __init__(self, morph, extra, prefixes=()):
        self.m, self.extra, self.prefixes, self.cache = morph, extra, prefixes, {}

    def known(self, w):
        w = w.lower().replace("ё", "е")
        if w in self.cache:
            return self.cache[w]
        ok = (w in self.extra or w in ABBR or (self.prefixes and w.startswith(self.prefixes))
              or self.m.word_is_known(w) or self.m.word_is_known(w.replace("е", "ё")))
        self.cache[w] = ok
        return ok

    def suggest(self, w):
        w = w.lower()
        cands = set()
        for i in range(len(w) + 1):
            a, b = w[:i], w[i:]
            if b:
                cands.add(a + b[1:])                                        # лишняя буква
                for c in RU:
                    cands.add(a + c + b[1:])                                # не та буква
            if len(b) > 1:
                cands.add(a + b[1] + b[0] + b[2:])                          # переставлены
            for c in RU:
                cands.add(a + c + b)                                        # пропущена буква
        good = [c for c in cands if c != w and len(c) > 2 and self.known(c)]
        common = lambda c: next((i for i, (x, y) in enumerate(zip(c, w)) if x != y), min(len(c), len(w)))
        good.sort(key=lambda c: (-common(c), abs(len(c) - len(w)), c))
        return good[:3]


def candidates(text):
    """Слова для проверки: (слово, с заглавной ли буквы в начале строки/предложения)."""
    for line in join_breaks(text).splitlines():
        first = True
        for m in WORD.finditer(line):
            token = m.group(0)
            prev = line[:m.start()].rstrip()
            sentence_start = first or prev.endswith((".", "!", "?", ":", "«", '"', "(", "–", "—"))
            first = False
            if re.search(r"[A-Za-z\d]", line[max(0, m.start() - 1):m.end() + 1]):
                continue                                                    # рядом латиница/цифры: марка
            if line[m.end():m.end() + 1] == "." and token[-1].lower() not in "аеёиоуыэюяйь":
                continue                                                    # сокращение: «компл.», «устан.»
            for part in token.split("-"):
                if len(part) < 4:
                    continue
                if part.isupper():
                    if len(part) < 7:
                        continue                                            # сокращение: ТОО, ГСЛ, ВРУ
                elif part[0].isupper() and not sentence_start:
                    continue                                                # имя собственное
                elif any(ch.isupper() for ch in part[1:]):
                    continue
                yield part, part[0].isupper()


def check(album, norms=None, log=print):
    import ov_album
    morph = _load_morph(log)
    if not morph:
        return [ov_album.finding("Орфография", "", "", "info",
                                 "Орфография не проверялась: не удалось загрузить словарь русского языка.",
                                 "Выполните один раз: python -m pip install pymorphy3")]
    words, prefixes = user_words()
    ck = Checker(morph, words | norms_words(norms), prefixes)
    found, capital = {}, set()                                              # слово -> листы
    for s in album.sheets:
        if s["kind"] in ("image", "cover"):
            continue
        st = s["stamp"]
        parts = [st.get(k, "") for k in ("project", "building", "title", "org")]
        parts.append(s["text"])
        for w, cap in candidates("\n".join(parts)):
            if not ck.known(w):
                found.setdefault(w, []).append(s)
                if cap:
                    capital.add(w)

    def locs(sheets):
        return [{"file": s["file"], "path": s["path"], "page": s["page"], "sheet": s["label"],
                 "x": None, "y": None, "needle": w} for s in sheets[:30]]

    def labels(sheets):
        return ", ".join(s["label"] for s in sheets[:12]) + ("…" if len(sheets) > 12 else "")

    F, other = [], []
    for w, sheets in sorted(found.items(), key=lambda kv: -len(kv[1])):
        uniq = list({(s["file"], s["page"]): s for s in sheets}.values())
        sug = [] if w in capital else ck.suggest(w)
        if not sug:
            other.append((w, uniq))             # имена, названия, термины — одним списком «к сведению»
            continue
        if len(F) >= MAX_WORDS:
            continue
        F.append({"group": "Орфография", "norm": "", "clause": "", "severity": "warn",
                  "text": f"«{w}» — опечатка? Возможно: «{' / '.join(sug)}».",
                  "fix": "Исправьте. Если слово верное (термин, название) — добавьте его в «Справочники\\Словарь.txt».",
                  "sheet": labels(uniq), "page": uniq[0]["page"] + 1, "file": uniq[0]["file"], "source": "авто",
                  "kind": "design", "locs": [dict(l, needle=w) for l in locs(uniq)]})
    if other:
        words = ", ".join(f"{w} (л. {labels(u)})" for w, u in other[:40]) + ("…" if len(other) > 40 else "")
        first = other[0][1][0]
        F.append({"group": "Орфография", "norm": "", "clause": "", "severity": "info",
                  "text": f"Слова, которых нет в словаре (имена, названия, термины — проверьте написание): {words}.",
                  "fix": "Верные слова добавьте в «Справочники\\Словарь.txt» (по слову в строке) — "
                         "в следующий раз они не попадут в отчёт.",
                  "sheet": labels([u[0] for _, u in other]), "page": first["page"] + 1, "file": first["file"],
                  "source": "авто", "kind": "design", "locs": []})
    log(f"  орфография: возможных опечаток {len(F) - bool(other)}, неизвестных слов {len(other)}")
    return F


if __name__ == "__main__":
    import ov_album
    a = ov_album.Album(sys.argv[1:])
    for f in check(a):
        print(f["sheet"][:40], "|", f["text"])
