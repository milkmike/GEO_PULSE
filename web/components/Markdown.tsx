/** Markdown renderer for AI briefs.
 *  Escapes HTML first (defence-in-depth), then applies lightweight transforms.
 *  Citation refs like [1] become clickable superscript links when a citations
 *  map is provided. Citation urls are validated to http/https only. */
import type { Citation } from "@/lib/types";

function escAttr(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHttpUrl(u: string): string | null {
  try {
    const parsed = new URL(u);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function mdToHtml(text: string, cites?: Map<number, Citation>): string {
  const esc = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  let html = esc
    .replace(/^### (.*)$/gm, '<h4 class="mt-3 mb-1 text-[13px] font-semibold text-accent">$1</h4>')
    .replace(/^## (.*)$/gm, '<h3 class="mt-3 mb-1 text-[13px] font-semibold text-accent">$1</h3>')
    .replace(/^# (.*)$/gm, '<h3 class="mt-3 mb-1 text-sm font-semibold text-accent">$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/^[-*] (.*)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/\n{2,}/g, "<br/>");
  if (cites?.size) {
    html = html.replace(/\[(\d+)\]/g, (m, num) => {
      const c = cites.get(Number(num));
      if (!c) return m;
      const url = safeHttpUrl(c.url);
      if (!url) return m;
      const t = escAttr(`${c.title} — ${c.source}`);
      return `<sup><a href="${escAttr(url)}" target="_blank" rel="noopener noreferrer" title="${t}" class="text-accent hover:underline">${num}</a></sup>`;
    });
  }
  return html;
}

export default function Markdown({
  text,
  citations,
  className = "",
}: {
  text: string;
  citations?: Citation[] | null;
  className?: string;
}) {
  const map =
    citations?.length ? new Map(citations.map((c) => [c.n, c])) : undefined;
  return (
    <div
      className={`text-[13px] leading-relaxed ${className}`}
      dangerouslySetInnerHTML={{ __html: mdToHtml(text, map) }}
    />
  );
}
