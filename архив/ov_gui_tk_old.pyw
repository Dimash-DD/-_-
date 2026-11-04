#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ОВ-Проверка — окно программы.

Запуск: двойной щелчок по этому файлу или по ярлыку «ОВ-Проверка» на рабочем столе.
PDF можно перетащить прямо на ярлык — они сразу попадут в список.
Сама проверка — в ov_check.py, этот файл только окно и меню.
"""

import json
import os
import queue
import sys
import threading
import traceback
import types
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SETTINGS = os.path.join(os.environ.get("APPDATA", HERE), "ОВ-Проверка", "settings.json")
SCALES = ["Авто", "1:10", "1:20", "1:25", "1:50", "1:75", "1:100", "1:125", "1:150", "1:200",
          "1:250", "1:300", "1:400", "1:500"]

HELP = """Как пользоваться

1. Добавьте один или несколько PDF (Файл → Добавить PDF или кнопка «Добавить»).
   PDF должен быть векторным, напечатанным из AutoCAD / nanoCAD / Revit, с текстом.
2. При необходимости укажите листы (например 9-15 или 3,5,8) и масштаб.
   «Авто» — масштаб подбирается для каждого листа сам.
3. Нажмите «Проверить» (F5).
4. Откройте результат: отчёт в браузере, PDF с отметками или папку.

Что проверяется сейчас:
• подпись сечения воздуховода (400х300, Ø250) против нарисованной ширины;
• стояки — по обеим сторонам прямоугольника;
• круглые сечения — по диаметру окружности.

Цвета замечаний: красный — ошибка, оранжевый — внимание,
синий — воздуховод у подписи не найден."""


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fo:
            return json.load(fo)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as fo:
            json.dump(data, fo, ensure_ascii=False, indent=1)
    except OSError:
        pass


class QueueWriter:
    """Перенаправляет print() проверки в окно журнала."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


class App:
    def __init__(self, root, files):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.last = []                      # [(pdf, html, csv, pdf с отметками)]
        self.st = load_settings()

        root.title("ОВ-Проверка")
        root.geometry(self.st.get("geometry", "900x640"))
        root.minsize(720, 520)
        self._menu()
        self._body()
        root.bind("<F5>", lambda e: self.run())
        root.bind("<Control-o>", lambda e: self.add_files())
        root.protocol("WM_DELETE_WINDOW", self.quit)

        for f in files:
            self._add(f)
        self.root.after(100, self._poll)

    # ------------------------------------------------------------ интерфейс

    def _menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Добавить PDF…", accelerator="Ctrl+O", command=self.add_files)
        f.add_command(label="Убрать выбранные", command=self.remove_selected)
        f.add_command(label="Очистить список", command=self.clear_files)
        f.add_separator()
        f.add_command(label="Выход", command=self.quit)
        m.add_cascade(label="Файл", menu=f)

        c = tk.Menu(m, tearoff=0)
        c.add_command(label="Проверить", accelerator="F5", command=self.run)
        m.add_cascade(label="Проверка", menu=c)

        r = tk.Menu(m, tearoff=0)
        r.add_command(label="Открыть отчёт", command=lambda: self.open_result(1))
        r.add_command(label="Открыть PDF с отметками", command=lambda: self.open_result(3))
        r.add_command(label="Открыть таблицу Excel", command=lambda: self.open_result(2))
        r.add_command(label="Открыть папку с результатами", command=self.open_folder)
        m.add_cascade(label="Результаты", menu=r)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Как пользоваться", command=lambda: messagebox.showinfo("Справка", HELP))
        h.add_command(label="О программе", command=self.about)
        m.add_cascade(label="Справка", menu=h)
        self.root.config(menu=m)

    def _body(self):
        pad = {"padx": 10, "pady": 6}
        top = ttk.LabelFrame(self.root, text="PDF для проверки")
        top.pack(fill="x", **pad)
        self.files = tk.Listbox(top, height=5, selectmode="extended", activestyle="none")
        self.files.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)
        btns = ttk.Frame(top)
        btns.pack(side="right", fill="y", padx=(4, 8), pady=8)
        ttk.Button(btns, text="Добавить…", command=self.add_files).pack(fill="x")
        ttk.Button(btns, text="Убрать", command=self.remove_selected).pack(fill="x", pady=4)
        ttk.Button(btns, text="Очистить", command=self.clear_files).pack(fill="x")

        opt = ttk.LabelFrame(self.root, text="Настройки")
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="Листы:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.pages = tk.StringVar(value="")
        ttk.Entry(opt, textvariable=self.pages, width=14).grid(row=0, column=1, sticky="w")
        ttk.Label(opt, text="пусто — все, или 9-15, 3,5,8", foreground="#777").grid(
            row=0, column=2, sticky="w", padx=6)

        ttk.Label(opt, text="Масштаб:").grid(row=0, column=3, sticky="w", padx=(20, 8))
        self.scale = tk.StringVar(value=self.st.get("scale", "Авто"))
        ttk.Combobox(opt, textvariable=self.scale, values=SCALES, width=8, state="readonly").grid(
            row=0, column=4, sticky="w")

        ttk.Label(opt, text="Допуск, мм:").grid(row=0, column=5, sticky="w", padx=(20, 8))
        self.tol = tk.StringVar(value=str(self.st.get("tol", 5)))
        ttk.Spinbox(opt, from_=1, to=50, textvariable=self.tol, width=5).grid(row=0, column=6, sticky="w")

        ttk.Label(opt, text="Результаты:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.outmode = tk.StringVar(value=self.st.get("outmode", "near"))
        self.outdir = tk.StringVar(value=self.st.get("outdir", ""))
        ttk.Radiobutton(opt, text="рядом с PDF", variable=self.outmode, value="near").grid(
            row=1, column=1, sticky="w")
        ttk.Radiobutton(opt, text="в папку:", variable=self.outmode, value="dir").grid(
            row=1, column=2, sticky="w", padx=6)
        ttk.Entry(opt, textvariable=self.outdir).grid(row=1, column=3, columnspan=3, sticky="we")
        ttk.Button(opt, text="…", width=3, command=self.pick_outdir).grid(row=1, column=6, sticky="w", padx=4)
        opt.columnconfigure(5, weight=1)

        act = ttk.Frame(self.root)
        act.pack(fill="x", **pad)
        self.run_btn = ttk.Button(act, text="▶  Проверить", command=self.run)
        self.run_btn.pack(side="left", ipadx=14, ipady=4)
        self.prog = ttk.Progressbar(act, mode="indeterminate", length=160)
        self.prog.pack(side="left", padx=12)
        self.res_btns = []
        for text, cmd in (("Папка", self.open_folder),
                          ("PDF с отметками", lambda: self.open_result(3)),
                          ("Отчёт", lambda: self.open_result(1))):
            b = ttk.Button(act, text=text, command=cmd, state="disabled")
            b.pack(side="right", padx=3)
            self.res_btns.append(b)

        self.summary = ttk.Label(self.root, text="Добавьте PDF и нажмите «Проверить».",
                                 font=("Segoe UI", 10, "bold"))
        self.summary.pack(fill="x", padx=12)

        logf = ttk.LabelFrame(self.root, text="Журнал")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, wrap="word", height=12, font=("Consolas", 9), state="disabled")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=8)
        self.log.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        self.log.tag_config("err", foreground="#c8372d")
        self.log.tag_config("head", font=("Consolas", 9, "bold"))

    # ------------------------------------------------------------ файлы

    def _add(self, path):
        path = os.path.abspath(path)
        if path.lower().endswith(".pdf") and path not in self.files.get(0, "end"):
            self.files.insert("end", path)

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Выберите PDF", filetypes=[("PDF", "*.pdf")],
            initialdir=self.st.get("lastdir") or os.path.expanduser("~"))
        for p in paths:
            self._add(p)
        if paths:
            self.st["lastdir"] = os.path.dirname(paths[0])

    def remove_selected(self):
        for i in reversed(self.files.curselection()):
            self.files.delete(i)

    def clear_files(self):
        self.files.delete(0, "end")

    def pick_outdir(self):
        d = filedialog.askdirectory(title="Папка для результатов", initialdir=self.outdir.get() or None)
        if d:
            self.outdir.set(d)
            self.outmode.set("dir")

    # ------------------------------------------------------------ проверка

    def run(self):
        if self.busy:
            return
        files = list(self.files.get(0, "end"))
        if not files:
            messagebox.showwarning("ОВ-Проверка", "Сначала добавьте PDF.")
            return
        try:
            tol = float(self.tol.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("ОВ-Проверка", "Допуск должен быть числом, например 5.")
            return
        sc = self.scale.get()
        scale = None if sc == "Авто" else int(sc.split(":")[1])
        outdir = self.outdir.get().strip() if self.outmode.get() == "dir" else None
        if self.outmode.get() == "dir" and not outdir:
            messagebox.showwarning("ОВ-Проверка", "Укажите папку для результатов.")
            return

        self.busy = True
        self.run_btn.config(state="disabled")
        for b in self.res_btns:
            b.config(state="disabled")
        self.prog.start(12)
        self.summary.config(text="Идёт проверка…")
        self._clear_log()
        args = types.SimpleNamespace(pages=self.pages.get().strip() or None, scale=scale, out=outdir)
        threading.Thread(target=self._worker, args=(files, tol, args), daemon=True).start()

    def _worker(self, files, tol, args):
        out = QueueWriter(self.q)
        old = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = out
        done, totals = [], {"error": 0, "warn": 0, "notfound": 0, "ok": 0}
        try:
            import ov_check
            cfg = dict(ov_check.DEFAULTS, tol=tol)
            for path in files:
                self.q.put(("head", f"\n{os.path.basename(path)}\n"))
                try:
                    res = ov_check.run_file(path, cfg, args)
                except BaseException as e:      # SystemExit из проверки тоже показываем
                    self.q.put(("err", f"Не удалось проверить: {e}\n"))
                    if not isinstance(e, SystemExit):
                        self.q.put(("err", traceback.format_exc()))
                    continue
                for r in res:
                    totals[r["status"]] += 1
                done.append((path,) + ov_check.output_paths(path, args.out))
        except BaseException:
            self.q.put(("err", traceback.format_exc()))
        finally:
            sys.stdout, sys.stderr = old
            self.q.put(("done", (done, totals)))

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "done":
                    self._finished(*data)
                else:
                    self._write(data, kind if kind in ("err", "head") else None)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finished(self, done, totals):
        self.busy = False
        self.prog.stop()
        self.run_btn.config(state="normal")
        self.last = done
        if done:
            for b in self.res_btns:
                b.config(state="normal")
            self.summary.config(
                text=f"Готово.  Ошибок: {totals['error']}   Внимание: {totals['warn']}   "
                     f"Не найдено: {totals['notfound']}   ОК: {totals['ok']}")
            if len(done) == 1:
                self.open_result(1)
        else:
            self.summary.config(text="Проверка не выполнена — см. журнал.")

    def _write(self, s, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", s, tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    # ------------------------------------------------------------ результаты

    def _choose(self):
        """Какой файл открыть, если проверялось несколько."""
        if len(self.last) <= 1:
            return self.last[0] if self.last else None
        sel = self.files.curselection()
        if sel:
            p = self.files.get(sel[0])
            for item in self.last:
                if item[0] == p:
                    return item
        return self.last[-1]

    def open_result(self, idx):
        item = self._choose()
        if not item:
            messagebox.showinfo("ОВ-Проверка", "Сначала выполните проверку.")
            return
        if os.path.exists(item[idx]):
            os.startfile(item[idx])
        else:
            messagebox.showwarning("ОВ-Проверка", f"Файл не найден:\n{item[idx]}")

    def open_folder(self):
        item = self._choose()
        if item:
            os.startfile(os.path.dirname(item[1]))

    def about(self):
        try:
            import ov_check
            ver = ov_check.VERSION
        except Exception:
            ver = "?"
        messagebox.showinfo("О программе", f"ОВ-Проверка, версия {ver} (прототип)\n\n"
                                           "Проверка чертежей ОВ в PDF.\nОтдел ОВиК.")

    def quit(self):
        self.st.update(scale=self.scale.get(), tol=self.tol.get(), outmode=self.outmode.get(),
                       outdir=self.outdir.get(), geometry=self.root.geometry())
        save_settings(self.st)
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root, sys.argv[1:])
    root.mainloop()


if __name__ == "__main__":
    main()
