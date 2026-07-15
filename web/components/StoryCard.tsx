import Link from "next/link";
import { ArrowUpRight, Radio, TrendingDown, TrendingUp } from "lucide-react";
import type { StoryLifecycle, StoryListItem } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

const LIFECYCLE: Record<StoryLifecycle, { label: string; tone: string }> = {
  emerging: { label: "зарождается", tone: "border-accent/50 text-accent" },
  developing: { label: "развивается", tone: "border-ally/50 text-ally" },
  escalating: { label: "обостряется", tone: "border-hostile/60 text-hostile" },
  cooling: { label: "затухает", tone: "border-cooling/50 text-cooling" },
  resolved: { label: "завершён", tone: "border-line text-dim" },
};

const ACTION_LEVEL: Record<number, string> = {
  0: "фоновый",
  1: "обычный",
  2: "заметный",
  3: "значимый",
  4: "высокий",
  5: "критический",
};

function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(["ru"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function compactDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "дата не указана";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Moscow",
  }).format(parsed);
}

function plural(value: number, forms: [string, string, string]): string {
  const mod100 = value % 100;
  const mod10 = value % 10;
  if (mod100 >= 11 && mod100 <= 14) return forms[2];
  if (mod10 === 1) return forms[0];
  if (mod10 >= 2 && mod10 <= 4) return forms[1];
  return forms[2];
}

function signed(value: number): string {
  return new Intl.NumberFormat("ru-RU", {
    signDisplay: "always",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value).replace("-", "−");
}

export default function StoryCard({
  story,
  placement = "global",
  countryCode,
  compact = false,
}: {
  story: StoryListItem;
  placement?: "global" | "country" | "home";
  countryCode?: string;
  compact?: boolean;
}) {
  const lifecycle = LIFECYCLE[story.lifecycle] ?? LIFECYCLE.developing;
  const primaryUrl = safeHttpUrl(story.primary_url);
  const countries = story.countries.map(countryName).join(" · ");
  const rri = story.latest_rri_shift;
  const ShiftIcon = rri && rri.delta_24h >= 0 ? TrendingUp : TrendingDown;

  return (
    <article
      className={`group relative overflow-hidden rounded-lg border border-line bg-panel2/55 transition-[border-color,transform] duration-200 hover:-translate-y-0.5 hover:border-ru-blue/50 motion-reduce:transition-none motion-reduce:hover:translate-y-0 ${
        compact ? "px-3 py-3" : "px-4 py-4"
      }`}
      data-story-id={story.id}
      data-placement={placement}
    >
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-0 w-px bg-gradient-to-b from-ru-white via-ru-blue to-ru-red opacity-70"
      />
      <div className="flex flex-wrap items-center gap-2 pl-1">
        <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-[0.12em] ${lifecycle.tone}`}>
          {lifecycle.label}
        </span>
        <span className="tnum text-[10px] uppercase tracking-[0.12em] text-dim">
          сюжет {String(story.id).padStart(4, "0")}
        </span>
        {countryCode && (
          <span className="ml-auto text-[10px] text-dim">
            срез: {countryName(countryCode)}
          </span>
        )}
      </div>

      <h2 className={`display mt-2 pl-1 leading-tight ${compact ? "text-[17px]" : "text-[21px]"}`}>
        <Link
          href={`/stories/${story.id}`}
          className="rounded-sm outline-none transition-colors hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          {story.title_ru}
        </Link>
      </h2>

      {!compact && story.summary && (
        <p className="mt-2 line-clamp-3 pl-1 text-[13px] leading-relaxed text-dim">
          {story.summary}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 pl-1 text-[11px] text-dim">
        <span className="text-fg">{countries || "страны не указаны"}</span>
        <span>
          {story.article_count} {plural(story.article_count, ["публикация", "публикации", "публикаций"])}
        </span>
        <span>
          {story.source_count} {plural(story.source_count, ["источник", "источника", "источников"])}
        </span>
        <span>
          {story.linked_signal_count} {plural(story.linked_signal_count, ["сигнал", "сигнала", "сигналов"])}
        </span>
        <span className={story.highest_action_level >= 4 ? "text-hostile" : "text-fg"}>
          уровень события {story.highest_action_level} · {ACTION_LEVEL[story.highest_action_level] ?? "повышенный"}
        </span>
      </div>

      {rri && (
        <div className="mt-3 rounded-md border border-line/80 bg-bg/45 px-3 py-2 text-[11px]">
          <div className="flex items-center gap-2 text-fg">
            <ShiftIcon aria-hidden="true" size={13} className={rri.delta_24h >= 0 ? "text-ally" : "text-hostile"} />
            <span className="font-semibold">
              Контекст RRI · {rri.country_name} · <span className="tnum">{signed(rri.delta_24h)}</span>
            </span>
          </div>
          <p className="mt-1 leading-snug text-dim">{rri.limitation}</p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line/70 pt-2 pl-1 text-[11px] text-dim">
        <time dateTime={story.last_seen}>обновлено {compactDate(story.last_seen)}</time>
        {story.linked_signal_count > 0 && (
          <span className="inline-flex items-center gap-1 text-cooling">
            <Radio aria-hidden="true" size={12} /> есть доказательные связи
          </span>
        )}
        <span className="ml-auto flex items-center gap-3">
          {primaryUrl ? (
            <a
              href={primaryUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-1 text-dim underline decoration-line underline-offset-4 hover:text-ru-white sm:min-h-0"
            >
              первоисточник <ArrowUpRight aria-hidden="true" size={12} />
            </a>
          ) : (
            <span>ссылка недоступна</span>
          )}
          <Link
            href={`/stories/${story.id}`}
            className="inline-flex min-h-11 items-center text-accent hover:text-ru-white sm:min-h-0"
            aria-label={`Открыть досье сюжета «${story.title_ru}»`}
          >
            досье →
          </Link>
        </span>
      </div>
    </article>
  );
}
