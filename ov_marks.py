#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: PDF с комментариями замечаний.

Для каждого проверенного PDF создаётся копия «<имя>_ЗАМЕЧАНИЯ.pdf»: в местах замечаний —
цветной кружок с номером и всплывающий комментарий (текст замечания, требование нормы,
как исправить). Комментарии видны в любом просмотрщике PDF (Acrobat, Edge, Foxit) и
в списке комментариев.

Координаты мест — в системе неповёрнутой страницы; если у места указан только текст
(needle), он ищется на странице, и найденные координаты записываются обратно в замечание.
"""

import os

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

COLORS = {"error": (0.86, 0.15, 0.12), "warn": (0.93, 0.55, 0.0), "info": (0.45, 0.45, 0.5)}
SEV_RU = {"error": "Нарушение", "warn": "Внимание", "info": "К сведению"}


def comments_path(path, outdir=None):
    base = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(outdir or os.path.dirname(os.path.abspath(path)), base + "_ЗАМЕЧАНИЯ.pdf")


def resolve_locs(findings, docs):
    """Находит координаты для мест, заданных текстом; убирает места вне известных файлов."""
    for f in findings:
        out = []
        for l in f.get("locs", []):
            doc = docs.get(l.get("path"))
            if doc is None or l.get("page") is None or l["page"] >= doc.page_count:
                continue
            if l.get("x") is None and l.get("needle"):
                hits = doc[l["page"]].search_for(l["needle"])
                if not hits:
                    continue
                for r in hits[:6]:
                    out.append(dict(l, x=(r.x0 + r.x1) / 2, y=(r.y0 + r.y1) / 2))
                continue
            if l.get("x") is None:
                continue
            out.append(l)
        f["locs"] = out


def write_comments(findings, files, outdir=None):
    """Возвращает {путь исходного PDF: путь PDF с комментариями}."""
    docs = {p: pymupdf.open(p) for p in files}
    resolve_locs(findings, docs)
    used = {p: 0 for p in files}
    for f in findings:
        col = COLORS[f["severity"]]
        req = f["norm"] + (f", п. {f['clause']}" if f.get("clause") else "")
        body = (f"№{f['n']} · {SEV_RU[f['severity']]} · {f['group']}\n{f['text']}\n\n"
                f"Требование: {req}" + (f"\nКак исправить: {f['fix']}" if f.get("fix") else ""))
        for l in f["locs"]:
            doc = docs[l["path"]]
            page = doc[l["page"]]
            x, y = l["x"], l["y"]
            rad = float(l.get("r") or 12)
            a = page.add_circle_annot(pymupdf.Rect(x - rad, y - rad, x + rad, y + rad))
            a.set_colors(stroke=col)
            a.set_border(width=1.6)
            a.set_info(title="Ordinem servare", content=body, subject=SEV_RU[f["severity"]])
            a.update()
            sx, sy = (pymupdf.Point(x, y) * page.rotation_matrix)
            box = pymupdf.Rect(sx + rad * 0.7, sy - rad * 0.7 - 17, sx + rad * 0.7 + 40, sy - rad * 0.7) \
                * page.derotation_matrix
            t = page.add_freetext_annot(box, f"{f['n']}", fontsize=11, fontname="helv",
                                        text_color=col, rotate=page.rotation)
            t.set_info(title="Ordinem servare")
            t.update()
            used[l["path"]] += 1
    out = {}
    pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        for p, doc in docs.items():
            if not used[p]:
                continue
            target = comments_path(p, outdir)
            doc.save(target, garbage=1, deflate=True)
            out[p] = target
    finally:
        pymupdf.TOOLS.mupdf_display_errors(True)
    return out


def preview_png(pdf_path, page_no, x=None, y=None, width=900, span=260):
    """PNG фрагмента листа вокруг точки (или всего листа), как его видит человек."""
    doc = pymupdf.open(pdf_path)
    page = doc[page_no]
    if x is None:
        z = width / page.rect.width
        return page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).tobytes("png")
    q = pymupdf.Point(x, y) * page.rotation_matrix
    clip = pymupdf.Rect(q.x - span * 1.5, q.y - span, q.x + span * 1.5, q.y + span) & page.rect
    z = width / max(clip.width, 1)
    return page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False).tobytes("png")
