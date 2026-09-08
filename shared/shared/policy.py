"""One strict interval policy at every write and render boundary."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse
import re

MIN_DURATION = 90
MAX_DURATION = 1500
MAX_UPLOAD_BYTES = 10_000_000_000
POLICY = {
    "id": "whole-episodes", "name": "Цельные эпизоды", "version": "1.0",
    "model": "gpt-6-astra", "reasoning": "medium", "prompt_version": "whole-episodes-v2",
    "min_duration_sec": MIN_DURATION, "max_duration_sec": MAX_DURATION,
    "continuous_only": True, "allow_overlap": False, "window_overlap_sec": 1500,
}


def policy_snapshot():
    return deepcopy(POLICY)


class PolicyError(ValueError):
    pass


def decimal_seconds(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise PolicyError("Таймкод должен быть конечным числом.") from None


def validate_interval(start, end, source_duration):
    start, end, duration = map(decimal_seconds, (start, end, source_duration))
    if start < 0 or end <= start:
        raise PolicyError("Некорректные границы: начало должно быть раньше конца и не меньше нуля.")
    if end > duration:
        raise PolicyError("Конец ролика выходит за длительность исходника.")
    length = end - start
    if length < MIN_DURATION or length > MAX_DURATION:
        raise PolicyError("Цельный эпизод должен длиться от 90 до 1500 секунд. Нельзя дополнять или механически обрывать тему.")
    return float(length)


def validate_actual_duration(duration):
    duration = decimal_seconds(duration)
    if not Decimal(MIN_DURATION) <= duration <= Decimal(MAX_DURATION):
        raise PolicyError(f"Фактическая длительность MP4 {duration} с вне диапазона 90–1500 с.")


def validate_selected(intervals, source_duration):
    previous_end = None
    for item in sorted(intervals, key=lambda v: decimal_seconds(v["start_sec"])):
        if any(key in item for key in ("ranges", "parts", "intervals")):
            raise PolicyError("Разрешён только один непрерывный диапазон исходника.")
        validate_interval(item["start_sec"], item["end_sec"], source_duration)
        start, end = map(decimal_seconds, (item["start_sec"], item["end_sec"]))
        if previous_end is not None and start < previous_end:
            raise PolicyError("Выбранные эпизоды пересекаются.")
        previous_end = end


def is_selected(segment):
    return segment.selection == "include" or (segment.selection == "auto" and segment.decision == "publish")


def analysis_duration(job, source):
    """A transcript bound is conservative; it is not a measured media duration."""
    value = source.duration_sec if source and source.duration_sec is not None else (job.transcript_snapshot or {}).get("duration_limit_sec")
    duration = decimal_seconds(value)
    if duration <= 0:
        raise PolicyError("Не определены временные границы расшифровки.")
    return float(duration)


def youtube_id(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https", "http"):
        raise PolicyError("Укажите ссылку YouTube.")
    host = (parsed.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        value = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        pieces = parsed.path.strip("/").split("/")
        value = parse_qs(parsed.query).get("v", [""])[0] if parsed.path == "/watch" else (pieces[1] if len(pieces) == 2 and pieces[0] in ("live", "shorts", "embed") else "")
    else:
        value = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise PolicyError("Не удалось определить video ID из ссылки YouTube.")
    return value
