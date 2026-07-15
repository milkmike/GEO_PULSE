import Link from "next/link";
import type { StoryListItem } from "@/lib/types";
import StoryCard from "./StoryCard";

export default function StoriesPanel({
  stories,
  title = "Главные межстрановые сюжеты",
  countryCode,
  state = "ready",
  onRetry,
  compact = true,
}: {
  stories: StoryListItem[];
  title?: string;
  countryCode?: string;
  state?: "loading" | "ready" | "error";
  onRetry?: () => void;
  compact?: boolean;
}) {
  const allHref = countryCode
    ? `/stories?country=${encodeURIComponent(countryCode)}`
    : "/stories";
  return (
    <section className="card overflow-hidden" aria-label={title}>
      <div className="flex items-baseline gap-3 border-b border-line px-4 pb-2 pt-3">
        <h2 className="card-title text-dim">{title}</h2>
        <span className="tnum text-[10px] text-dim">{stories.length || "—"}</span>
        <Link href={allHref} className="ml-auto min-h-11 py-3 text-[11px] text-accent hover:text-ru-white sm:min-h-0 sm:py-0">
          все сюжеты →
        </Link>
      </div>

      {state === "loading" && (
        <div className="space-y-2 p-3" aria-live="polite">
          <span className="sr-only">Загрузка сюжетов</span>
          {[0, 1].map((item) => (
            <div key={item} className="h-28 animate-pulse rounded-lg border border-line bg-panel2/60 motion-reduce:animate-none" />
          ))}
        </div>
      )}
      {state === "error" && (
        <div className="px-4 py-6 text-sm text-dim" role="alert">
          <p>Не удалось загрузить сюжеты.</p>
          {onRetry && (
            <button type="button" onClick={onRetry} className="mt-3 min-h-11 text-accent underline underline-offset-4">
              повторить
            </button>
          )}
        </div>
      )}
      {state === "ready" && stories.length === 0 && (
        <div className="px-4 py-7">
          <p className="display text-lg text-fg">Пока нет межстрановых сюжетов</p>
          <p className="mt-1 max-w-lg text-xs leading-relaxed text-dim">
            Панель останется на месте: здесь появятся связи этой страны с общей повесткой, когда накопится достаточно независимых источников.
          </p>
        </div>
      )}
      {state === "ready" && stories.length > 0 && (
        <div className="grid gap-2 p-3 lg:grid-cols-2">
          {stories.map((story) => (
            <StoryCard
              key={story.id}
              story={story}
              placement={countryCode ? "country" : "home"}
              countryCode={countryCode}
              compact={compact}
            />
          ))}
        </div>
      )}
    </section>
  );
}
