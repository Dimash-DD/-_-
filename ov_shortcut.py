#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: ярлык программы на рабочем столе и в меню «Пуск» (Windows).

Ярлык запускает «Ordinem servare.pyw» через pythonw.exe (без чёрного окна консоли)
со значком «Ordinem servare.ico». Создаётся через стандартный объект Windows WScript.Shell —
дополнительные библиотеки не нужны. Рабочий стол определяется средствами Windows,
поэтому работает и с рабочим столом в OneDrive.

create(desktop=True, start_menu=True) -> список созданных ярлыков
"""

import base64
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Ordinem servare"
LAUNCHER = os.path.join(HERE, "Ordinem servare.pyw")
ICON = os.path.join(HERE, "Ordinem servare.ico")


def pythonw():
    """pythonw.exe рядом с текущим Python — запуск без окна консоли."""
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def _ps(script):
    """PowerShell-скрипт в кодировке UTF-16 (кириллица в путях передаётся без искажений)."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-EncodedCommand", enc], capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or out or "ошибка PowerShell")
    return out


def create(desktop=True, start_menu=True):
    if sys.platform != "win32":
        raise RuntimeError("Ярлык создаётся только в Windows.")
    q = lambda s: "'" + s.replace("'", "''") + "'"
    folders = []
    if desktop:
        folders.append("[Environment]::GetFolderPath('Desktop')")
    if start_menu:
        folders.append("[Environment]::GetFolderPath('Programs')")
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $sh=New-Object -ComObject WScript.Shell; "
        f"foreach($d in @({','.join(folders)})){{ "
        f"$p=Join-Path $d {q(NAME + '.lnk')}; $l=$sh.CreateShortcut($p); "
        f"$l.TargetPath={q(pythonw())}; $l.Arguments={q(chr(34) + LAUNCHER + chr(34))}; "
        f"$l.WorkingDirectory={q(HERE)}; $l.IconLocation={q(ICON + ',0')}; "
        f"$l.Description={q('Проверка раздела ОВ по ГОСТ 21.602 и СП РК 4.02-101')}; $l.Save(); $p }}"
    )
    return [l for l in _ps(script).splitlines() if l.strip()]


def exists():
    """Есть ли уже ярлык на рабочем столе."""
    if sys.platform != "win32":
        return False
    try:
        d = _ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath('Desktop')")
        return os.path.isfile(os.path.join(d, NAME + ".lnk"))
    except Exception:
        return False


if __name__ == "__main__":
    for p in create():
        print("Ярлык:", p)
