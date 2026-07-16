export function EmptyState({
  title,
  description,
  icon = "📭",
}: {
  title: string;
  description: string;
  icon?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-[var(--border)] bg-surface px-6 py-14 text-center">
      <div className="text-3xl" aria-hidden>
        {icon}
      </div>
      <div className="mt-3 text-base font-medium text-primary">{title}</div>
      <p className="mt-1 max-w-md text-sm text-secondary">{description}</p>
    </div>
  );
}
