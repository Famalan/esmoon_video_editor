import type { Metadata } from "next";
import { NewJobForm } from "@/components/NewJobForm";
export const metadata: Metadata = { title: "Новый анализ" };
export default function NewJobPage() {
  return (
    <main className="space-y-6">
      <header className="max-w-3xl">
        <p className="eyebrow">Новый анализ</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">
          Добавьте исходное видео
        </h1>
        <p className="mt-3 text-neutral-600">
          По ссылке приложение сначала разметит расшифровку. Само видео
          скачивается только когда это понадобится для создания MP4.
        </p>
      </header>
      <NewJobForm />
    </main>
  );
}
