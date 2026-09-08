import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Video Slicer", template: "%s · Video Slicer" },
  description: "Предсказуемая нарезка длинных видео на цельные эпизоды",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru">
      <body>
        <header className="border-b bg-white">
          <div className="app-shell flex items-center justify-between py-4">
            <Link
              href="/"
              className="timeline-rule pb-2 text-lg font-bold tracking-tight"
            >
              Video Slicer
            </Link>
            <nav aria-label="Основная навигация">
              <Link href="/jobs/new" className="button-primary">
                Новый анализ
              </Link>
            </nav>
          </div>
        </header>
        <div className="app-shell">{children}</div>
      </body>
    </html>
  );
}
