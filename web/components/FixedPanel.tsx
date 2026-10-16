"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import InfoTip from "./InfoTip";

/** A regular grid cell that collapses when a self-loading panel has no content. */
export default function FixedPanel({ id, className = "", tip, children }: {
  id: string; className?: string; tip?: string; children: ReactNode;
}) {
  const content = useRef<HTMLDivElement>(null);
  const [empty, setEmpty] = useState(false);
  useEffect(() => {
    const element = content.current;
    if (!element) return;
    const measure = () => setEmpty(element.childElementCount === 0 && !element.textContent);
    measure();
    const observer = new MutationObserver(measure);
    observer.observe(element, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);
  return <div data-panel={id} hidden={empty} className={`relative min-w-0 ${className}`}>
    <div ref={content}>{children}</div>
    {!empty && tip && <span className="absolute right-2 top-2"><InfoTip text={tip} /></span>}
  </div>;
}
