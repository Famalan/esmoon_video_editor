import { NewJobForm } from "@/components/NewJobForm";

export default function NewJobPage() {
  return (
    <main className="space-y-6">
      <h1 className="text-2xl font-semibold">Новая задача</h1>
      <NewJobForm />
    </main>
  );
}
