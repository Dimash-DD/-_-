#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: чтение документов Word для базы нормативов.

  .docx — читается напрямую (это zip с XML), без дополнительных библиотек;
          страницы делятся по разрывам, которые сохранил Word при последнем сохранении;
  .doc  — если внутри на самом деле HTML (так сохраняются документы с adilet.zan.kz),
          текст берётся из HTML; настоящий старый .doc и .rtf сначала конвертируются в .docx
          через установленный Microsoft Word, а если его нет — через LibreOffice.

read_word(path) -> список страниц (строк).
"""

import html
import html.parser
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WORD_EXT = (".docx", ".doc", ".rtf")


# ---------------------------------------------------------------- .docx

def _para_text(p, pages):
    """Текст абзаца; разрыв страницы внутри абзаца начинает новую страницу."""
    buf = []
    for el in p.iter():
        tag = el.tag
        if tag == W + "t":
            buf.append(el.text or "")
        elif tag == W + "tab":
            buf.append("\t")
        elif tag == W + "br":
            if el.get(W + "type") == "page":
                pages[-1].append("".join(buf))
                buf = []
                pages.append([])
            else:
                buf.append("\n")
        elif tag == W + "lastRenderedPageBreak":
            if buf and "".join(buf).strip():
                pages[-1].append("".join(buf))
                buf = []
            pages.append([])
        elif tag == W + "noBreakHyphen":
            buf.append("-")
    return "".join(buf)


def _walk(node, pages):
    for el in node:
        if el.tag == W + "p":
            t = _para_text(el, pages)
            pages[-1].append(t)
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                cells = []
                for tc in tr.findall(W + "tc"):
                    sub = [[]]
                    _walk(tc, sub)
                    cells.append(" ".join(" ".join(x for x in pg if x.strip()) for pg in sub).strip())
                pages[-1].append(" | ".join(cells))
        elif el.tag in (W + "sdt", W + "sdtContent", W + "customXml", W + "smartTag"):
            _walk(el, pages)


def read_docx(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    pages = [[]]
    _walk(body, pages)
    out = ["\n".join(pg) for pg in pages]
    out = [p for p in out if p.strip()] or [""]
    return out


# ---------------------------------------------------------------- .doc в виде HTML

class _HTMLText(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _decode(raw):
    m = re.search(rb"charset\s*=\s*[\"']?([\w-]+)", raw[:4000], re.I)
    for enc in ([m.group(1).decode("ascii", "ignore")] if m else []) + ["utf-8", "cp1251"]:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_html(raw):
    p = _HTMLText()
    p.feed(_decode(raw))
    text = html.unescape("".join(p.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return [text.strip()]


# ---------------------------------------------------------------- конвертация старого .doc / .rtf

def _convert_word(src, dst_dir):
    """Конвертация через Microsoft Word (Windows, без окна)."""
    if sys.platform != "win32":
        return None
    dst = os.path.join(dst_dir, "doc.docx")
    q = lambda s: "'" + s.replace("'", "''") + "'"
    ps = ("$ErrorActionPreference='Stop';$w=New-Object -ComObject Word.Application;$w.Visible=$false;"
          "$w.DisplayAlerts=0;try{$d=$w.Documents.Open(" + q(os.path.abspath(src)) + ",$false,$true);"
          "$d.SaveAs2(" + q(dst) + ",16);$d.Close($false)}finally{$w.Quit()}")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       timeout=180, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return dst if os.path.isfile(dst) else None


def _find_soffice():
    cands = [shutil.which("soffice"), shutil.which("libreoffice")]
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if base:
            cands.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    return next((c for c in cands if c and os.path.isfile(c)), None)


def _convert_libreoffice(src, dst_dir):
    exe = _find_soffice()
    if not exe:
        return None
    work = os.path.join(dst_dir, "in" + os.path.splitext(src)[1].lower())
    shutil.copyfile(src, work)                 # LibreOffice плохо переносит кириллицу и пробелы в путях
    try:
        subprocess.run([exe, "--headless", "--norestore", "--convert-to", "docx", "--outdir", dst_dir, work],
                       timeout=180, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    dst = os.path.join(dst_dir, "in.docx")
    return dst if os.path.isfile(dst) else None


def convert_to_docx_pages(src):
    with tempfile.TemporaryDirectory(prefix="ov-word-") as tmp:
        for conv in (_convert_word, _convert_libreoffice):
            dst = conv(src, tmp)
            if dst:
                return read_docx(dst)
    raise RuntimeError("Не удалось прочитать файл Word: нужен установленный Microsoft Word или LibreOffice. "
                       "Можно сохранить документ как .docx или PDF.")


# ---------------------------------------------------------------- общее

def read_word(path):
    with open(path, "rb") as fo:
        head = fo.read(4096)
    if head.startswith(b"PK"):                              # .docx (в том числе переименованный в .doc)
        return read_docx(path)
    sniff = head.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if sniff.startswith((b"<html", b"<!doctype", b"<?xml", b"<meta", b"<head")) or b"<html" in sniff[:600]:
        with open(path, "rb") as fo:
            return read_html(fo.read())
    return convert_to_docx_pages(path)                      # настоящий .doc (OLE) или .rtf


if __name__ == "__main__":
    for a in sys.argv[1:]:
        pgs = read_word(a)
        print(f"{a}: страниц {len(pgs)}, символов {sum(len(p) for p in pgs)}")
        print(pgs[0][:600])
