#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: полный прогон проверки комплекта.

    run(files, opts, log)  ->  итог со всеми замечаниями и путями к отчётам

Шаги: нормативы → основная надпись и состав → сечения и выноски → технические проверки
по СП РК → (по желанию) аудит Claude через API → нумерация замечаний → PDF с комментариями,
сводный отчёт, данные для аудита Claude в приложении (Claude Code).

Замечание: {n, kind (design — оформление / tech — технические), group, norm, clause,
severity (error / warn / info), text, fix, sheet, source, locs: [{path, file, page, x, y, sheet}]}
"""

import datetime
import json
import os
import re

import ov_album
import ov_cad
import ov_check
import ov_config
import ov_marks
import ov_norms
import ov_report
import ov_tech

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

DEFAULT_OPTS = {
    "code": "", "name": "",
    "sections": True, "stamp": True, "callouts": True, "tech": True, "spell": True, "ai": False,
    "scale": None, "tol": 5.0, "pages": None, "outdir": None,
    "norms_dir": None, "api_key": "", "model": "claude-opus-5-5", "effort": "high",
}
SEV_ORDER = {"error": 0, "warn": 1, "info": 2}
SEC_STATUS = {"error": "error", "warn": "warn", "notfound": "info"}


def base_name(project, files):
    return re.sub(r"[\\/:*?\"<>|]+", "_", project["code"] or os.path.splitext(os.path.basename(files[0]))[0])


def report_dir(pdf, base_dir=None):
    """Папка отчётов: рядом с чертежом (или в выбранной папке результатов) — «<имя чертежа>_отчет»."""
    stem = os.path.splitext(os.path.basename(pdf))[0]
    return os.path.join(base_dir or os.path.dirname(os.path.abspath(pdf)), f"{stem}_отчет")


def claude_paths(outdir, base):
    return (os.path.join(outdir, f"{base}_ДАННЫЕ_ДЛЯ_CLAUDE.txt"),
            os.path.join(outdir, f"{base}_АУДИТ_CLAUDE.json"))


def section_findings(section_results, cfg):
    """Ошибки подписей сечений — в общий список замечаний (по одной строке на подпись)."""
    out = []
    for r in section_results:
        sev = SEC_STATUS.get(r["status"])
        if not sev:
            continue
        rad = ov_check.mark_radius(r)
        out.append({"group": "Сечения: подпись и чертёж", "norm": "ГОСТ 21.602-2016", "clause": "4.9, 6.1.9 ж)",
                    "severity": sev, "text": f"{r['msg']} (подпись «{r['text']}», привязка: {r['via']}).",
                    "fix": "Исправьте подпись сечения или ширину воздуховода на плане." if sev != "info"
                    else "Проверьте, к какому воздуховоду относится подпись.",
                    "sheet": r["sheet_label"], "page": r["page"] + 1, "file": os.path.basename(r["file"]),
                    "source": "авто", "kind": "tech",
                    "locs": [{"path": r["file"], "file": os.path.basename(r["file"]), "page": r["page"],
                              "x": r["px"], "y": r["py"], "r": rad, "sheet": r["sheet_label"]}]})
    return out


def load_claude(path):
    """Аудит, который Claude выполнил в приложении и сохранил в JSON рядом с отчётом."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as fo:
            data = json.load(fo)
    except (OSError, ValueError) as e:
        return {"error": f"Файл аудита Claude не читается: {e}", "findings": []}
    for i, f in enumerate(data.get("findings", []), 1):
        f["n"] = f"C{i}"
        f.setdefault("source", "Claude")
        f.setdefault("kind", "design" if "21.602" in f.get("norm", "") or "21.101" in f.get("norm", "") else "tech")
        f.setdefault("group", "Аудит Claude")
        f.setdefault("severity", "warn")
        f.setdefault("clause", "")
        f.setdefault("fix", "")
        f.setdefault("locs", [])
    data["path"] = path
    data["mtime"] = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
    return data


def run(files, opts=None, log=print, norms=None):
    o = dict(DEFAULT_OPTS, **(opts or {}))
    started = datetime.datetime.now()
    if not files:
        raise ValueError("Не выбраны чертежи для проверки.")
    files = [os.path.abspath(f) for f in files]
    first = files[0]                                    # папка отчёта — по первому чертежу, как он назван

    # 0. DWG и DXF → PDF (в папку отчёта), дальше проверка как у PDF
    if any(f.lower().endswith(ov_cad.CAD_EXT) for f in files):
        conv = []
        for f in files:
            if f.lower().endswith(ov_cad.CAD_EXT):
                d = report_dir(f, o["outdir"])
                os.makedirs(d, exist_ok=True)
                pdf = os.path.join(d, os.path.splitext(os.path.basename(f))[0] + ".pdf")
                log(f"Перевожу {os.path.basename(f)} в PDF…")
                conv.append(ov_cad.to_pdf(f, pdf, log))
            else:
                conv.append(f)
        files = conv

    # 1. нормативы
    if norms is None:
        norms = ov_norms.Norms(o["norms_dir"])
        norms.load(progress=log)
    log("Нормативы: " + (", ".join(d["code"] for d in norms.docs) or "не найдены"))

    # 2. листы и основная надпись
    log("Читаю листы и основные надписи…")
    album = ov_album.Album(files)
    guess = album.project_guess()
    project = {"code": o["code"].strip() or guess["code"], "name": o["name"].strip() or guess["name"],
               "building": guess["building"]}
    log(f"Комплект: {len(album.sheets)} листов, шифр «{project['code']}»")

    # 3. сечения воздуховодов, выноски, подготовка листов для технических проверок
    sprav = ov_config.get()
    cfg = dict(ov_check.DEFAULTS, tol=float(o["tol"]), duct_layers=sprav["duct_layers"],
               exclude_layers=sprav["exclude_layers"],
               skip_words=ov_check.DEFAULTS["skip_words"] + "|" + sprav["codes_rx"])
    skip_re = re.compile(cfg["skip_words"], re.I)
    section_results, infos_by_file, sheet_info, callouts, labels, plans = [], {}, {}, [], [], []
    docs = {}
    for path in files:
        doc = docs[path] = pymupdf.open(path)
        pages = ov_check.parse_pages(o["pages"], doc.page_count)
        fname = os.path.basename(path)
        infos = []
        for pno in pages:
            meta = next(s for s in album.sheets if s["path"] == path and s["page"] == pno)
            if meta["kind"] in ("spec", "general"):
                continue
            sheet = ov_check.Sheet(doc[pno], cfg)
            label = meta["label"]
            if o["callouts"]:
                callouts += ov_album.collect_callouts(sheet, fname, pno, label, path)
            res, info = ov_check.check_sheet(sheet, cfg, o["scale"])
            for r in res:
                r["file"], r["sheet_label"] = path, label
            for lab in sheet.labels:
                size = ov_check.label_sizes(lab, skip_re)
                if size:
                    labels.append((fname, pno, label, size[0], size[1], size[2], lab["cx"], lab["cy"], path))
            sheet_info[(fname, pno)] = info
            infos.append(info)
            if o["sections"]:
                section_results += res
            plans.append({"file": fname, "path": path, "page": pno, "label": label, "title": meta["stamp"]["title"],
                          "sheet": sheet, "scale": info["scale"], "pdf_page": doc[pno]})
            if info["scale"] and o["sections"]:
                c = sum(1 for r in res if r["status"] == "error")
                log(f"  {fname}, лист {label}: 1:{info['scale']}, подписей {info['nlabels']}, ошибок сечений {c}")
        infos_by_file[path] = infos

    # 4. проверки комплекта (оформление, выноски)
    findings = []
    if o["stamp"] or o["callouts"]:
        log("Проверяю шифр, наименование, состав комплекта и обозначения…")
        findings = ov_album.check_album(album, o["code"], o["name"], sheet_info,
                                        callouts if o["callouts"] else None, labels)
        if not o["stamp"]:
            findings = [f for f in findings if f["kind"] == "tech"]
        if not o["callouts"]:
            findings = [f for f in findings if f["group"] != "Выноски и спецификация"]

    # 5. технические проверки по СП РК
    spec_text = " ".join(s["text"] for s in album.sheets if s["kind"] == "spec")
    general_text = " ".join(s["text"] for s in album.sheets if s["kind"] == "general")
    if o["tech"]:
        log("Технические проверки: клапаны, прокладка, класс и толщина воздуховодов…")
        tech = ov_tech.check_tech(plans, spec_text, general_text)
        log(f"  технических замечаний: {len(tech)}")
        findings += tech
    if o["sections"]:
        findings += section_findings(section_results, cfg)
    if o.get("spell", True):
        log("Проверяю орфографию…")
        import ov_spell
        findings += ov_spell.check(album, norms, log)

    # 6. аудит Claude через API
    ai = None
    if o["ai"]:
        import ov_ai
        log("Аудит Claude по ГОСТ 21.602 и СП РК 4.02-101 (обычно 1–4 минуты)…")
        try:
            ai = ov_ai.audit(album, findings, norms, project, o["api_key"], o["model"], o["effort"], log)
            findings += ai["findings"]
        except Exception as e:      # сбой ИИ не должен отменять остальные проверки
            msg = str(e) if isinstance(e, ov_ai.AIError) else f"{type(e).__name__}: {e}"
            log(f"Аудит Claude не выполнен: {msg}")
            ai = {"error": msg, "findings": [], "summary": ""}

    # 7. нумерация: сначала оформление, потом технические; внутри — по важности и листу
    findings.sort(key=lambda f: (f["kind"] != "design", SEV_ORDER[f["severity"]], f["group"],
                                 str(f.get("sheet") or "")))
    for k, f in enumerate(findings, 1):
        f["n"] = k

    # 8. выходные файлы
    outdir = report_dir(first, o["outdir"])
    os.makedirs(outdir, exist_ok=True)
    base = base_name(project, files)
    log("Ставлю комментарии в PDF…")
    comment_files = ov_marks.write_comments(findings, files, outdir)
    for f in findings:
        for l in f["locs"]:
            l["pdf"] = comment_files.get(l["path"], l["path"])
        first = f["locs"][0] if f["locs"] else None
        if first and not f.get("page"):
            f["page"], f["file"] = first["page"] + 1, first["file"]

    section_files = {}
    order = {"error": 0, "warn": 1, "notfound": 2, "ok": 3}
    if o["sections"]:
        for path in files:
            res = [r for r in section_results if r["file"] == path]
            if not res:
                continue
            res.sort(key=lambda r: (order[r["status"]], r["page"], r["py"], r["px"]))
            for k, r in enumerate(res, 1):
                r["n"] = k
            p_html, p_csv, _ = ov_check.output_paths(path, outdir)
            ov_check.write_html(res, infos_by_file[path], docs[path],
                                {"file": os.path.basename(path), "npages": len(infos_by_file[path]),
                                 "tol": cfg["tol"]}, p_html)
            ov_check.write_csv(res, docs[path], p_csv)
            section_files[os.path.basename(path)] = {"html": p_html, "csv": p_csv,
                                                      "pdf": comment_files.get(path, "")}

    p_report = os.path.join(outdir, f"{base}_ПРОВЕРКА_ОВ.html")
    p_json = os.path.join(outdir, f"{base}_ПРОВЕРКА_ОВ.json")
    p_cdata, p_claude = claude_paths(outdir, base)
    result = {
        "project": project, "files": files, "started": started.isoformat(), "outdir": outdir,
        "norms": norms.summary(), "findings": findings,
        "sections": [{k: r[k] for k in ("n", "status", "text", "msg", "measured", "page", "via", "scale", "px", "py")}
                     | {"file": os.path.basename(r["file"]), "path": r["file"], "sheet": r["sheet_label"]}
                     for r in section_results],
        "section_files": section_files, "comment_files": {os.path.basename(k): v for k, v in comment_files.items()},
        "ai": ai, "report": p_report, "json": p_json, "claude_data": p_cdata, "claude_json": p_claude,
        "claude": load_claude(p_claude),
        "sheets": [{"file": s["file"], "page": s["page"] + 1, "sheet": s["stamp"]["sheet"],
                    "title": s["stamp"]["title"], "kind": s["kind"], "code": s["stamp"]["code"]}
                   for s in album.sheets],
    }
    write_claude_data(result, album, p_cdata)
    attach_claude_locs(result)
    finish(result)
    cnt = result["counts"]["findings"]
    log(f"Готово за {(datetime.datetime.now() - started).seconds} с. Оформление: {result['counts']['design']}, "
        f"технические: {result['counts']['tech']} (нарушений {cnt['error']}, внимание {cnt['warn']}).")
    log(f"Отчёт: {p_report}")
    return result


def finish(result):
    """Счётчики, отчёт и JSON — вызывается и после подгрузки аудита Claude."""
    F = result["findings"]
    result["counts"] = {
        "findings": {k: sum(1 for f in F if f["severity"] == k) for k in SEV_ORDER},
        "design": sum(1 for f in F if f["kind"] == "design"),
        "tech": sum(1 for f in F if f["kind"] == "tech"),
        "sections": {k: sum(1 for r in result["sections"] if r["status"] == k)
                     for k in ("error", "warn", "notfound", "ok")},
        "claude": len((result.get("claude") or {}).get("findings", [])),
    }
    ov_report.write(result, result["report"])
    with open(result["json"], "w", encoding="utf-8") as fo:
        json.dump(result, fo, ensure_ascii=False, indent=1, default=str)


def attach_claude_locs(result):
    """Места замечаний Claude: по имени файла и номеру страницы — в PDF с комментариями."""
    cl = result.get("claude")
    if not cl or cl.get("error"):
        return
    by_name = {os.path.basename(p): p for p in result["files"]}
    cfiles = {os.path.basename(k): v for k, v in result.get("comment_files", {}).items()}
    for f in cl["findings"]:
        if f["locs"] or not f.get("page"):
            continue
        name = f.get("file") or (os.path.basename(result["files"][0]) if len(result["files"]) == 1 else "")
        path = by_name.get(name)
        if not path:
            continue
        f["locs"] = [{"path": path, "file": name, "page": int(f["page"]) - 1, "x": None, "y": None,
                      "pdf": cfiles.get(name, path), "sheet": f.get("sheet")}]


def reload_claude(result):
    result["claude"] = load_claude(result["claude_json"])
    attach_claude_locs(result)
    finish(result)
    return result


CLAUDE_FORMAT = """
ФОРМАТ РЕЗУЛЬТАТА (сохранить в {json}):
{{
  "author": "Claude",
  "summary": "общий вывод по комплекту, 3–6 предложений",
  "findings": [
    {{"kind": "design | tech", "severity": "error | warn | info",
      "norm": "ГОСТ 21.602-2016 | СП РК 4.02-101-2012 | ...", "clause": "номер пункта",
      "quote": "дословная цитата из пункта", "group": "раздел замечания",
      "sheet": "номер листа", "file": "имя PDF", "page": номер страницы PDF (с 1),
      "text": "замечание", "fix": "как исправить"}}
  ]
}}
kind: design — оформление (ГОСТ 21.602, ГОСТ 21.101), tech — технические (СП РК, расчёты, оборудование).
Не повторяйте автоматические замечания из раздела AUTO ниже — дайте собственный независимый аудит.
"""


def write_claude_data(result, album, path):
    """Пакет данных для аудита в приложении Claude: листы, автоматические замечания, нормативы."""
    lines = [f"ORDINEM SERVARE (ISIS-Tech) — ДАННЫЕ ДЛЯ АУДИТА CLAUDE ({datetime.datetime.now():%d.%m.%Y %H:%M})",
             f"Шифр: {result['project']['code']}", f"Объект: {result['project']['name']}",
             "Файлы:"] + [f"  {f}" for f in result["files"]] + [
             "Нормативы (папка программы):"] + [f"  {d['code']} — {d['file']}" for d in result["norms"]] + [
             CLAUDE_FORMAT.format(json=result["claude_json"]), "=" * 80, "AUTO — замечания автоматических проверок:"]
    for f in result["findings"]:
        lines.append(f"  №{f['n']} [{f['kind']}/{f['severity']}] {f['norm']} {f['clause']} · лист {f.get('sheet') or '—'}: "
                     f"{f['text']}")
    lines += ["=" * 80, "ЛИСТЫ (текст, извлечённый из PDF):"]
    for s in album.sheets:
        st = s["stamp"]
        text = re.sub(r"\n{2,}", "\n", re.sub(r"[ \t]+", " ", s["text"]))[:15000]
        lines += [f"--- {s['file']} · стр. PDF {s['page'] + 1} · лист {st['sheet']} · {s['kind']} · {st['title']}", text]
    with open(path, "w", encoding="utf-8") as fo:
        fo.write("\n".join(lines))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Полная проверка комплекта ОВ")
    ap.add_argument("pdf", nargs="+")
    ap.add_argument("--code", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--ai", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    run(a.pdf, {"code": a.code, "name": a.name, "ai": a.ai, "outdir": a.out})
