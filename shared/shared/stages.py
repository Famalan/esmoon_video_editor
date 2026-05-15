from enum import StrEnum


class Stage(StrEnum):
    FETCH = "fetch"
    TRANSCRIBE = "transcribe"
    SEGMENT = "segment"
    CUT = "cut"
    THUMBNAIL = "thumbnail"
    METADATA = "metadata"
    UPLOAD = "upload"
    DONE = "done"


PIPELINE_ORDER: list[Stage] = [
    Stage.FETCH,
    Stage.TRANSCRIBE,
    Stage.SEGMENT,
    Stage.CUT,
    Stage.THUMBNAIL,
    Stage.METADATA,
    Stage.UPLOAD,
]
