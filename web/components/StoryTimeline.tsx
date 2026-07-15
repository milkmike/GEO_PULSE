import { FileText, Milestone } from "lucide-react";
import type { StoryArticleEvidence, StoryEventEvidence } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

type TimelineItem =
  | { kind: "event"; at: string | null; event: StoryEventEvidence }
  | { kind: "article"; at: string | null; article: StoryArticleEvidence };

function timelineDate(value: string | null): string {
  if (!value) return "время не указано";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "время не указано";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Moscow",
  }).format(parsed);
}

export default function StoryTimeline({
  events,
  articles,
}: {
  events: StoryEventEvidence[];
  articles: StoryArticleEvidence[];
}) {
  const items: TimelineItem[] = [
    ...events.map((event): TimelineItem => ({ kind: "event", at: event.event_at, event })),
    ...articles.map((article): TimelineItem => ({
      kind: "article",
      at: article.published_at,
      article,
    })),
  ].sort((left, right) => {
    const leftTime = left.at ? new Date(left.at).getTime() : Number.MAX_SAFE_INTEGER;
    const rightTime = right.at ? new Date(right.at).getTime() : Number.MAX_SAFE_INTEGER;
    return leftTime - rightTime;
  });

  if (!items.length) {
    return <p className="text-sm text-dim">Хронология пока не собрана.</p>;
  }

  return (
    <ol aria-label="Хронология сюжета" className="relative space-y-0 border-l border-line pl-5">
      {items.map((item) => {
        const isEvent = item.kind === "event";
        const article = isEvent ? null : item.article;
        const event = isEvent ? item.event : null;
        const url = article ? safeHttpUrl(article.url) : null;
        const title = event?.event_key || article?.title || "Материал без заголовка";
        return (
          <li
            key={isEvent ? `event-${event?.entity_id}-${item.at}` : `article-${article?.article_id}`}
            className="relative pb-6 pl-2 last:pb-0"
          >
            <span
              aria-hidden="true"
              className={`absolute -left-[1.67rem] top-1 flex h-5 w-5 items-center justify-center rounded-full border bg-panel ${
                isEvent ? "border-ru-red/60 text-ru-red" : "border-ru-blue/60 text-ru-blue"
              }`}
            >
              {isEvent ? <Milestone size={10} /> : <FileText size={10} />}
            </span>
            <time dateTime={item.at ?? undefined} className="tnum text-[10px] uppercase tracking-[0.12em] text-dim">
              {timelineDate(item.at)}
            </time>
            <div className="mt-1 text-[14px] font-semibold leading-snug text-fg">
              {url ? (
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-sm hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
                >
                  {title}
                </a>
              ) : title}
            </div>
            <div className="mt-1 text-[11px] text-dim">
              {event ? (
                <>событие · уровень события {event.action_level} · достоверность {Math.round(event.confidence * 100)}%</>
              ) : (
                <>{article?.source} · {article?.country_code} · связь {Math.round((article?.membership_confidence ?? 0) * 100)}%</>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
