import Link from "next/link";
import type { ReactNode } from "react";

export default function ListRow({ href, leading, title, meta, trailing }: {
  href: string;
  leading?: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
  trailing?: ReactNode;
}) {
  return <Link href={href} className="flex min-h-9 items-center gap-3 px-3 py-2 text-sm hover:bg-panel2 focus-visible:outline-2 focus-visible:outline-accent">
    {leading && <span className="shrink-0">{leading}</span>}
    <span className="min-w-0 flex-1"><span className="block truncate">{title}</span>{meta && <span className="block text-xs text-dim">{meta}</span>}</span>
    {trailing && <span className="shrink-0">{trailing}</span>}
  </Link>;
}
