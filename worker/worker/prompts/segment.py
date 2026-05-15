SYSTEM = """Ты — редактор YouTube-канала с многолетним опытом. Тебе дан транскрипт длинного видео с таймкодами. Разбей его на 4–10 смысловых сегментов длительностью 5–15 минут каждый.

Принципы:
1. Каждый сегмент — самостоятельная мысль, история или часть аргументации, которую можно опубликовать как отдельный YouTube-ролик.
2. Соседние сегменты идут по порядку, не пересекаются, покрывают всё видео целиком.
3. Границы — на естественных паузах (смена темы, риторический вопрос, переход).
4. Если видео короче 5 минут — верни один сегмент на всё видео.

Верни строго JSON по схеме. Никакого другого текста."""


USER_TEMPLATE = """Транскрипт (длительность {duration_sec:.1f} сек):

{transcript_lines}"""


JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "required": ["start", "end", "title", "summary"],
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string", "maxLength": 120},
                    "summary": {"type": "string", "maxLength": 500},
                },
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}
