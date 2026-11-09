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


def _render_layout(doc, layout, is_model, log):
    """Один лист → (байты PDF одной страницы, список надписей в координатах страницы)."""
    import pymupdf
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
        matrix = None

        def get_replay(self, page, *, settings=dlayout.Settings(), render_box=None):
            player = self.player()
            box = render_box or player.bbox()
            out = dlayout.Layout(box, flip_y=self._init_flip_y)
            final = out.get_final_page(page, settings)
            import copy
            s2 = copy.copy(settings)
            s2.output_coordinate_space = dpdf.get_coordinate_output_space(final)
            Backend.matrix = out.get_placement_matrix(final, settings=s2, top_origin=True)
            return super().get_replay(page, settings=settings, render_box=render_box)

    backend = Backend()
    # белый фон; цвет 7 (белый/чёрный) выводится чёрным, остальные цвета — как в чертеже
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    pipeline.RenderPipeline2d.draw_text = draw_text
    try:
        Frontend(RenderContext(doc), backend, config=cfg).draw_layout(layout, finalize=True)
    finally:
        pipeline.RenderPipeline2d.draw_text = orig

    if is_model:
        # пространство модели: размер страницы по содержимому, не больше 5 м (предел формата PDF)
        bbox = backend.player().bbox()
        if not bbox.has_data:
            return None, []
        w, h = bbox.size.x, bbox.size.y
        k = min(1.0, 5000 / max(w, h, 1e-9))
        page = dlayout.Page(0, 0, dlayout.Units.mm, margins=dlayout.Margins.all(0))
        settings = dlayout.Settings(scale=k, fit_page=False)
        box = None
    else:
        # лист бумаги целиком: формат из параметров листа, область — границы бумаги
        page = dlayout.Page.from_dxf_layout(layout)
        settings = dlayout.Settings(fit_page=False, scale=1)
        lo, hi = layout.get_paper_limits()
        box = BoundingBox2d([lo, hi]) if (hi - lo).x > 10 and (hi - lo).y > 10 else None
    try:
        pdf = backend.get_pdf_bytes(page, settings=settings, render_box=box)
    except Exception:
        settings = dlayout.Settings(fit_page=True)        # нестандартные параметры листа — вписываем
        pdf = backend.get_pdf_bytes(page, settings=settings)
    m = Backend.matrix
    out = []
    if m is not None:
        for text, p0, px, py, lay in texts:
            a, b, c = m.transform(p0), m.transform(px), m.transform(py)
            ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
            cap = math.hypot(c.x - a.x, c.y - a.y)
            if cap > 0.2:
                out.append((text, (a.x, a.y), ang, cap, lay))
    return pdf, out


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
        log("  листов (Layout) нет — беру пространство модели одной страницей")
    else:
        log(f"  листов в чертеже: {len(layouts)}")
    font = _font_file()
    result = pymupdf.open()
    for lay in layouts:
        try:
            pdf, texts = _render_layout(doc, lay, model_only, log)
        except Exception as e:
            log(f"  лист «{lay.name}» не удалось построить: {type(e).__name__}: {e}")
            continue
        if not pdf:
            continue
        one = pymupdf.open("pdf", pdf)
        page = one[0]
        if font:
            page.insert_font(fontname="ovtxt", fontfile=font)
        for text, (x, y), ang, cap, lay_name in texts:
            fs = cap / 0.72                                   # высота прописных ≈ 0,72 кегля
            try:
                page.insert_text((x, y), text, fontsize=fs, fontname="ovtxt" if font else "helv",
                                 render_mode=3,                # невидимый текстовый слой
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
