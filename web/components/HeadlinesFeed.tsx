import type { Headline } from "@/lib/types";
import { fmtDate, safeHttpUrl } from "@/lib/format";

function sentDot(s: number | null | undefined): string {
  if (s == null) return "bg-zinc-600";
  if (s > 0.3) return "bg-emerald-500";
  if (s < -0.3) return "bg-red-500";
  return "bg-yellow-500";
}

export default function HeadlinesFeed({ items }: { items: Headline[] }) {
  if (!items.length)
    return <div className="px-4 py-2 text-xs text-dim">Нет свежих заголовков</div>;
  return (
    <ul className="divide-y divide-white/5">
      {items.map((h, i) => {
        const url = h.url ? safeHttpUrl(h.url) : null;
        return (
        <li key={h.url ?? `${h.title}-${i}`} className="flex gap-2 px-4 py-2">
          <span className="shrink-0 text-sm leading-5">{h.flag || "🌐"}</span>
          <div className="min-w-0">
            {url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="block truncate text-[13px] leading-5 hover:text-accent"
                title={h.title}
              >
                {h.title}
              </a>
            ) : (
              <span
                className="block truncate text-[13px] leading-5"
                title={h.title}
              >
                {h.title}
              </span>
            )}
            <div className="flex items-center gap-2 text-[11px] text-dim">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${sentDot(h.sentiment)}`} />
              <span className="truncate">{h.source}</span>
              <span>·</span>
              <span>{h.country_name}</span>
              {h.published_at && (
                <>
                  <span>·</span>
                  <span>{fmtDate(h.published_at)}</span>
                </>
              )}
            </div>
          </div>
        </li>
        );
      })}
    </ul>
  );
}
