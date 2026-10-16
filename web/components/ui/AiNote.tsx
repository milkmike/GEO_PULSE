import type { Citation } from "@/lib/types";
import { fmtDate, pluralRu } from "@/lib/format";

export function citationSourceCount(citations?: Citation[] | null): number | undefined {
  return citations ? new Set(citations.map((citation) => citation.source || citation.url)).size : undefined;
}

export default function AiNote({ createdAt, sources, model }: {
  createdAt: string;
  sources?: number;
  model?: string | null;
}) {
  return <span className="text-xs text-dim" title={model ?? undefined}>
    ИИ · <time dateTime={createdAt}>{fmtDate(createdAt)}</time>
    {sources != null && ` · ${sources} ${pluralRu(sources, "источник", "источника", "источников")}`}
  </span>;
}
