import Link from "next/link";

export default function SectionHeader({ title, description, action, id }: {
  title: string;
  id?: string;
  description?: string;
  action?: { href: string; label: string };
}) {
  return <div className="flex items-baseline justify-between gap-4">
    <div className="min-w-0">
      <h2 id={id} className="text-xl font-semibold">{title}</h2>
      {description && <p className="mt-1 text-sm text-dim">{description}</p>}
    </div>
    {action && <Link href={action.href} className="shrink-0 text-sm text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent">{action.label} <span aria-hidden="true">→</span></Link>}
  </div>;
}
