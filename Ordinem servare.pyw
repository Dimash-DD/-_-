#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare — запуск программы (двойной щелчок или ярлык на рабочем столе).
Открывает окно программы; сама логика — в ov_app.py, интерфейс — ui.html.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ov_app  # noqa: E402

ov_app.main()
