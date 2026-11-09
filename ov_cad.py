#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: чтение чертежей DXF и DWG.

Чертёж переводится в PDF, и дальше проверяется так же, как PDF из CAD:
  • каждый лист (вкладка пространства листа, Layout) — отдельная страница PDF размера листа,
    со всеми видовыми экранами; если листов нет — пространство модели одной страницей;
  • линии — векторные, слои чертежа сохраняются как слои PDF;
  • текст рисуется контурами (как в CAD, в том числе шрифтами SHX), а поверх кладётся
    невидимый текстовый слой — по нему программа читает основную надпись, подписи сечений,
    выноски и спецификацию (как распознанный текст в скане).

DWG сначала конвертируется в DXF бесплатной программой ODA File Converter
(https://www.opendesign.com/guestfiles/oda_file_converter) — её нужно установить один раз.

to_pdf(src, dst, log=print) -> dst
"""

import glob
import math
import os
import sys

CAD_EXT = (".dxf", ".dwg")
MM = 72 / 25.4


class CadError(Exception):
    pass


def _need_ezdxf():
    try:
        import ezdxf  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", "ezdxf"],
                       timeout=600, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import site, importlib
        if site.getusersitepackages() not in sys.path:
            sys.path.append(site.getusersitepackages())
        importlib.invalidate_caches()
        try:
            import ezdxf  # noqa: F401
        except ImportError:
            raise CadError("Для DXF/DWG нужна библиотека ezdxf: выполните «python -m pip install ezdxf».")


def find_oda():
    """ODA File Converter: стандартные места установки на Windows и Linux."""
    pats = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        pats.append(os.path.join(base, "ODA", "ODAFileConverter*", "ODAFileConverter.exe"))
    pats += ["/usr/bin/ODAFileConverter", "/opt/ODAFileConverter*/ODAFileConverter"]
    found = sorted({p for pat in pats for p in glob.glob(pat)})
    return found[-1] if found else None                 # самая новая версия


def read_doc(path, log=print):
    _need_ezdxf()
    import ezdxf
    from ezdxf import recover
    if path.lower().endswith(".dwg"):
        exe = find_oda()
        if not exe:
            raise CadError("Для DWG нужна бесплатная программа ODA File Converter "
                           "(opendesign.com → ODA File Converter). Установите её или сохраните чертёж как DXF.")
        from ezdxf.addons import odafc
        ezdxf.options.set("odafc-addon", "win_exec_path", exe)
        ezdxf.options.set("odafc-addon", "unix_exec_path", exe)
        log("  DWG → DXF (ODA File Converter)…")
        try:
            return odafc.readfile(path)
        except Exception as e:
            raise CadError(f"ODA File Converter не смог прочитать DWG: {e}")
    try:
        doc, auditor = recover.readfile(path)          # устойчиво к мелким ошибкам в файле
    except Exception as e:
        raise CadError(f"Не удалось прочитать DXF: {e}")
    return doc


def _font_file():
    for p in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\tahoma.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if os.path.isfile(p):
            return p
    return None


# форматы ГОСТ 2.301 (мм), включая дополнительные
FORMATS = [(1189, 841), (841, 594), (594, 420), (420, 297), (297, 210), (420, 891), (420, 1189),
           (297, 630), (297, 841), (297, 1051), (594, 1261), (594, 841), (841, 1783), (841, 1189)]


def _match_format(w, h):
    """Размер рамки → (формат w, h, смещение рамки внутри формата слева, снизу) или None.
    Узнаёт и внешнюю рамку (обрез/граница формата), и внутреннюю (поля 20/5/5/5 мм)."""
    for fw, fh in FORMATS:
        for W, H in ((fw, fh), (fh, fw)):
            if 0.97 * W <= w <= W * 1.005 and 0.97 * H <= h <= H * 1.005:
                return W, H, (W - w) / 2, (H - h) / 2              # внешняя: по центру формата
            if abs(w - (W - 25)) < 3 and abs(h - (H - 10)) < 3:
                return W, H, 20, 5                                # внутренняя рамка
    return None


def find_frames(layout):
    """Рамки листов, начерченные в пространстве модели (несколько листов рядом в масштабе 1:1).
    Возвращает [(xmin, ymin, W, H)] — левый нижний угол формата и его размер, в единицах чертежа."""
    from ezdxf import bbox as ebbox
    import re
    cand = []
    for e in layout.query("LWPOLYLINE POLYLINE INSERT"):
        t = e.dxftype()
        if t == "INSERT" and not re.search(r"рамк|формат|format|frame|штамп|sheet|лист|[АA][0-4]", e.dxf.name, re.I):
            continue
        if t != "INSERT" and not getattr(e, "is_closed", False):
            continue
        try:
            ext = ebbox.extents([e], fast=True)
        except Exception:
            continue
        if not ext.has_data:
            continue
        w, h = ext.size.x, ext.size.y
        f = _match_format(w, h)
        if f:
            W, H, dx, dy = f
            cand.append((ext.extmin.x - dx, ext.extmin.y - dy, W, H))
    # убираем повторы и рамки внутри других (внешняя и внутренняя рамка одного листа)
    cand.sort(key=lambda c: -c[2] * c[3])
    out = []
    for c in cand:
        if any(c[0] >= o[0] - 1 and c[1] >= o[1] - 1 and c[0] + c[2] <= o[0] + o[2] + 1
               and c[1] + c[3] <= o[1] + o[3] + 1 for o in out):
            continue
        out.append(c)
    # порядок листов: по рядам сверху вниз, в ряду слева направо
    out.sort(key=lambda c: (-round((c[1] + c[3] / 2) / 50), c[0]))
    return out


def _render_layout(doc, layout, is_model, log):
    """Вкладка чертежа → список страниц [(байты PDF, надписи в координатах страницы)].
    Пространство модели с несколькими рамками — по странице на рамку."""
    import copy
    from ezdxf.addons.drawing import Frontend, RenderContext, pymupdf as dpdf, layout as dlayout, pipeline, config
    from ezdxf.math import Vec2, Vec3, BoundingBox2d

    texts = []
    orig = pipeline.RenderPipeline2d.draw_text

    def draw_text(self, text, transform, properties, cap_height, dxftype="TEXT"):
        orig(self, text, transform, properties, cap_height, dxftype)
        if text and text.strip():
            p0 = transform.transform(Vec3(0, 0, 0))
            px = transform.transform(Vec3(1, 0, 0))
            py = transform.transform(Vec3(0, cap_height, 0))
            # текст внутри видового экрана: из модели в лист (стек видовых экранов)
            portal = getattr(self, "clipping_portal", None)
            if portal is not None and portal.is_active:
                if portal.clip_point(Vec2(p0)) is None:
                    return                                        # надпись вне видового экрана
                for stage in reversed(portal._stages):
                    if stage.transform is not None:
                        p0, px, py = (stage.transform.transform(v) for v in (p0, px, py))
            texts.append((pipeline.prepare_string_for_rendering(text, dxftype) if hasattr(
                pipeline, "prepare_string_for_rendering") else text, p0, px, py, properties.layer))

    class Backend(dpdf.PyMuPdfBackend):
        def replay(self, page, settings, render_box=None):
            """Как get_replay, но на копии записи — чтобы из одной отрисовки сделать несколько страниц."""
            player = self.player().copy()
            box = render_box or player.bbox()
            out = dlayout.Layout(box, flip_y=True)
            final = out.get_final_page(page, settings)
            s2 = copy.copy(settings)
            s2.output_coordinate_space = dpdf.get_coordinate_output_space(final)
            m = out.get_placement_matrix(final, settings=s2, top_origin=True)
            player.transform(m)
            be = self.make_backend(final, s2)
            player.replay(be)
            return be.get_pdf_bytes(), m

    backend = Backend()
    # белый фон; цвет 7 (белый/чёрный) выводится чёрным, остальные цвета — как в чертеже
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    pipeline.RenderPipeline2d.draw_text = draw_text
    try:
        Frontend(RenderContext(doc), backend, config=cfg).draw_layout(layout, finalize=True)
    finally:
        pipeline.RenderPipeline2d.draw_text = orig

    jobs = []                                                     # (страница, параметры, область)
    if is_model:
        frames = find_frames(layout)
        if frames:
            log(f"  в пространстве модели рамок листов: {len(frames)}")
            for x, y, W, H in frames:
                jobs.append((dlayout.Page(W, H, dlayout.Units.mm, margins=dlayout.Margins.all(0)),
                             dlayout.Settings(fit_page=False, scale=1),
                             BoundingBox2d([(x, y), (x + W, y + H)])))
        else:
            # рамок нет: всё содержимое одной страницей, не больше 5 м (предел формата PDF)
            bbox = backend.player().bbox()
            if not bbox.has_data:
                return []
            k = min(1.0, 5000 / max(bbox.size.x, bbox.size.y, 1e-9))
            jobs.append((dlayout.Page(0, 0, dlayout.Units.mm, margins=dlayout.Margins.all(0)),
                         dlayout.Settings(scale=k, fit_page=False), None))
    else:
        # лист бумаги целиком: формат из параметров листа, область — границы бумаги
        lo, hi = layout.get_paper_limits()
        box = BoundingBox2d([lo, hi]) if (hi - lo).x > 10 and (hi - lo).y > 10 else None
        jobs.append((dlayout.Page.from_dxf_layout(layout), dlayout.Settings(fit_page=False, scale=1), box))

    pages = []
    for page, settings, box in jobs:
        try:
            pdf, m = backend.replay(page, settings, box)
        except Exception:
            pdf, m = backend.replay(page, dlayout.Settings(fit_page=True))   # нестандартный лист — вписываем
        W, H = page.width_in_mm * MM if page.width else None, page.height_in_mm * MM if page.height else None
        out = []
        for text, p0, px, py, lay in texts:
            a, b, c = m.transform(p0), m.transform(px), m.transform(py)
            if W and H and not (-1 <= a.x <= W + 1 and -1 <= a.y <= H + 1):
                continue                                          # надпись с другого листа
            ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
            cap = math.hypot(c.x - a.x, c.y - a.y)
            if cap > 0.2:
                out.append((text, (a.x, a.y), ang, cap, lay))
        pages.append((pdf, out))
    return pages


def _register_layers(doc):
    """insert_pdf переносит слои (OCG) как объекты, но не вносит их в каталог документа —
    без этого просмотрщики и программа не видят слои. Регистрируем все найденные."""
    import re
    refs = []
    for page in doc:
        res = doc.xref_get_key(page.xref, "Resources")
        rx = int(res[1].split()[0]) if res[0] == "xref" else None
        props = doc.xref_get_key(rx, "Properties") if rx else ("null", "")
        text = doc.xref_object(rx) if rx else ""
        for x in re.findall(r"/MC\d+\s+(\d+)\s+0\s+R", text):
            if doc.xref_get_key(int(x), "Type")[1] == "/OCG" and int(x) not in refs:
                refs.append(int(x))
    if not refs:
        return
    arr = " ".join(f"{x} 0 R" for x in refs)
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "OCProperties", f"<</OCGs[{arr}]/D<</ON[{arr}]/Order[{arr}]>>>>")


def to_pdf(src, dst, log=print):
    """DXF/DWG → многостраничный PDF (по странице на лист). Возвращает dst."""
    import pymupdf
    doc = read_doc(src, log)
    layouts = []
    for name in doc.layout_names_in_taborder():
        lay = doc.layouts.get(name)
        if lay.is_modelspace:
            continue
        if len(lay) > 1 or any(e.dxftype() != "VIEWPORT" for e in lay):   # пустые вкладки пропускаем
            layouts.append(lay)
    model_only = not layouts
    if model_only:
        layouts = [doc.modelspace()]
        log("  листов (Layout) нет — беру пространство модели")
    else:
        log(f"  листов в чертеже: {len(layouts)}")
    font = _font_file()
    result = pymupdf.open()
    for lay in layouts:
        try:
            pages = _render_layout(doc, lay, model_only, log)
        except Exception as e:
            log(f"  лист «{lay.name}» не удалось построить: {type(e).__name__}: {e}")
            continue
        for pdf, texts in pages:
            one = pymupdf.open("pdf", pdf)
            page = one[0]
            if font:
                page.insert_font(fontname="ovtxt", fontfile=font)
            for text, (x, y), ang, cap, lay_name in texts:
                fs = cap / 0.72                                   # высота прописных ≈ 0,72 кегля
                try:
                    # пробел в конце: MTEXT выводится по словам, без него слова слипаются при чтении
                    page.insert_text((x, y), text.rstrip() + " ", fontsize=fs, fontname="ovtxt" if font else "helv",
                                     render_mode=3,               # невидимый текстовый слой
                                     morph=((pymupdf.Point(x, y), pymupdf.Matrix(-ang))) if abs(ang) > 0.5 else None)
                except Exception:
                    continue
            result.insert_pdf(one)
    if not result.page_count:
        raise CadError("В чертеже нет листов с изображением.")
    _register_layers(result)
    result.set_metadata({"title": os.path.basename(src), "creator": "Ordinem servare (DXF/DWG)"})
    result.save(dst, garbage=3, deflate=True)
    return dst


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(to_pdf(a, os.path.splitext(a)[0] + "_из_CAD.pdf"))
