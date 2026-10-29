#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: сводный отчёт о проверке раздела ОВ.

  А. Оформление (ГОСТ 21.602-2016, ГОСТ 21.101)
  Б. Технические замечания (СП РК 4.02-101-2012, сечения, клапаны, спецификация)
  В. Аудит Claude — отдельный независимый результат (если выполнен)

У каждого замечания — номер (как в PDF с комментариями), лист и страница PDF со ссылкой.
Отчёт — один HTML-файл, печатается на A4 (кнопка «Печать / PDF»).
"""

import datetime
import html
import os
import urllib.parse

SEV = {"error": ("Нарушение", "e"), "warn": ("Внимание", "w"), "info": ("К сведению", "i")}


def esc(s):
    return html.escape(str(s or ""))


def file_url(path, page=None):
    u = "file:///" + urllib.parse.quote(os.path.abspath(path).replace("\\", "/"), safe="/:")
    return u + (f"#page={page}" if page else "")


def pages_cell(f):
    """«стр. 9, 10» со ссылками на страницы PDF с комментариями."""
    seen, out = set(), []
    for l in f.get("locs", []):
        key = (l.get("pdf") or l.get("path"), l["page"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f'<a href="{file_url(key[0], l["page"] + 1)}">{l["page"] + 1}</a>')
    if not out and f.get("page"):
        out.append(str(f["page"]))
    return ", ".join(out[:10]) + ("…" if len(out) > 10 else "") or "—"


def rows(findings):
    out = []
    for f in findings:
        name, cls = SEV[f["severity"]]
        req = esc(f["norm"]) + (f", п. {esc(f['clause'])}" if f.get("clause") else "")
        if f["norm"].startswith("ГОСТ 21.101") or f["norm"].startswith("ГОСТ 21.110"):
            req += '<div class="sub">документ не загружен в базу</div>'
        if f.get("quote"):
            mark = "" if f.get("quote_ok", True) else '<span class="unv">не подтверждено</span>'
            req += f'<div class="q">«{esc(f["quote"])}» {mark}</div>'
        src = {"ИИ": '<span class="ai">ИИ</span>', "Claude": '<span class="ai">Claude</span>'}.get(f["source"], "авто")
        out.append(f'<tr class="{cls}"><td class="n">{f.get("n", "")}</td><td><span class="pill {cls}">{name}</span>'
                   f'<div class="grp">{esc(f["group"])}</div></td><td class="sh">{esc(f.get("sheet") or "—")}</td>'
                   f'<td class="pg">{pages_cell(f)}</td>'
                   f'<td>{esc(f["text"])}</td><td class="req">{req}</td><td>{esc(f.get("fix"))}</td>'
                   f'<td class="src">{src}</td></tr>')
    return "\n".join(out)


def table(findings, empty):
    if not findings:
        return f'<p class="empty">{empty}</p>'
    order = {"error": 0, "warn": 1, "info": 2}
    findings = sorted(findings, key=lambda f: (order[f["severity"]], f.get("n", 0)))
    return ('<table><thead><tr><th>№</th><th>Вид</th><th>Лист</th><th>Стр. PDF</th><th>Замечание</th>'
            '<th>Требование</th><th>Как исправить</th><th>Источник</th></tr></thead>'
            f'<tbody>{rows(findings)}</tbody></table>')


def counts(F):
    c = {k: sum(1 for f in F if f["severity"] == k) for k in SEV}
    return f'<span class="cnt e">{c["error"]}</span><span class="cnt w">{c["warn"]}</span><span class="cnt i">{c["info"]}</span>'


def write(res, path):
    F = res["findings"]
    p = res["project"]
    design = [f for f in F if f["kind"] == "design"]
    tech = [f for f in F if f["kind"] == "tech"]
    cnt = {k: sum(1 for f in F if f["severity"] == k) for k in SEV}

    ai = res.get("ai")
    ai_note = ""
    if ai and ai.get("error"):
        ai_note = f'<p class="note bad">Аудит Claude через API не выполнен: {esc(ai["error"])}</p>'

    cl = res.get("claude")
    if cl and cl.get("error"):
        claude_block = f'<p class="note bad">{esc(cl["error"])}</p>'
    elif cl:
        cf = cl.get("findings", [])
        for k, f in enumerate(cf, 1):
            f["n"] = f"C{k}"
        claude_block = (f'<div class="card sum"><h3>Вывод Claude <span class="sub">· {esc(cl.get("mtime"))}</span></h3>'
                        f'<p>{esc(cl.get("summary"))}</p></div>'
                        f'<h3 class="h3">Оформление {counts([f for f in cf if f["kind"] == "design"])}</h3>'
                        + table([f for f in cf if f["kind"] == "design"], "Замечаний нет.")
                        + f'<h3 class="h3">Технические {counts([f for f in cf if f["kind"] == "tech"])}</h3>'
                        + table([f for f in cf if f["kind"] == "tech"], "Замечаний нет."))
    else:
        claude_block = ('<p class="note">Аудит Claude ещё не выполнен. В приложении Claude попросите: '
                        f'«Проведи аудит ОВ по файлу {esc(res.get("claude_data"))}» — результат появится здесь '
                        'отдельным разделом после повторного открытия результатов.</p>')

    sec = res["sections"]
    sc = {k: sum(1 for r in sec if r["status"] == k) for k in ("ok", "error", "warn", "notfound")}
    links = " · ".join(f'<a href="{file_url(v["html"])}">план с отметками сечений «{esc(k)}»</a>'
                       for k, v in res.get("section_files", {}).items())
    pdfs = " · ".join(f'<a href="{file_url(v)}">{esc(os.path.basename(v))}</a>'
                      for v in res.get("comment_files", {}).values())
    sec_line = (f'<p class="note">Подписи сечений: проверено {len(sec)}, совпадает {sc["ok"]}, ошибок {sc["error"]}, '
                f'внимание {sc["warn"]}, воздуховод не найден {sc["notfound"]}. {links}</p>') if sec else ""

    norms = "".join(f'<li><b>{esc(d["code"])}</b> — {esc(d["title"])} ({d["pages"]} с., пунктов: {d["clauses"]})</li>'
                    for d in res["norms"]) or "<li>не загружены</li>"
    files = "".join(f"<li>{esc(os.path.basename(f))}</li>" for f in res["files"])
    summary = (f'<div class="card sum"><h3>Вывод Claude (API)</h3><p>{esc(ai["summary"])}</p></div>'
               if ai and ai.get("summary") else "")
    when = datetime.datetime.fromisoformat(res["started"]).strftime("%d.%m.%Y %H:%M")

    page = TEMPLATE
    for k, v in {
        "{{CODE}}": esc(p["code"] or "—"), "{{NAME}}": esc(p["name"] or "—"), "{{BUILDING}}": esc(p.get("building")),
        "{{DATE}}": when, "{{FILES}}": files, "{{NORMS}}": norms, "{{NSHEETS}}": str(len(res["sheets"])),
        "{{E}}": str(cnt["error"]), "{{W}}": str(cnt["warn"]), "{{I}}": str(cnt["info"]),
        "{{ND}}": str(len(design)), "{{NT}}": str(len(tech)), "{{PDFS}}": pdfs or "—",
        "{{SUMMARY}}": summary + ai_note, "{{CD}}": counts(design), "{{CT}}": counts(tech),
        "{{TD}}": table(design, "Замечаний по оформлению не найдено."),
        "{{TT}}": sec_line + table(tech, "Технических замечаний не найдено."),
        "{{CLAUDE}}": claude_block,
    }.items():
        page = page.replace(k, v)
    with open(path, "w", encoding="utf-8") as fo:
        fo.write(page)


TEMPLATE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Проверка ОВ {{CODE}}</title>
<style>
:root{--bg:#f2f2f7;--card:#fff;--ink:#1c1c1e;--mute:#8e8e93;--line:#e5e5ea;--blue:#007aff;
--red:#ff3b30;--orange:#ff9500;--gray:#8e8e93;--green:#34c759}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--ink:#f2f2f7;--mute:#8e8e93;--line:#2c2c2e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 -apple-system,"SF Pro Text","Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1240px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:30px;font-weight:700;letter-spacing:-.02em;margin:0 0 2px}
.lead{color:var(--mute);margin:0 0 20px}
h2{font-size:21px;font-weight:700;margin:36px 0 10px;letter-spacing:-.01em;display:flex;align-items:center;gap:8px}
h3{font-size:15px;margin:0 0 6px}.h3{margin:18px 0 8px;display:flex;align-items:center;gap:6px}
.card{background:var(--card);border-radius:14px;padding:16px 18px;margin:10px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.k{background:var(--card);border-radius:14px;padding:14px 16px}
.k b{display:block;font-size:28px;font-weight:700;letter-spacing:-.02em}
.k span{color:var(--mute);font-size:13px}
.k.e b{color:var(--red)}.k.w b{color:var(--orange)}.k.i b{color:var(--gray)}.k.b b{color:var(--blue)}
.meta{display:grid;grid-template-columns:150px 1fr;gap:6px 14px}
.meta>div:nth-child(odd){color:var(--mute)}
ul{margin:4px 0;padding-left:18px}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--card);border-radius:14px;overflow:hidden;font-size:13px}
th{text-align:left;font-weight:600;color:var(--mute);font-size:12px;padding:10px;border-bottom:1px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.n{color:var(--mute);width:38px}td.sh,td.pg{white-space:nowrap}td.src{color:var(--mute);font-size:12px}
th:nth-child(5){width:32%}th:nth-child(6){width:17%}th:nth-child(7){width:22%}
.pill{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:99px;color:#fff;white-space:nowrap}
.pill.e{background:var(--red)}.pill.w{background:var(--orange)}.pill.i{background:var(--gray)}
.cnt{font-size:12px;font-weight:600;padding:1px 8px;border-radius:99px;color:#fff}
.cnt.e{background:var(--red)}.cnt.w{background:var(--orange)}.cnt.i{background:var(--gray)}
.grp{color:var(--mute);font-size:12px;margin-top:4px}
.q{color:var(--mute);font-size:12px;margin-top:4px;font-style:italic}
.sub{color:var(--mute);font-size:11px;font-weight:400}
.unv{font-style:normal;color:var(--orange);font-size:11px;font-weight:600}
.ai{background:color-mix(in srgb,var(--blue) 14%,transparent);color:var(--blue);font-weight:600;padding:1px 7px;border-radius:99px}
.empty{color:var(--mute);background:var(--card);border-radius:14px;padding:14px 18px}
.note{background:var(--card);border-radius:14px;padding:12px 16px;color:var(--mute);margin:8px 0}
.note.bad{color:var(--red)}
.sum p{margin:0}
a{color:var(--blue);text-decoration:none}
.btn{position:fixed;right:20px;bottom:20px;background:var(--blue);color:#fff;border:0;border-radius:99px;
padding:12px 20px;font:600 14px/1 inherit;box-shadow:0 6px 20px rgba(0,0,0,.18);cursor:pointer}
.foot{color:var(--mute);font-size:12px;margin-top:30px}
.brandline{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.brandline b{display:block;font-size:15px}.brandline span{display:block;font-size:11px;letter-spacing:.14em;color:var(--mute);font-weight:600}
@media print{body{background:#fff}.btn{display:none}.card,.k,table,.empty,.note{border:1px solid #ddd}
@page{size:A4 landscape;margin:12mm}h2{break-after:avoid}tr{break-inside:avoid}}
</style></head><body><div class="wrap">
<div class="brandline"><svg viewBox="0 0 256 256" width="34" height="34"><rect width="256" height="256" rx="64" fill="#e11d48"/>
<path d="M48 128 Q128 60 208 128 Q128 196 48 128Z" fill="none" stroke="#fff" stroke-width="16" stroke-linejoin="round"/>
<circle cx="128" cy="128" r="31" fill="#fff"/><circle cx="128" cy="128" r="13" fill="#e11d48"/>
<path d="M128 26v19M48 128h19m122 0h19" stroke="#fecdd3" stroke-width="10" stroke-linecap="round"/></svg>
<div><b>Ordinem servare</b><span>ISIS-Tech</span></div></div>
<h1>Проверка раздела ОВ</h1>
<p class="lead">Автоматическая проверка по ГОСТ 21.602-2016 и СП РК 4.02-101-2012 · {{DATE}}</p>

<div class="card meta">
<div>Шифр</div><div><b>{{CODE}}</b></div>
<div>Объект</div><div>{{NAME}}</div>
<div>Здание</div><div>{{BUILDING}}</div>
<div>Листов в проверке</div><div>{{NSHEETS}}</div>
<div>Файлы</div><div><ul>{{FILES}}</ul></div>
<div>PDF с комментариями</div><div>{{PDFS}}</div>
<div>Нормативы</div><div><ul>{{NORMS}}</ul></div>
</div>

<div class="grid">
<div class="k e"><b>{{E}}</b><span>нарушений</span></div>
<div class="k w"><b>{{W}}</b><span>требуют внимания</span></div>
<div class="k i"><b>{{I}}</b><span>к сведению</span></div>
<div class="k b"><b>{{ND}} / {{NT}}</b><span>оформление / технические</span></div>
</div>
{{SUMMARY}}

<h2>А. Оформление {{CD}}</h2>
<p class="note">ГОСТ 21.602-2016, основная надпись по ГОСТ 21.101: шифр, наименование, нумерация, состав комплекта, обозначения систем, наименования листов.</p>
{{TD}}

<h2>Б. Технические замечания {{CT}}</h2>
<p class="note">СП РК 4.02-101-2012: клапаны, прокладка и толщина воздуховодов, противодымная вентиляция; сечения на чертежах, выноски и спецификация.</p>
{{TT}}

<h2>В. Аудит Claude — отдельный результат</h2>
{{CLAUDE}}

<p class="foot">Отчёт сформирован программой «Ordinem servare» (ISIS-Tech). Номера замечаний совпадают с номерами в PDF с комментариями;
ссылка в графе «Стр. PDF» открывает нужную страницу. Замечания с пометкой «проверьте по чертежу» найдены по геометрии и
требуют подтверждения инженером. Раздел «Аудит Claude» — независимая проверка, выполненная Claude.</p>
</div><button class="btn" onclick="print()">Печать / PDF</button></body></html>"""
