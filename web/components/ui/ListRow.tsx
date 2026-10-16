import Link from "next/link";
import type { ReactNode } from "react";

export default function ListRow({ href, onClick, expanded, controls, leading, title, meta, trailing, inline = false }: {
  href?: string;
  onClick?: () => void;
  expanded?: boolean;
  controls?: string;
  leading?: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
  trailing?: ReactNode;
  inline?: boolean;
}) {
  const className = `flex ${onClick ? "min-h-11 w-full text-left" : "min-h-9"} ${inline ? "items-start sm:items-center" : "items-center"} gap-3 px-3 py-2 text-sm hover:bg-panel2 focus-visible:outline-2 focus-visible:outline-accent`;
  const content = <>
    {leading && <span className="shrink-0">{leading}</span>}
    <span className={`min-w-0 flex-1 ${inline ? "sm:truncate" : ""}`}><span className={inline ? "line-clamp-2 break-words sm:line-clamp-none sm:inline" : "block truncate"}>{title}</span>{meta && <span className={inline ? "line-clamp-2 break-words text-dim sm:line-clamp-none sm:inline" : "block text-xs text-dim"}>{inline && <span className="hidden sm:inline"> · </span>}{meta}</span>}</span>
    {trailing && <span className="shrink-0">{trailing}</span>}
  </>;
  return onClick
    ? <button type="button" onClick={onClick} aria-expanded={expanded} aria-controls={controls} className={className}>{content}</button>
    : <Link href={href ?? "/changes"} className={className}>{content}</Link>;
}
