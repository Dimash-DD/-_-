#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: база нормативных документов.

Читает PDF, TXT и Word (.docx, .doc, .rtf) из папки нормативов и всех вложенных папок, режет текст на пункты (5.3, 6.2.14 ...)
и даёт поиск по ним. Разобранный текст кэшируется в %APPDATA%\\ОВ-Проверка\\norms.json,
поэтому повторный запуск быстрый; при замене файла кэш обновляется сам.
"""

import json
import os
import re

import ov_word

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "Нормативная документация для проверки")
CACHE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ОВ-Проверка", "norms.json")
CACHE_VER = 10

RE_CODE = re.compile(r"(ГОСТ\s*Р?\s*\d+(?:\.\d+)*\s*[-—–]\s*\d{2,4}|"
                     r"С[ПН]\s*РК\s*\d+(?:\.\d+)*\s*[-—–]\s*\d+(?:\s*[-—–]\s*\d{4})?|"
                     r"СП\s*\d+(?:\.\d+)+(?:\s*[-—–]\s*\d{4})?|СНиП\s*(?:РК\s*)?\d[\d.\-]*\d)", re.I)
# начало пункта: «6.2.14 Текст» в начале строки
RE_CLAUSE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3}){1,3})\.?\s+(?=[А-ЯЁA-Z«(])", re.M)
# пункты приказов и правил РК: «15. Текст» (одноуровневая нумерация)
RE_CLAUSE1 = re.compile(r"^\s*(\d{1,4})\.\s+(?=[А-ЯЁA-Z«(])", re.M)
RE_APPENDIX = re.compile(r"^\s*Приложение\s+([А-ЯЁA-Z](?:-\d)?)\b", re.M)
RE_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)


# собственные обозначения документов, которых нет в RE_CODE
RE_OWN = re.compile(r"(?<![А-Яа-яЁёA-Za-z])(МДС\s*\d+-\d+(?:\.\d+)*|"
                    r"(?:СТО|Р)\s*НП\s*[«\"“]?\s*АВОК\s*[»\"”]?\s*\d+(?:\.\d+)*\s*[-—–]\s*\d{4}|"
                    r"МСН\s*\d+(?:\.\d+)*\s*[-—–]\s*\d+\s*[-—–]\s*\d{4})", re.I)
RE_FOR = re.compile(r"^(Пособие|Методические\s+рекомендации|Рекомендации|Руководство|Комментарий)\s+к\s+", re.I)


def _latin_to_cyr(text):
    """Латинские буквы, набранные вместо русских в обозначениях («CП РК», «CНиП»)."""
    return re.sub(r"(?<![A-Za-z])[CС][ПH](?=\s*(?:РК|иП|\d))",
                  lambda m: "С" + ("П" if m.group(0)[1] == "П" else "Н"), text)


def _norm_code(code):
    code = re.sub(r"\s*[-—–]\s*", "-", code)
    code = re.sub(r"\s+", " ", code).strip().rstrip(".")
    code = re.sub(r"\s*[«\"“]?\s*АВОК\s*[»\"”]?\s*", " «АВОК» ", code, flags=re.I).strip()
    return code.upper().replace("СНИП", "СНиП")


def doc_code(name, first_text):
    """Обозначение документа.

    1. Обозначение в начале имени файла: «СП РК 4.02-101-2012 …», «ГОСТ 21.602-2016 …», «МДС 41-1.99 …».
    2. «Пособие к СНиП 3.05.01-85», «Методические рекомендации к СП 7.13130.2013» — вместе с приставкой.
    3. Собственное обозначение с титульного листа (СТО НП «АВОК» 1.05-2006 и т. п.); ссылки вида
       «к СНиП 2.04.05-91» и обозначения из середины имени файла не берутся — это другие документы.
    4. Иначе — имя файла.
    """
    stem = os.path.splitext(os.path.basename(name))[0]
    st = re.sub(r"[_\s]+", " ", stem).strip()
    st = re.sub(r"^SP(?=\s|\d)", "СП", st, flags=re.I)
    st = re.sub(r"^SN(?=\s|\d)", "СН", st, flags=re.I)
    st = re.sub(r"^(СП|СН)\s+RK(?=\s)", r"\1 РК", st, flags=re.I)
    st = re.sub(r"^(СП|СН|СНиП)\s+(?:РФ|RF)\s+", r"\1 ", st, flags=re.I)
    # «СП_РК_2_04_107_2022» → «СП РК 2.04-107-2022»
    st = re.sub(r"^(СП|СН)\s+РК\s+(\d)\s+(\d{2})\s+(\d{2,3})\s+(\d{4})(?=\s|$)", r"\1 РК \2.\3-\4-\5", st, flags=re.I)
    st = re.sub(r"^(СП|СН)\s+РК\s+(\d\.\d{2})\s+(\d{2,3})\s+(\d{4})(?=\s|$)", r"\1 РК \2-\3-\4", st, flags=re.I)
    text = _latin_to_cyr((first_text or "")[:1500]).replace("\n", " ")
    for rx in (RE_CODE, RE_OWN):
        m = rx.match(st)
        if m:
            code = _norm_code(m.group(1))
            if re.fullmatch(r"СП \d+\.\d+", code):          # «СП 60.13330» без года — год с титульного листа
                y = re.search(re.escape(code) + r"\s*[.-]\s*(\d{4})", text)
                if y:
                    code += "." + y.group(1)
            return code
    m = RE_FOR.match(st)
    if m:
        rest = st[m.end():]
        c = RE_CODE.match(rest) or RE_OWN.match(rest)
        if c:
            return m.group(0).strip()[:1].upper() + m.group(0).strip()[1:].lower() + " " + _norm_code(c.group(1))
    if re.search(r"Об\s+утверждении|ПРИКАЗЫВАЮ", text) or re.match(r"(Об утверждении|Приказ|Санитарн|Правила|Закон)", st, re.I):
        return stem                     # приказ РК: обозначения в тексте — ссылки на другие документы
    for rx in (RE_OWN, RE_CODE):
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 4):m.start()]
            if re.search(r"(?<![А-Яа-яЁё])к\s*$", before, re.I):        # «к СНиП …» — ссылка, не своё
                continue
            return _norm_code(m.group(1))
    return stem


NORM_EXT = (".pdf", ".txt") + ov_word.WORD_EXT


def read_pages(path):
    if path.lower().endswith(ov_word.WORD_EXT):
        return ov_word.read_word(path)
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8", errors="replace") as fo:
            return [fo.read()]
    doc = pymupdf.open(path)
    return [doc[i].get_text() for i in range(doc.page_count)]


def clean(text):
    text = text.replace("­", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"-\n(?=[а-яё])", "", text)          # переносы слов
    return text


def split_clauses(pages):
    """Режет документ на пункты: [{'n': '6.2.14', 'page': 31, 'text': ...}]."""
    marks = []
    full, offsets = "", []
    for i, t in enumerate(pages):
        offsets.append((len(full), i + 1))
        full += clean(t) + "\n"
    for m in RE_CLAUSE.finditer(full):
        marks.append((m.start(1), m.group(1)))       # start(1): без пробелов и перевода строки перед номером
    if len(marks) < 20:                     # нет пунктов вида 6.2.14 — приказ или правила с нумерацией «15.»
        single = [(m.start(1), m.group(1)) for m in RE_CLAUSE1.finditer(full)]
        if len(single) >= 50:
            marks = single
    for m in RE_APPENDIX.finditer(full):
        marks.append((m.start() + len(m.group(0)) - len(m.group(0).lstrip()), "Прил. " + m.group(1)))
    marks.sort()
    out = []
    for k, (pos, n) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(full)
        body = full[pos:end].strip()
        if len(body) < 25 or re.search(r"\.{8,}", body[:300]):     # строки оглавления
            continue
        page = max(p for off, p in offsets if off <= pos)
        out.append({"n": n, "page": page, "text": body[:4000]})
    return out, full


def list_files(folder):
    """PDF, TXT и Word в папке и во всех вложенных папках; пути относительно folder, через «/».

    Пропускаются скрытые и служебные папки (.git, __pycache__ и т. п.) и временные
    файлы Office «~$…».
    """
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__", "~")))
        for n in files:
            if n.lower().endswith(NORM_EXT) and not n.startswith(("~$", ".")):
                rel = os.path.relpath(os.path.join(root, n), folder)
                out.append(rel.replace(os.sep, "/"))
    return sorted(out, key=lambda r: (r.count("/"), r.lower()))


KIND_ORDER = ("sp", "gost", "sanpin", "other")


def doc_kind(file, code=""):
    """Вид документа для списка нормативов: sp — СП, СН, СНиП, МСН; gost — ГОСТ;
    sanpin — СанПиН и санитарно-эпидемиологические требования; other — АВОК, МДС, ПУЭ и прочие.

    Определяется по имени файла: обозначение, найденное в тексте, бывает ссылкой
    на другой документ (рекомендации АВОК со ссылкой на ГОСТ и т. п.).
    """
    name = os.path.basename(file)
    name = re.sub(r"[_\s]+", " ", name).strip()
    name = re.sub(r"^SP(?=\s|\d)", "СП", name, flags=re.I)
    name = re.sub(r"^SN(?=\s|\d)", "СН", name, flags=re.I)
    # сначала обозначение в начале имени: «СН РК … санитарно-технические системы» — это СН, а не СанПиН
    if re.match(r"(СП|СН|СНиП|МСН)(?![А-Яа-яЁё])", name, re.I):
        return "sp"
    if re.match(r"ГОСТ(?![А-Яа-яЁё])|ГОСТР", name, re.I):
        return "gost"
    if re.match(r"санпин|санитарн", name, re.I) or re.search(r"(?<![А-Яа-яЁё])СанПиН", name, re.I):
        return "sanpin"
    return "other"


def stems(text):
    return {w[:6] for w in RE_WORD.findall(text.lower()) if len(w) > 2}


class Norms:
    def __init__(self, folder=None):
        self.folder = folder or DEFAULT_DIR
        self.docs = []              # [{'file','code','title','pages','clauses','text'}]

    def load(self, force=False, progress=None):
        cache = {}
        if not force:
            try:
                with open(CACHE, encoding="utf-8") as fo:
                    data = json.load(fo)
                if data.get("ver") == CACHE_VER:
                    cache = data.get("files", {})
            except (OSError, ValueError):
                pass
        self.docs = []
        if not os.path.isdir(self.folder):
            return self.docs
        names = list_files(self.folder)
        for k, name in enumerate(names):
            path = os.path.join(self.folder, name)
            try:
                st = os.stat(path)
            except OSError:                 # файл удалён или недоступен во время чтения
                continue
            key = f"{name}|{st.st_size}|{int(st.st_mtime)}"
            if progress:
                progress(f"Норматив {k + 1}/{len(names)}: {name}")
            d = cache.get(key)
            if d is None:
                try:
                    pages = read_pages(path)
                except Exception as e:      # битый файл не должен ронять программу
                    d = {"file": name, "code": os.path.basename(name), "title": "", "pages": 0, "clauses": [],
                         "text": "", "error": str(e)}
                else:
                    clauses, full = split_clauses(pages)
                    first = " ".join(pages[:2])
                    code = doc_code(os.path.basename(name), first)
                    title = refine_title(doc_title(title_text(path, first), code), name, code)
                    d = {"file": name, "code": code, "title": title,
                         "pages": len(pages), "clauses": clauses, "text": full}
                cache[key] = d
            self.docs.append(d)
        keep = {f"{d['file']}|" for d in self.docs}
        cache = {k: v for k, v in cache.items() if any(k.startswith(p) for p in keep)}
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as fo:
                json.dump({"ver": CACHE_VER, "files": cache}, fo, ensure_ascii=False)
        except OSError:
            pass
        return self.docs

    def summary(self):
        return [{"file": d["file"], "code": d["code"], "title": d.get("title", ""),
                 "kind": doc_kind(d["file"], d["code"]),
                 "pages": d["pages"], "clauses": len(d["clauses"]), "error": d.get("error")}
                for d in self.docs]

    def find(self, code_part):
        """Документ, в обозначении или имени которого есть code_part (например '21.602')."""
        for d in self.docs:
            if code_part.lower() in (d["code"] + " " + d["file"]).lower():
                return d
        return None

    def search(self, query, k=10, code=None):
        q = stems(query)
        if not q:
            return []
        hits = []
        for d in self.docs:
            if code and code.lower() not in (d["code"] + d["file"]).lower():
                continue
            for c in d["clauses"]:
                s = stems(c["text"])
                score = len(q & s) / len(q)
                if query.lower() in c["text"].lower():
                    score += 1
                if score > 0.3:
                    hits.append((score, d["code"], c))
        hits.sort(key=lambda h: -h[0])
        return [{"code": code_, "n": c["n"], "page": c["page"], "text": c["text"][:1200],
                 "score": round(s, 2)} for s, code_, c in hits[:k]]

    def clause(self, code_part, n):
        d = self.find(code_part)
        if not d:
            return None
        for c in d["clauses"]:
            if c["n"] == n:
                return c
        return None

    def quote_ok(self, code_part, quote):
        """Есть ли цитата в тексте документа (с точностью до пробелов и регистра)."""
        d = self.find(code_part)
        if not d or not quote:
            return False
        norm = lambda s: re.sub(r"[\s«»\"“”„]+", " ", s.lower()).strip()
        q = norm(quote)
        return len(q) >= 12 and q[:120] in norm(d["text"])


def _line_text(chars):
    """Строка из символов PDF: пробел ставится по расстоянию между буквами, а не по тексту файла.

    В некоторых PDF заголовок набран вразрядку или кусками, и обычное чтение даёт
    «УСЛО ВНЫ Е ОБОЗНАЧЕНИЯ ЭЛЕМ ЕНТО В». Здесь пробел ставится только там,
    где промежуток заметно больше обычного промежутка между буквами в этой строке.
    """
    chars = [c for c in chars if c["c"].strip()]
    if not chars:
        return ""
    size = sorted(c["size"] for c in chars)[len(chars) // 2]
    gaps = [b["bbox"][0] - a["bbox"][2] for a, b in zip(chars, chars[1:])]
    if not gaps:
        return chars[0]["c"]
    base = max(0.0, sorted(gaps)[len(gaps) // 2])          # обычный промежуток между буквами
    out = chars[0]["c"]
    for g, c in zip(gaps, chars[1:]):
        if g > base + 0.18 * size or g < -size:            # шире обычного или переход назад
            out += " "
        out += c["c"]
    return out


def title_text(path, fallback=""):
    """Текст первых двух страниц для поиска наименования, со словами, собранными по геометрии."""
    if not path.lower().endswith(".pdf"):
        return fallback
    try:
        doc = pymupdf.open(path)
        lines = []
        for pno in range(min(2, doc.page_count)):
            for b in doc[pno].get_text("rawdict")["blocks"]:
                for l in b.get("lines", []):
                    if abs(l.get("dir", (1, 0))[1]) > 0.1:       # повёрнутый текст пропускаем
                        continue
                    chars = [dict(ch, size=sp["size"]) for sp in l["spans"] for ch in sp["chars"]]
                    t = _line_text(chars)
                    if t:
                        lines.append(t)
            lines.append("")
        return "\n".join(lines) or fallback
    except Exception:
        return fallback


def file_desc(name, code):
    """Описание из имени файла после обозначения: «СП_РК_3.02-109-2012_Многофункциональные здания» →
    «Многофункциональные здания». Пусто, если обозначение не в начале имени."""
    stem = re.sub(r"[_\s]+", " ", os.path.splitext(os.path.basename(name))[0]).strip()
    digits = re.findall(r"\d+", code)
    if not digits or not re.match(r"(СП|СН|СНиП|МСН|ГОСТ|SP|SN|МДС)", stem, re.I):
        return ""
    pos = 0
    for dg in digits:                       # пропускаем все числа обозначения по порядку
        m = re.compile(r"(?<!\d)" + dg + r"(?!\d)").search(stem, pos)
        if not m:
            return ""
        pos = m.end()
    desc = re.sub(r"^[\s.*\-–—_]+", "", stem[pos:])
    desc = re.sub(r"^Свод правил\.?\s*", "", desc, flags=re.I).strip()
    return desc if len(desc) >= 6 else ""


_LAT = str.maketrans("ABCEHKMOPTXYaceopxy", "АВСЕНКМОРТХУасеорху")
_KAZ_SUFFIX = re.compile(r"(СЫ|СІ|ЛАРЫ|ЛЕРІ|ДАРЫ|ДЕРІ|ТАРЫ|ТЕРІ|ДЕР|ДАР|ТАР|ТЕР|ЛАР|ЛЕР|НЫҢ|НІҢ|ДАРЫН|ДЕРІН|ЛАРЫН|ЛЕРІН|ЛАУ|ЛЕУ)$")


def refine_title(title, name, code):
    """Сверка наименования с описанием в имени файла.

    Если ни одно слово описания не встречается в наименовании (или слова слиплись без пробелов) —
    берётся описание из имени файла. Если перед совпавшими словами стоит казахский текст —
    он отрезается.
    """
    desc = file_desc(name, code)
    if not desc:
        return title
    dwords = [w.upper().translate(_LAT) for w in re.findall(r"[А-Яа-яЁё]{4,}", desc)]
    if not dwords:
        return title
    if not title or any(len(w) > 22 for w in re.split(r"[\s\-–—]+", title)):
        return desc
    def match(w):
        w = w.upper().translate(_LAT)
        for dw in dwords:
            if w[:5] == dw[:5] and (len(w) <= len(dw) + 1 or not _KAZ_SUFFIX.search(w)):
                return True
        return False
    toks = list(re.finditer(r"[^\s]+", title))
    hits = [t for t in toks if match(re.sub(r"[^А-Яа-яЁёA-Za-z]", "", t.group(0)))]
    if not hits:
        return desc
    i = hits[0].start()
    prefix = re.findall(r"[А-ЯЁA-Z]+", title[:i].upper())
    if i > 0 and any(_KAZ_SUFFIX.search(w) for w in prefix):   # перед русским наименованием — казахское
        return title[i:]
    return title


KAZ = re.compile(r"[ӘҒҚҢӨҰҮҺІӚҤҦәғқңөұүһіӛҥҧ]")     # казахские буквы: строка на казахском


def doc_title(first_text, code=""):
    """Наименование документа с титульного листа (русское, без казахского дубля)."""
    text = first_text or ""
    # выгрузки NormaCS / «Параграф»: «Название рус.: …»
    m = re.search(r"Название\s+рус\.?:\s*(.+?)(?:\n\s*\n|\n\s*Краткое содержание|\n\s*[А-ЯЁ][а-яё ]+:|$)", text, re.S)
    if m:
        return " ".join(m.group(1).split())[:160]
    # приказы РК (adilet.zan.kz): первая строка «Об утверждении …» до «Приказ …»
    m = re.search(r"(Об\s+утверждении\s.+?)(?=\s*Приказ\b|\s*Постановлени|\s*ПРИКАЗЫВАЮ|\s*В\s+соответствии|\n\s*\n|\n\s{3,})",
                  text[:1500], re.S)
    if m:
        return " ".join(m.group(1).split())[:160]
    skip = re.compile(r"СОВЕТ|МЕЖГОСУДАРСТВ|СТАНДАРТ$|ИЗДАНИЕ|КОМИТЕТ|РЕСПУБЛИК|ГОСУДАРСТВ|СВОД ПРАВИЛ|"
                      r"СТРОИТЕЛЬНЫЕ НОРМЫ|НОРМЫ РК$|ОФИЦИАЛЬНОЕ|^СН |ФЕДЕРАЛЬН|АГЕНТСТВ|МЕТРОЛОГ|РЕГУЛИРОВАНИ|"
                      r"НАЦИОНАЛЬН|ФЕДЕРАЦИ|РОССИЙСК|МИНИСТЕРСТВ|ОБОРОН|ЧРЕЗВЫЧАЙН|БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ|ИНСТИТУТ|ЖИЛИЩНО-КОММУНАЛЬН|ОНЛАЙН|КАЛЬКУЛЯТОР|WWW\.|"
                      r"ПАРТНЕРСТВ|ЛИКВИДАЦИ|БЕДСТВИ|ЗНАК ПОЧЕТА|НАУЧНО-ИССЛЕДОВАТЕЛЬСК|МЕТОДИЧЕСКАЯ ДОКУМЕНТАЦИЯ|САНТЕХНИИПРОЕКТ|СОГЛАСОВАН|УТВЕРЖД|ПРИКАЗЫВАЮ|"
                      r"^Р НП|^СТО |^РЕКОМЕНДАЦИИ АВОК$|^СТАНДАРТ АВОК$|^МОСКВА|"
                      r"ЕРЕЖЕЛЕР|ҚАЗАҚСТАН|ҚҰРЫЛЫС|ЖЫЛЫТУ|КОНДИЦИОНЕРЛЕУ|СОДЕРЖАНИЕ|^ГОСТ|^СП |^ҚР", re.I)
    good = lambda l: (len(l) > 6 and l.upper() == l and re.search("[А-ЯЁ]{4}", l) and not skip.search(l)
                      and not KAZ.search(l)
                      and "..." not in l and not re.match(r"ВВЕДЕНИЕ|ПРЕДИСЛОВИЕ|\d", l))
    title = []
    for l in (x.strip() for x in text.splitlines()):
        if not l and not title:
            continue
        if good(l):
            title.append(l)                 # заглавие идёт подряд на нескольких строках
        elif title:
            break
    if not title and code:                  # заглавие строчными буквами сразу под обозначением
        compact = lambda x: re.sub(r"[\s*—–-]", "", x).upper()
        lines = [x.strip() for x in text.splitlines()]
        for i, l in enumerate(lines[:40]):
            if l and compact(l) == compact(code):
                for nxt in lines[i + 1:i + 4]:
                    if not nxt or nxt.startswith("(") or KAZ.search(nxt) or re.match(
                            r"(Содержание|Предисловие|Введение|Издание|ОКС|Дата|Москва|Астана|\d)", nxt, re.I):
                        break
                    title.append(nxt if not title or title[-1].endswith((".", ",", ":")) else nxt)
                break
        return ". ".join(t.rstrip(".") for t in title)[:160]
    return " ".join(title)[:160]


if __name__ == "__main__":
    import sys
    n = Norms(sys.argv[1] if len(sys.argv) > 1 else None)
    n.load(force=True, progress=print)
    for s in n.summary():
        print(s)
