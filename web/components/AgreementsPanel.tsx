import type { AgreementGroup } from "@/lib/types";
import { fmtDate, safeHttpUrl } from "@/lib/format";

const TYPE_LABEL: Record<string, string> = {
  diplomatic: "дипломатия", economic: "экономика",
};

export default function AgreementsPanel({ items }: { items: AgreementGroup[] }) {
  if (!items.length) return null;
  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Договоры и намерения (180 дней)</div>
      <div className="max-h-[360px] divide-y divide-white/5 overflow-y-auto">
        {items.map((g) => (
          <div key={g.event_key} className="px-4 py-2">
            <div className="flex items-baseline gap-2 text-[13px]">
              <span className="font-medium">{g.event_key}</span>
              <span className="rounded bg-white/5 px-1.5 text-[10px] text-dim">
                {TYPE_LABEL[g.event_type] ?? g.event_type} · AL{g.action_level}
              </span>
              <span className="ml-auto shrink-0 text-[11px] text-dim">{fmtDate(g.last_at)}</span>
            </div>
            <ul className="mt-1 space-y-0.5">
              {g.articles.map((a, i) => {
                const url = a.url ? safeHttpUrl(a.url) : null;
                return (
                  <li key={i} className="truncate text-[12px]">
                    {url ? (
                      <a href={url} target="_blank" rel="noopener noreferrer"
                         className="text-dim hover:text-accent">↗ {a.title}</a>
                    ) : (
                      <span className="text-dim">{a.title}</span>
                    )}
                    <span className="text-[10px] text-zinc-600"> — {a.source}</span>
                  </li>
                );
              })}
            </ul>
            {g.articles_total > g.articles.length && (
              <div className="text-[10px] text-zinc-600">+{g.articles_total - g.articles.length} статей</div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
