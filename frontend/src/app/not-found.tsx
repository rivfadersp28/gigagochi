import Link from "next/link";

export default function NotFound() {
  return (
    <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6 text-center text-[var(--ink)]">
      <div className="grid max-w-sm gap-4">
        <h1 className="text-balance text-2xl font-semibold">Страница не найдена</h1>
        <p className="text-pretty text-sm text-[var(--ink-muted)]">
          Возможно, ссылка устарела или в адресе ошибка
        </p>
        <Link href="/" className="rounded-lg bg-[var(--ink)] px-4 py-3 text-[var(--paper)]">
          В приложение
        </Link>
      </div>
    </main>
  );
}
