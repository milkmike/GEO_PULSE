/** Tiny markdown renderer for AI briefs (headers, bold, lists, paragraphs).
 *  Escapes HTML first — brief content comes from our own LLM pipeline, but
 *  defence-in-depth costs nothing. */
function mdToHtml(text: string): string {
  const esc = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc
    .replace(/^### (.*)$/gm, '<h4 class="mt-3 mb-1 text-[13px] font-semibold text-accent">$1</h4>')
    .replace(/^## (.*)$/gm, '<h3 class="mt-3 mb-1 text-[13px] font-semibold text-accent">$1</h3>')
    .replace(/^# (.*)$/gm, '<h3 class="mt-3 mb-1 text-sm font-semibold text-accent">$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/^[-*] (.*)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/\n{2,}/g, "<br/>");
}

export default function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div
      className={`text-[13px] leading-relaxed ${className}`}
      dangerouslySetInnerHTML={{ __html: mdToHtml(text) }}
    />
  );
}
