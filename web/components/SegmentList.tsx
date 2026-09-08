"use client";
import { Cue, Segment } from "@/lib/api";
import { SegmentCard } from "./SegmentCard";
export function SegmentList({
  segments,
  cues,
  sourceDuration,
  sourceUrl,
  mediaDeferred,
  onSeek,
  onSegmentChange,
}: {
  segments: Segment[];
  cues: Cue[];
  sourceDuration: number | null;
  sourceUrl: string | null;
  mediaDeferred: boolean;
  onSeek: (time: number) => void;
  onSegmentChange: (segment: Segment) => void;
}) {
  const ordered = [...segments].sort(
    (a, b) => a.index - b.index || a.start_sec - b.start_sec,
  );
  return (
    <section className="space-y-4">
      <div>
        <p className="eyebrow">Кандидаты</p>
        <h2 className="mt-1 text-xl font-semibold">Предложенные эпизоды</h2>
        <p className="mt-2 max-w-3xl text-sm text-neutral-600">
          {mediaDeferred
            ? "Границы можно уточнить по соседним репликам. После сохранения модель повторно проверит цельность этого эпизода."
            : "Границы можно уточнить только внутри исходника. После сохранения создаётся новая ревизия этого клипа, остальные файлы не меняются."}
        </p>
      </div>
      {ordered.length === 0 ? (
        <div className="panel-pad">
          <p className="font-semibold">Кандидатов пока нет</p>
          <p className="mt-1 text-sm text-neutral-600">
            Они появятся здесь автоматически после смысловой разметки.
          </p>
        </div>
      ) : (
        <div className="space-y-5">
          {ordered
            .filter((segment) => segment.selected)
            .map((segment) => (
              <SegmentCard
                key={segment.id}
                segment={segment}
                cues={cues}
                sourceDuration={sourceDuration}
                sourceUrl={sourceUrl}
                mediaDeferred={mediaDeferred}
                onSeek={onSeek}
                onChange={onSegmentChange}
              />
            ))}
          {ordered.some((segment) => !segment.selected) && (
            <details className="panel-pad">
              <summary
                id="excluded-candidates"
                className="cursor-pointer font-semibold"
              >
                Исключённые кандидаты:{" "}
                {ordered.filter((segment) => !segment.selected).length}
              </summary>
              <p className="mt-2 text-sm text-neutral-600">
                Причины отказа, просмотр контекста и возврат в выборку.
              </p>
              <div className="mt-4 space-y-5">
                {ordered
                  .filter((segment) => !segment.selected)
                  .map((segment) => (
                    <SegmentCard
                      key={segment.id}
                      segment={segment}
                      cues={cues}
                      sourceDuration={sourceDuration}
                      sourceUrl={sourceUrl}
                      mediaDeferred={mediaDeferred}
                      onSeek={onSeek}
                      onChange={onSegmentChange}
                    />
                  ))}
              </div>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
