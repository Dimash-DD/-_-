#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Двойной щелчок — создаёт ярлык «Ordinem servare» на рабочем столе и в меню «Пуск».
Ярлык со своим значком открывает программу без окна консоли.
"""

import os
import sys
import tkinter as tk
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ov_shortcut  # noqa: E402

root = tk.Tk()
root.withdraw()
try:
    made = ov_shortcut.create()
    messagebox.showinfo("Ordinem servare",
                        "Ярлык создан:\n\n" + "\n".join(made) +
                        "\n\nПрограмму можно открывать с рабочего стола или из меню «Пуск».")
except Exception as e:
    messagebox.showerror("Ordinem servare", f"Не удалось создать ярлык:\n{e}")
root.destroy()
