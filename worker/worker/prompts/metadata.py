SYSTEM = """Ты пишешь метаданные для горизонтального YouTube-клипа (16:9) для русскоязычной аудитории.

Требования:
- Заголовок ≤60 символов, цепляет, не кликбейт.
- Описание 200–500 слов: о чём ролик, ключевые мысли, кому будет полезно. Без призывов «подписаться/лайк» — это шаблон.
- Теги: 5–10 шт, на русском, через массив.

Верни строго JSON по схеме."""


USER_TEMPLATE = """Заголовок сегмента: {segment_title}
Краткое содержание: {segment_summary}

Полный текст сегмента:
{segment_transcript}"""


JSON_SCHEMA = {
    "type": "object",
    "required": ["title", "description", "tags"],
    "properties": {
        "title": {"type": "string", "maxLength": 60},
        "description": {"type": "string", "maxLength": 2000},
        "tags": {
            "type": "array",
            "minItems": 5,
            "maxItems": 10,
            "items": {"type": "string", "maxLength": 30},
        },
    },
    "additionalProperties": False,
}
