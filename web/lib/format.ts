export function formatTime(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const total = Math.max(0, Math.floor(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: process.env.NEXT_PUBLIC_TIMEZONE || "Europe/Moscow",
  }).format(new Date(value));
}

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

export function timedSourceUrl(
  sourceUrl: string | null | undefined,
  time: number,
): string | null {
  if (!sourceUrl) return null;
  try {
    const target = new URL(sourceUrl);
    target.searchParams.set("t", `${Math.max(0, Math.floor(time))}s`);
    return target.toString();
  } catch {
    return sourceUrl;
  }
}

export const statusLabels: Record<string, string> = {
  queued: "В очереди",
  running: "В работе",
  succeeded: "Готово",
  partial: "Готово частично",
  failed: "Ошибка",
  ready: "Проверен",
  analyzed: "Разметка готова",
  processing: "Обрабатывается",
  pending: "Ожидает",
  cut: "Файл создан",
  thumbnail_ready: "Превью готовы",
  metadata_ready: "Метаданные готовы",
  uploaded: "Загружен",
  auto: "По решению ИИ",
  include: "Включён",
  exclude: "Исключён",
  unreviewed: "Не проверен",
  accepted: "Подтверждён",
  rejected: "Отклонён",
};
