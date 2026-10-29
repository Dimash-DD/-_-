#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordinem servare: аудит комплекта ОВ с помощью Claude по ГОСТ 21.602-2016 и СП РК 4.02-101-2012.

Claude получает полный текст нормативов (кэшируется на стороне API, повторные запуски дешевле),
текст всех листов комплекта и замечания автоматических проверок, и возвращает замечания
в виде JSON. Каждое замечание обязано ссылаться на пункт и цитировать его — цитата
проверяется по тексту норматива, неподтверждённые помечаются.

Ключ API: из настроек программы или переменной окружения ANTHROPIC_API_KEY.
"""

import json
import os
import re

MODEL = "claude-opus-5-5"
MAX_SHEET_CHARS = 12000

INSTRUCTIONS = """Ты — эксперт-нормоконтролёр раздела ОВ (отопление, вентиляция и кондиционирование) \
для объектов в Республике Казахстан. Тебе дают полный текст нормативных документов и текст, \
извлечённый из PDF рабочих чертежей комплекта ОВ: основные надписи, подписи, выноски, таблицы, \
наименования листов. Геометрию чертежа ты не видишь — только текст.

Задача: найти несоответствия комплекта требованиям ГОСТ 21.602-2016 (правила выполнения \
рабочей документации ОВ) и СП РК 4.02-101-2012 (технические требования к системам ОВиК).

Правила:
1. Ссылайся только на пункты, которые есть в приведённых текстах нормативов. В поле quote дай \
дословную цитату из этого пункта (20–200 символов) — её проверят по тексту норматива.
2. Замечание должно опираться на конкретные данные из текста листов (номер листа, обозначение \
системы, подпись, значение). Если данных недостаточно для вывода, но вопрос важен, ставь \
severity "info" и в fix напиши, что проверить.
3. Не повторяй замечания из списка автоматических проверок — они уже есть в отчёте.
4. Не выдумывай отсутствие того, чего не видно из текста (например, оси или размеры могут быть \
графикой без текста) — такие пункты не включай.
5. Для СП РК проверяй то, что видно по тексту: обозначения и состав систем противодымной \
вентиляции, огнезадерживающие клапаны на воздуховодах через противопожарные преграды, \
расходы и параметры воздуха, указанные в общих данных, требования к воздуховодам и \
оборудованию, раздельность систем и т.п.
6. Пиши по-русски, кратко и по делу, как в замечаниях экспертизы.
7. В summary — 3–6 предложений об общем состоянии комплекта."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "norm": {"type": "string", "enum": ["ГОСТ 21.602-2016", "СП РК 4.02-101-2012"]},
                    "clause": {"type": "string"},
                    "quote": {"type": "string"},
                    "sheet": {"type": "string"},
                    "severity": {"type": "string", "enum": ["error", "warn", "info"]},
                    "group": {"type": "string"},
                    "text": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["norm", "clause", "quote", "sheet", "severity", "group", "text", "fix"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "findings"],
    "additionalProperties": False,
}


class AIError(Exception):
    pass


def build_digest(album, local_findings, project):
    parts = [f"<project code=\"{project.get('code', '')}\">{project.get('name', '')}</project>", "<sheets>"]
    for s in album.sheets:
        st = s["stamp"]
        text = re.sub(r"[ \t]+", " ", s["text"])
        text = re.sub(r"\n{2,}", "\n", text)[:MAX_SHEET_CHARS]
        parts.append(f"<sheet file=\"{s['file']}\" page=\"{s['page'] + 1}\" number=\"{st['sheet']}\" "
                     f"kind=\"{s['kind']}\" title=\"{st['title']}\">\n{text}\n</sheet>")
    parts.append("</sheets>\n<auto_findings>")
    for f in local_findings[:300]:
        parts.append(f"- [{f['norm']} {f['clause']}] лист {f.get('sheet') or '—'}: {f['text']}")
    parts.append("</auto_findings>\nПроверь комплект и верни замечания.")
    return "\n".join(parts)


def audit(album, local_findings, norms, project, api_key="", model=MODEL, effort="high", log=print):
    try:
        import anthropic
    except ImportError:
        raise AIError("Не установлена библиотека anthropic: python -m pip install anthropic")
    docs = [d for d in (norms.find("21.602"), norms.find("4.02-101")) if d]
    if not docs:
        raise AIError("В папке нормативов нет ГОСТ 21.602 и СП РК 4.02-101 — аудиту не на что опираться.")
    if not (api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise AIError("Не задан ключ API Anthropic. Укажите его в разделе «Настройки».")
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    norm_text = "\n\n".join(f"<norm code=\"{d['code']}\">\n{d['text']}\n</norm>" for d in docs)
    system = [{"type": "text", "text": INSTRUCTIONS},
              {"type": "text", "text": norm_text, "cache_control": {"type": "ephemeral"}}]
    digest = build_digest(album, local_findings, project)
    log(f"Отправляю в Claude ({model}): {len(album.sheets)} листов, нормативы: "
        + ", ".join(d["code"] for d in docs))
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            system=system,
            messages=[{"role": "user", "content": digest}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AIError("Неверный ключ API Anthropic. Проверьте его в настройках.")
    except anthropic.PermissionDeniedError:
        raise AIError("У ключа API нет доступа к модели. Проверьте настройки организации в Anthropic Console.")
    except anthropic.RateLimitError:
        raise AIError("Превышен лимит запросов Anthropic. Повторите через минуту.")
    except anthropic.BadRequestError as e:
        raise AIError(f"Запрос отклонён API: {e.message}")
    except anthropic.APIStatusError as e:
        raise AIError(f"Ошибка API Anthropic ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise AIError("Нет связи с api.anthropic.com. Проверьте интернет или прокси.")

    if msg.stop_reason == "refusal":
        raise AIError("Модель отказалась выполнять запрос.")
    if msg.stop_reason == "max_tokens":
        raise AIError("Ответ модели оборвался (слишком много замечаний). Сократите число листов.")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        raise AIError("Модель вернула ответ не в формате JSON.")

    u = msg.usage
    log(f"Ответ получен: {len(data['findings'])} замечаний. Токены: вход {u.input_tokens}, "
        f"из кэша {u.cache_read_input_tokens or 0}, запись в кэш {u.cache_creation_input_tokens or 0}, "
        f"выход {u.output_tokens}.")

    out = []
    for f in data["findings"]:
        code = "21.602" if "21.602" in f["norm"] else "4.02-101"
        ok = norms.quote_ok(code, f["quote"])
        out.append({"group": f["group"] or "Аудит ИИ", "norm": f["norm"], "clause": f["clause"],
                    "severity": f["severity"], "text": f["text"], "fix": f["fix"],
                    "sheet": f["sheet"] or None, "page": None, "file": None,
                    "quote": f["quote"], "quote_ok": ok, "source": "ИИ",
                    "kind": "design" if code == "21.602" else "tech", "locs": []})
    return {"summary": data["summary"], "findings": out,
            "usage": {"input": u.input_tokens, "cache_read": u.cache_read_input_tokens or 0,
                      "cache_write": u.cache_creation_input_tokens or 0, "output": u.output_tokens}}
