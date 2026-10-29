#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: база нормативных документов.

Читает PDF / TXT из папки нормативов, режет текст на пункты (5.3, 6.2.14 ...)
и даёт поиск по ним. Разобранный текст кэшируется в %APPDATA%\\ОВ-Проверка\\norms.json,
поэтому повторный запуск быстрый; при замене файла кэш обновляется сам.
"""

import json
import os
import re

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "Нормативная документация для проверки")
CACHE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ОВ-Проверка", "norms.json")
CACHE_VER = 7

RE_CODE = re.compile(r"(ГОСТ\s*Р?\s*\d+(?:\.\d+)*\s*[-—–]\s*\d{2,4}|"
                     r"С[ПН]\s*РК\s*\d+(?:\.\d+)*\s*[-—–]\s*\d+(?:\s*[-—–]\s*\d{4})?|"
                     r"СП\s*\d+(?:\.\d+)+\s*[-—–]?\s*\d{4}|СНиП\s*[\d.\-]+)", re.I)
# начало пункта: «6.2.14 Текст» в начале строки
RE_CLAUSE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3}){1,3})\.?\s+(?=[А-ЯЁA-Z«(])", re.M)
RE_APPENDIX = re.compile(r"^\s*Приложение\s+([А-ЯЁA-Z](?:-\d)?)\b", re.M)
RE_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)


def doc_code(name, first_text):
    """Обозначение документа: из имени файла, иначе с титульного листа."""
    for src in (name, first_text):
        m = RE_CODE.search(src.replace("\n", " "))
        if m:
            code = re.sub(r"\s*[-—–]\s*", "-", m.group(1))
            code = re.sub(r"\s+", " ", code).strip()
            return code.replace("ГОСТ ", "ГОСТ ").upper().replace("СНИП", "СНиП")
    return os.path.splitext(name)[0]


def read_pages(path):
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
        marks.append((m.start(), m.group(1)))
    for m in RE_APPENDIX.finditer(full):
        marks.append((m.start(), "Прил. " + m.group(1)))
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
        names = sorted(n for n in os.listdir(self.folder) if n.lower().endswith((".pdf", ".txt")))
        for k, name in enumerate(names):
            path = os.path.join(self.folder, name)
            st = os.stat(path)
            key = f"{name}|{st.st_size}|{int(st.st_mtime)}"
            if progress:
                progress(f"Норматив {k + 1}/{len(names)}: {name}")
            d = cache.get(key)
            if d is None:
                try:
                    pages = read_pages(path)
                except Exception as e:      # битый файл не должен ронять программу
                    d = {"file": name, "code": name, "title": "", "pages": 0, "clauses": [],
                         "text": "", "error": str(e)}
                else:
                    clauses, full = split_clauses(pages)
                    first = " ".join(pages[:2])
                    d = {"file": name, "code": doc_code(name, first), "title": doc_title(first),
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


def doc_title(first_text):
    """Наименование документа с титульного листа (строки заглавными буквами)."""
    skip = re.compile(r"СОВЕТ|МЕЖГОСУДАРСТВ|СТАНДАРТ$|ИЗДАНИЕ|КОМИТЕТ|РЕСПУБЛИК|ГОСУДАРСТВ|СВОД ПРАВИЛ|"
                      r"ЕРЕЖЕЛЕР|ҚАЗАҚСТАН|ҚҰРЫЛЫС|ЖЫЛЫТУ|КОНДИЦИОНЕРЛЕУ|СОДЕРЖАНИЕ|^ГОСТ|^СП |^ҚР", re.I)
    good = lambda l: (len(l) > 6 and l.upper() == l and re.search("[А-ЯЁ]{4}", l) and not skip.search(l)
                      and "..." not in l and not re.match(r"ВВЕДЕНИЕ|ПРЕДИСЛОВИЕ|\d", l))
    title = []
    for l in (x.strip() for x in first_text.splitlines()):
        if not l and not title:
            continue
        if good(l):
            title.append(l)                 # заглавие идёт подряд на нескольких строках
        elif title:
            break
    return " ".join(title)[:160]


if __name__ == "__main__":
    import sys
    n = Norms(sys.argv[1] if len(sys.argv) > 1 else None)
    n.load(force=True, progress=print)
    for s in n.summary():
        print(s)
