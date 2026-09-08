import { statusLabels } from "@/lib/format";

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "succeeded" ||
    status === "ready" ||
    status === "analyzed" ||
    status === "accepted"
      ? "status-success"
      : status === "failed" || status === "rejected"
        ? "status-danger"
        : status === "partial" ||
            status === "processing" ||
            status === "running"
          ? "status-warning"
          : "status-neutral";
  return (
    <span className={`status-badge ${tone}`}>
      {statusLabels[status] ?? status}
    </span>
  );
}
