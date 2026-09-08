from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from xml.sax.saxutils import escape as xml_escape

from shared.policy import PolicyError, validate_actual_duration, validate_selected


class ExportValidationError(ValueError):
    """The frozen export snapshot cannot produce a trustworthy package."""


class StorageBackend(Protocol):
    def download_file(self, key: str, local_path: Path) -> None: ...


@dataclass(frozen=True)
class ExportArtifacts:
    zip_path: Path
    html_path: Path
    pdf_path: Path
    manifest_path: Path
    clip_count: int


_REQUIRED_CLIP_FIELDS = {
    "segment_id",
    "revision_id",
    "revision",
    "index",
    "start_sec",
    "end_sec",
    "actual_duration_sec",
    "video_key",
    "validation",
}
_FORBIDDEN_INTERVAL_FIELDS = {"ranges", "parts", "intervals"}
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _slug(value: Any, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", _text(value))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (value[:64].strip("-") or fallback)


def _timecode(seconds: Any) -> str:
    total = max(0, int(float(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ExportValidationError("Unsupported export snapshot schema.")
    source = snapshot.get("source")
    job = snapshot.get("job")
    clips = snapshot.get("clips")
    if not isinstance(source, dict) or not isinstance(job, dict):
        raise ExportValidationError("Export snapshot is missing source or job data.")
    if not isinstance(clips, list) or not clips:
        raise ExportValidationError("Export snapshot has no selected clips.")
    try:
        source_duration = source["duration_sec"]
        intervals: list[dict[str, Any]] = []
        seen_revisions: set[str] = set()
        for position, clip in enumerate(clips, start=1):
            if not isinstance(clip, dict):
                raise ExportValidationError(f"Clip {position} is not an object.")
            missing = _REQUIRED_CLIP_FIELDS.difference(clip)
            if missing:
                raise ExportValidationError(
                    f"Clip {position} is missing fields: {', '.join(sorted(missing))}."
                )
            if _FORBIDDEN_INTERVAL_FIELDS.intersection(clip):
                raise ExportValidationError(
                    f"Clip {position} contains multiple source intervals."
                )
            revision_id = _text(clip["revision_id"])
            if not revision_id or revision_id in seen_revisions:
                raise ExportValidationError(
                    f"Clip {position} has an empty or duplicate revision ID."
                )
            seen_revisions.add(revision_id)
            if not isinstance(clip["video_key"], str) or not clip["video_key"].strip():
                raise ExportValidationError(f"Clip {position} has no rendered video.")
            validation = clip.get("validation")
            technical = validation.get("technical") if isinstance(validation, dict) else None
            if not isinstance(technical, dict) or technical.get("ok") is not True:
                raise ExportValidationError(
                    f"Clip {position} has not passed technical verification."
                )
            if validation.get("narrative", {}).get("ok") is not True:
                raise ExportValidationError(f"Clip {position} has not passed narrative verification.")
            validate_actual_duration(clip["actual_duration_sec"])
            planned = float(clip["end_sec"]) - float(clip["start_sec"])
            actual = float(clip["actual_duration_sec"])
            if abs(planned - actual) > 0.5:
                raise ExportValidationError(
                    f"Clip {position} duration differs from its frozen interval."
                )
            thumbnail_keys = clip.get("thumbnail_keys", [])
            if not isinstance(thumbnail_keys, list) or not all(
                isinstance(key, str) and key for key in thumbnail_keys
            ):
                raise ExportValidationError(f"Clip {position} has invalid thumbnail keys.")
            selected_thumbnail = clip.get("selected_thumbnail_key")
            if selected_thumbnail is not None and selected_thumbnail not in thumbnail_keys:
                raise ExportValidationError(
                    f"Clip {position} selected thumbnail is not in its frozen assets."
                )
            intervals.append(
                {"start_sec": clip["start_sec"], "end_sec": clip["end_sec"]}
            )
        validate_selected(intervals, source_duration)
    except (KeyError, TypeError, ValueError, PolicyError) as exc:
        if isinstance(exc, ExportValidationError):
            raise
        raise ExportValidationError(str(exc)) from exc
    return clips


def _copy_snapshot(value: Any) -> Any:
    """JSON round-trip also guarantees manifest-safe scalar values."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise ExportValidationError("Export snapshot contains unsupported values.") from exc


def _render_html(snapshot: dict[str, Any], manifest: dict[str, Any]) -> str:
    source = snapshot["source"]
    rows: list[str] = []
    for clip in manifest["clips"]:
        media = clip["files"]["video"]["path"]
        chosen = clip["files"].get("selected_thumbnail")
        poster = f' poster="{html.escape(chosen["path"], quote=True)}"' if chosen else ""
        tags = ", ".join(_text(tag) for tag in clip.get("yt_tags") or [])
        description = clip.get("yt_description") or clip.get("summary") or ""
        rows.append(
            f"""
            <article class="clip">
              <div class="clip__media">
                <video controls preload="metadata"{poster}>
                  <source src="{html.escape(media, quote=True)}" type="video/mp4">
                </video>
              </div>
              <div class="clip__copy">
                <p class="eyebrow">Эпизод {int(clip['index']) + 1} · {html.escape(_timecode(clip['start_sec']))}–{html.escape(_timecode(clip['end_sec']))}</p>
                <h2>{html.escape(_text(clip.get('yt_title') or clip.get('title') or 'Без названия'))}</h2>
                <p>{html.escape(_text(description))}</p>
                {f'<p class="tags">{html.escape(tags)}</p>' if tags else ''}
                <a class="download" href="{html.escape(media, quote=True)}" download>Скачать MP4</a>
              </div>
            </article>"""
        )
    title = html.escape(_text(source.get("title") or "Экспорт Video Slicer"))
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — клипы</title>
  <style>
    :root {{ color-scheme: light; font-family: Arial, sans-serif; color: #171717; background: #f5f5f4; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; }}
    main {{ width: min(1080px, calc(100% - 32px)); margin: 0 auto; padding: 48px 0 72px; }}
    header {{ max-width: 760px; margin-bottom: 32px; }}
    h1 {{ font-size: clamp(32px, 5vw, 56px); line-height: 1.02; letter-spacing: -0.04em; margin: 0 0 16px; }}
    h2 {{ font-size: 26px; line-height: 1.15; margin: 6px 0 12px; }}
    p {{ line-height: 1.6; }}
    .notice {{ background: #fff7d6; border: 1px solid #ead98a; border-radius: 14px; padding: 14px 16px; }}
    .clip {{ display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr); gap: 28px; background: white; border: 1px solid #e7e5e4; border-radius: 20px; padding: 18px; margin: 18px 0; box-shadow: 0 8px 30px rgba(28,25,23,.05); }}
    video {{ display: block; width: 100%; border-radius: 12px; background: #171717; }}
    .eyebrow, .tags {{ color: #78716c; font-size: 13px; }}
    .download {{ display: inline-block; color: #fff; background: #171717; padding: 10px 14px; border-radius: 10px; text-decoration: none; font-weight: 700; }}
    footer {{ margin-top: 40px; color: #78716c; font-size: 13px; }}
    @media (max-width: 760px) {{ .clip {{ grid-template-columns: 1fr; }} main {{ padding-top: 28px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">VIDEO SLICER · {len(manifest['clips'])} КЛИПОВ</p>
      <h1>{title}</h1>
      <p class="notice"><strong>Как открыть:</strong> этот HTML — индекс пакета. Распакуйте ZIP целиком и оставьте папки <code>clips</code> и <code>thumbnails</code> рядом с файлом <code>index.html</code>.</p>
    </header>
    {''.join(rows)}
    <footer>Состав пакета зафиксирован при создании. Идентификаторы ревизий и контрольные суммы находятся в manifest.json.</footer>
  </main>
</body>
</html>
"""


def _font_paths() -> tuple[Path, Path]:
    candidates = [
        (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            return regular, bold
    raise RuntimeError("A Cyrillic-capable Arial or DejaVu Sans font is required.")


def _render_pdf(snapshot: dict[str, Any], manifest: dict[str, Any], path: Path) -> None:
    try:
        from reportlab import rl_config
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            KeepTogether,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError("reportlab is required to build report.pdf") from exc

    rl_config.invariant = 1
    regular, bold = _font_paths()
    pdfmetrics.registerFont(TTFont("VSSans", str(regular)))
    pdfmetrics.registerFont(TTFont("VSSansBold", str(bold)))
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "VSTitle", parent=styles["Title"], fontName="VSSansBold", fontSize=25,
        leading=29, textColor=colors.HexColor("#171717"), spaceAfter=9 * mm,
    )
    h2_style = ParagraphStyle(
        "VSH2", parent=styles["Heading2"], fontName="VSSansBold", fontSize=15,
        leading=19, textColor=colors.HexColor("#171717"), spaceAfter=3 * mm,
    )
    body_style = ParagraphStyle(
        "VSBody", parent=styles["BodyText"], fontName="VSSans", fontSize=10,
        leading=15, textColor=colors.HexColor("#44403c"), spaceAfter=3 * mm,
    )
    meta_style = ParagraphStyle(
        "VSMeta", parent=body_style, fontSize=8.5, leading=12,
        textColor=colors.HexColor("#78716c"), spaceAfter=2 * mm,
    )
    right_style = ParagraphStyle("VSRight", parent=meta_style, alignment=TA_RIGHT)

    source_title = _text(snapshot["source"].get("title") or "Экспорт Video Slicer")
    document = SimpleDocTemplate(
        str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=19 * mm, bottomMargin=18 * mm,
        title=source_title, author="Video Slicer",
    )

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("VSSans", 8)
        canvas.setFillColor(colors.HexColor("#78716c"))
        canvas.drawString(18 * mm, 10 * mm, "Video Slicer · зафиксированный экспорт")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Страница {doc.page}")
        canvas.restoreState()

    story: list[Any] = [
        Paragraph("VIDEO SLICER · ОТЧЁТ", meta_style),
        Paragraph(xml_escape(source_title), title_style),
        Paragraph(
            f"{len(manifest['clips'])} клипов · версия анализа "
            f"{xml_escape(_text(snapshot['job'].get('analysis_version') or 'неизвестна'))}",
            body_style,
        ),
        Spacer(1, 5 * mm),
    ]
    for ordinal, clip in enumerate(manifest["clips"], start=1):
        title = _text(clip.get("yt_title") or clip.get("title") or "Без названия")
        description = _text(clip.get("yt_description") or clip.get("summary") or "")
        revision = _text(clip["revision_id"])
        duration = float(clip["actual_duration_sec"])
        table = Table(
            [[
                Paragraph(f"Эпизод {int(clip['index']) + 1} · ревизия {int(clip['revision'])}", meta_style),
                Paragraph(
                    f"{_timecode(clip['start_sec'])}–{_timecode(clip['end_sec'])} · {duration:.3f} с",
                    right_style,
                ),
            ]],
            colWidths=[85 * mm, 73 * mm],
        )
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.7, colors.HexColor("#d6d3d1")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ]))
        heading: list[Any] = [
            table,
            Spacer(1, 3 * mm),
            Paragraph(xml_escape(title), h2_style),
        ]
        story.append(KeepTogether(heading))
        story.extend([
            Paragraph(xml_escape(description) if description else "Описание не задано.", body_style),
            Paragraph(f"ID ревизии: {xml_escape(revision)}", meta_style),
            Paragraph(
                f"Файл: {xml_escape(clip['files']['video']['path'])} · SHA256: "
                f"{xml_escape(clip['files']['video']['sha256'])}",
                meta_style,
            ),
        ])
        if ordinal != len(manifest["clips"]):
            story.append(Spacer(1, 7 * mm))
        if ordinal % 4 == 0 and ordinal != len(manifest["clips"]):
            story.append(PageBreak())
    document.build(story, onFirstPage=page, onLaterPages=page)


def _write_zip(zip_path: Path, members: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for local_path, relative_path in members:
            info = zipfile.ZipInfo(relative_path, date_time=_ZIP_TIME)
            info.compress_type = (
                zipfile.ZIP_STORED
                if local_path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png", ".pdf"}
                else zipfile.ZIP_DEFLATED
            )
            info.external_attr = 0o644 << 16
            with local_path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def build_export_artifacts(
    *,
    snapshot: dict[str, Any],
    export_id: str,
    attempt_id: str,
    storage_backend: StorageBackend,
    output_dir: Path,
) -> ExportArtifacts:
    """Build one package exclusively from its immutable creation snapshot."""
    clips = validate_snapshot(snapshot)
    snapshot = _copy_snapshot(snapshot)
    output_dir.mkdir(parents=True, exist_ok=True)
    media_dir = output_dir / "clips"
    thumbnails_dir = output_dir / "thumbnails"
    media_dir.mkdir()
    thumbnails_dir.mkdir()

    manifest_clips: list[dict[str, Any]] = []
    zip_members: list[tuple[Path, str]] = []
    for ordinal, clip in enumerate(clips, start=1):
        base = f"{ordinal:02d}-{_slug(clip.get('yt_title') or clip.get('title'), 'clip')}"
        video_relative = f"clips/{base}.mp4"
        video_path = output_dir / video_relative
        storage_backend.download_file(clip["video_key"], video_path)
        if not video_path.is_file() or video_path.stat().st_size == 0:
            raise RuntimeError(f"Rendered video for clip {ordinal} is empty.")
        video_file = {
            "path": video_relative,
            "sha256": _sha256(video_path),
            "size_bytes": video_path.stat().st_size,
        }
        zip_members.append((video_path, video_relative))

        thumbnail_files: list[dict[str, Any]] = []
        selected_file: dict[str, Any] | None = None
        for thumb_index, key in enumerate(clip.get("thumbnail_keys") or [], start=1):
            relative = f"thumbnails/{base}-{thumb_index:02d}.jpg"
            path = output_dir / relative
            storage_backend.download_file(key, path)
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError(f"Thumbnail {thumb_index} for clip {ordinal} is empty.")
            file_info = {
                "path": relative,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
            thumbnail_files.append(file_info)
            zip_members.append((path, relative))
            if key == clip.get("selected_thumbnail_key"):
                selected_file = file_info

        manifest_clip = _copy_snapshot(clip)
        manifest_clip.pop("video_key", None)
        manifest_clip.pop("thumbnail_keys", None)
        manifest_clip.pop("selected_thumbnail_key", None)
        manifest_clip["files"] = {
            "video": video_file,
            "thumbnails": thumbnail_files,
            "selected_thumbnail": selected_file,
        }
        manifest_clips.append(manifest_clip)

    manifest = {
        "schema_version": 1,
        "export_id": str(export_id),
        "attempt_id": str(attempt_id),
        "source": snapshot["source"],
        "job": snapshot["job"],
        "clips": manifest_clips,
    }
    html_path = output_dir / "index.html"
    html_path.write_text(_render_html(snapshot, manifest), encoding="utf-8")
    pdf_path = output_dir / "report.pdf"
    _render_pdf(snapshot, manifest, pdf_path)
    manifest["package_files"] = {
        "index": {
            "path": "index.html",
            "sha256": _sha256(html_path),
            "size_bytes": html_path.stat().st_size,
        },
        "report": {
            "path": "report.pdf",
            "sha256": _sha256(pdf_path),
            "size_bytes": pdf_path.stat().st_size,
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    zip_members.extend(
        [(html_path, "index.html"), (pdf_path, "report.pdf"), (manifest_path, "manifest.json")]
    )
    zip_path = output_dir / "video-slicer-export.zip"
    _write_zip(zip_path, zip_members)
    return ExportArtifacts(
        zip_path=zip_path,
        html_path=html_path,
        pdf_path=pdf_path,
        manifest_path=manifest_path,
        clip_count=len(clips),
    )
