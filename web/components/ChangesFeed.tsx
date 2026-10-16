"use client";

import Link from "next/link";
import { useId, useState } from "react";
import ListRow from "@/components/ui/ListRow";
import EmptyLine from "@/components/ui/EmptyLine";
import { safeHttpUrl } from "@/lib/urls";
import type { ChangeItem } from "@/lib/types";

const DIRECTIONS = {
  worse: { icon: "▼", label: "Хуже", color: "text-hostile" },
  better: { icon: "▲", label: "Лучше", color: "text-partner" },
  new: { icon: "◆", label: "Новый сюжет", color: "text-accent" },
  attention: { icon: "●", label: "Внимание", color: "text-dim" },
};
const dayKey = (date: Date) => date.toISOString().slice(0, 10);
const shortDate = (value: string) => new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" }).format(new Date(value));

export function dayLabel(key: string, now = new Date()): string {
  const yesterday = new Date(now);
  yesterday.setUTCDate(yesterday.getUTCDate() - 1);
  const prefix = key === dayKey(now) ? "Сегодня, " : key === dayKey(yesterday) ? "Вчера, " : "";
  return prefix + shortDate(key);
}

export function groupChanges(items: ChangeItem[]): [string, ChangeItem[]][] {
  const groups = new Map<string, ChangeItem[]>();
  for (const item of items) {
    const key = dayKey(new Date(item.occurred_at));
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups];
}

export function ChangeRow({ item, detailEnabled }: { item: ChangeItem; detailEnabled: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const direction = DIRECTIONS[item.direction];
  const href = item.kind === "signal" && !detailEnabled
    ? item.country_codes.length ? `/country/${item.country_codes[0]}` : "/countries"
    : item.href;
  const time = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(item.occurred_at));
  const articles = item.evidence_preview.slice(0, 2);
  const countries = item.country_names.slice(0, 3).join(", ")
    + (item.country_names.length > 3 ? ` и ещё ${item.country_names.length - 3}` : "");
  return <div>
    <ListRow onClick={() => setExpanded(!expanded)} expanded={expanded} controls={id} inline
      leading={<span className={direction.color} role="img" aria-label={direction.label}>{direction.icon}</span>}
      title={<><strong className="font-semibold">{item.kind === "story" ? "Новый сюжет" : countries || "Мир"}.</strong> {item.title}</>}
      meta={item.context || undefined}
      trailing={<time className="tnum text-xs text-dim" dateTime={item.occurred_at} title="Время UTC">{time}</time>} />
    {expanded && <div id={id} className="px-3 pb-3 pl-10 text-sm">
      {articles.length > 0 ? <ul className="space-y-2 border-t border-line pt-3">
        {articles.map((article) => {
          const url = safeHttpUrl(article.url);
          return <li key={article.id}>
            {url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">{article.title || "Публикация по теме"}</a> : <span>{article.title || "Публикация по теме"}</span>}
            <span className="text-dim"> · {article.source_name}{article.published_at && <> · <time dateTime={article.published_at}>{shortDate(article.published_at)}</time></>}</span>
          </li>;
        })}
      </ul> : <EmptyLine>Публикации пока недоступны.</EmptyLine>}
      <p className="mt-3 text-xs text-dim">{item.evidence_total > 0 && <>Показаны {articles.length} из {item.evidence_total} публикаций. </>}<Link href={href} className="text-accent hover:underline">Разбор →</Link></p>
    </div>}
  </div>;
}

