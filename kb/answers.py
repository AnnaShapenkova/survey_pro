import json

import db
from kb import llm
from kb.config import model
from kb.search import search

PROMPT_VERSION = "answers-v1"


def answer(conn, question, adapt=False):
    if not question.strip() or len(question) > 6000:
        raise ValueError("Question must contain 1–6000 characters")
    found = search(conn, question, limit=6, reference=False)
    reference = search(conn, question, limit=6, reference=True, platform="EnjoySurvey") if adapt else []
    if adapt and not reference:
        return "В справочнике EnjoySurvey не найдено подтверждений для адаптации. Уточните задачу; код не сгенерирован."
    context = found + reference
    if not context:
        return "В библиотеке пока нет материалов по этому вопросу."
    task = """Дай ответ на вопрос только на основании контекста. Обозначь противоречия и недостаток данных.
Не выдавай мнение за факт. Каждое существенное утверждение сопровождай [ID фрагмента]."""
    if adapt:
        task += """ Подготовь адаптацию для EnjoySurvey 4.1, используя синтаксис исключительно из reference_chunks.
Если синтаксиса или функций недостаточно, объясни пробел и оставь code пустым. Отдели подход исходной
платформы от адаптации. Сгенерированный код никогда не считается протестированным.
В citations обязательно включи использованные фрагменты справочника EnjoySurvey."""
    prompt = task + """ Схема JSON: {answer:строка, code:строка или пустая строка,
citations:[целочисленные ID использованных фрагментов], limitations:строка}.
Не добавляй ссылки, которых нет в контексте. Данные ниже:
""" + json.dumps({"question": question, "source_chunks": found, "reference_chunks": reference}, ensure_ascii=False)
    result = llm.chat(prompt)
    for key in ("answer", "code", "limitations"):
        if not isinstance(result.get(key), str):
            raise ValueError("Invalid answer schema")
    ids = result.get("citations")
    allowed = {c["id"]: c for c in context}
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in allowed for i in ids):
        raise ValueError("Answer is missing valid source citations")
    if adapt and not set(ids).intersection(c["id"] for c in reference):
        raise ValueError("Adaptation is missing manual references")
    result["tested"] = False
    conn.execute("INSERT INTO kb_generated(kind,question,answer,chunk_ids,model,prompt_version) VALUES (%s,%s,%s,%s,%s,%s)",
                 ("adaptation" if adapt else "answer", question, db.Jsonb(result), ids, model(), PROMPT_VERSION))
    text = result["answer"]
    if result["code"]:
        text += "\n\nСгенерированная адаптация, не проверена выполнением:\n\n" + result["code"]
    if result["limitations"]:
        text += "\n\nОграничения: " + result["limitations"]
    text += "\n\nИсточники:\n" + "\n".join(f"[{i}] {allowed[i]['title']} — {allowed[i]['locator']}" +
            ("\n" + allowed[i]["url"] if allowed[i]["url"].startswith("http") else " (локальный справочник)") for i in dict.fromkeys(ids))
    return text
