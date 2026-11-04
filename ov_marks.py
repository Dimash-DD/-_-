#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: PDF с комментариями замечаний.

Для каждого проверенного PDF создаётся копия «<имя>_ЗАМЕЧАНИЯ.pdf»: в местах замечаний —
цветной кружок (всплывающий комментарий: текст замечания, требование нормы, как исправить)
и рамка с текстом замечания и стрелкой-выноской к кружку. Комментарии видны в любом просмотрщике PDF (Acrobat, Edge, Foxit) и
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


MAX_BOXES = 30            # не больше стольких выносок с текстом на листе (остальные — номер в кружке)
MAX_TEXT = 230            # длина текста в выноске; полный текст — во всплывающем комментарии кружка


def _font_size(page):
    """Размер шрифта выноски по формату листа: A4–A3 — 8 pt, A2 — 11 pt, A1 и больше — 14 pt."""
    long_side = max(page.rect.width, page.rect.height)
    return 8 if long_side < 1300 else 11 if long_side < 1800 else 14


def _wrap(text, width, fs):
    """Перенос строк по ширине (оценка по Helvetica) — нужен, чтобы подобрать высоту рамки."""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            t = (cur + " " + w).strip()
            # с запасом: кириллицу просмотрщик выводит шрифтом шире Helvetica
            est = max(pymupdf.get_text_length(t, fontname="helv", fontsize=fs) * 1.2, len(t) * fs * 0.56)
            if est > width and cur:
                lines.append(cur)
                cur = w
            else:
                cur = t
        lines.append(cur)
    return lines


def _place(pt, w, h, view, taken, targets, gap):
    """Место для рамки выноски рядом с точкой: не за краем листа и не поверх других рамок и кружков."""
    margin = gap * 0.5
    for k in (1, 2, 3, 4, 6):
        d = gap * k
        for dx, dy in ((d, -d - h), (-d - w, -d - h), (d, d), (-d - w, d), (d * 1.5, -h / 2), (-d * 1.5 - w, -h / 2)):
            r = pymupdf.Rect(pt.x + dx, pt.y + dy, pt.x + dx + w, pt.y + dy + h)
            if not (view.x0 + margin <= r.x0 and r.x1 <= view.x1 - margin
                    and view.y0 + margin <= r.y0 and r.y1 <= view.y1 - margin):
                continue
            if any(r.intersects(t) for t in taken) or any(r.contains(q) for q in targets):
                continue
            return r
    return None


def write_comments(findings, files, outdir=None):
    """Возвращает {путь исходного PDF: путь PDF с комментариями}.

    В каждом месте замечания — цветной кружок (всплывающий комментарий с полным текстом)
    и рамка с текстом замечания и стрелкой-выноской к кружку. Если рамок на листе слишком
    много или места нет — у кружка только номер.
    """
    docs = {p: pymupdf.open(p) for p in files}
    resolve_locs(findings, docs)
    used = {p: 0 for p in files}
    # места по листам: сначала нарушения, потом «внимание», потом «к сведению»
    order = {"error": 0, "warn": 1, "info": 2}
    per_page = {}
    for f in findings:
        for i, l in enumerate(f["locs"]):
            per_page.setdefault((l["path"], l["page"]), []).append((order[f["severity"]], i, f, l))
    for (path, pno), items in per_page.items():
        page = docs[path][pno]
        rm, drm = page.rotation_matrix, page.derotation_matrix
        view = page.rect                                         # лист, как его видит человек
        fs = _font_size(page)
        gap = fs * 4
        targets = [pymupdf.Point(l["x"], l["y"]) * rm for _, _, _, l in items]
        mm = 72 / 25.4                                           # основную надпись рамками не закрываем
        taken = [pymupdf.Rect(view.x1 - 192 * mm, view.y1 - 62 * mm, view.x1, view.y1)]
        boxed = set()
        for sev, i, f, l in sorted(items, key=lambda t: (t[0], t[1])):
            col = COLORS[f["severity"]]
            req = f["norm"] + (f", п. {f['clause']}" if f.get("clause") else "")
            body = (f"№{f['n']} · {SEV_RU[f['severity']]} · {f['group']}\n{f['text']}\n\n"
                    f"Требование: {req}" + (f"\nКак исправить: {f['fix']}" if f.get("fix") else ""))
            x, y = l["x"], l["y"]
            rad = float(l.get("r") or 12)
            a = page.add_circle_annot(pymupdf.Rect(x - rad, y - rad, x + rad, y + rad))
            a.set_colors(stroke=col)
            a.set_border(width=1.6)
            a.set_info(title="Ordinem servare", content=body, subject=SEV_RU[f["severity"]])
            a.update()
            used[path] += 1
            pt = pymupdf.Point(x, y) * rm
            box = None
            if id(f) not in boxed and len(boxed) < MAX_BOXES:       # одна рамка на замечание на листе
                text = f["text"] if len(f["text"]) <= MAX_TEXT else f["text"][:MAX_TEXT - 1].rstrip() + "…"
                head = f"№{f['n']} · {SEV_RU[f['severity']]} · {f['group']}"
                width = fs * 32
                lines = _wrap(head + "\n" + text, width - fs * 1.5, fs)
                height = len(lines) * fs * 1.3 + fs * 1.0
                box = _place(pt, width, height, view, taken, [q for q in targets if q != pt], gap)
            if box:
                boxed.add(id(f))
                taken.append(box)
                # стрелка: от края кружка к ближайшей точке рамки
                end = pymupdf.Point(min(max(pt.x, box.x0 + fs), box.x1 - fs), min(max(pt.y, box.y0), box.y1))
                if box.y0 < pt.y < box.y1:                          # рамка сбоку — к середине боковой стороны
                    end = pymupdf.Point(box.x0 if box.x0 > pt.x else box.x1, (box.y0 + box.y1) / 2)
                v = end - pt
                n = max((v.x ** 2 + v.y ** 2) ** 0.5, 1e-6)
                start = pt + v * (rad * 1.15 / n)
                knee = start + (end - start) * 0.5
                t = page.add_freetext_annot(box * drm, head + "\n" + text, fontsize=fs, fontname="helv",
                                            text_color=col, fill_color=(1, 1, 0.94), border_width=0.8,
                                            callout=[start * drm, knee * drm, end * drm],
                                            line_end=pymupdf.PDF_ANNOT_LE_CLOSED_ARROW, rotate=page.rotation)
                t.set_info(title="Ordinem servare", subject=SEV_RU[f["severity"]])   # текст рамки = содержимое
                t.update()
                taken.append(pymupdf.Rect(pt.x - rad, pt.y - rad, pt.x + rad, pt.y + rad))
            else:                                                   # места нет — номер у кружка
                nb = pymupdf.Rect(pt.x + rad * 0.7, pt.y - rad * 0.7 - 17, pt.x + rad * 0.7 + 40, pt.y - rad * 0.7)
                t = page.add_freetext_annot(nb * drm, f"{f['n']}", fontsize=11, fontname="helv",
                                            text_color=col, rotate=page.rotation)
                t.set_info(title="Ordinem servare")
                t.update()
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
