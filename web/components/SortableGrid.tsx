"use client";

import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import InfoTip from "@/components/InfoTip";

export type SortableItem = {
  /** Stable id used for ordering + persistence. */
  id: string;
  /** Extra classes for the grid cell, e.g. "md:col-span-2" or
   *  "col-span-12 lg:col-span-8". Size lives on the cell (not its position),
   *  so reordering preserves each panel's width. */
  cellClassName?: string;
  /** Plain-language explanation shown via an info (ⓘ) badge on the card. */
  tip?: string;
  /** The panel itself; may be a falsy/empty node — empty cells auto-hide. */
  node: ReactNode;
};

/** One draggable grid cell. Auto-hides when its node renders nothing (so
 *  conditionally-empty panels don't leave holes in the masonry). */
function Cell({
  item, dragging, over, onStart, onOver, onDrop, onEnd,
}: {
  item: SortableItem;
  dragging: boolean;
  over: boolean;
  onStart: () => void;
  onOver: (e: DragEvent<HTMLElement>) => void;
  onDrop: (e: DragEvent<HTMLElement>) => void;
  onEnd: () => void;
}) {
  const cellRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [empty, setEmpty] = useState(false);

  // The node may render asynchronously (panels self-fetch). Watch the content
  // wrapper and hide the whole cell whenever it has no element children.
  useEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const measure = () => setEmpty(el.childElementCount === 0);
    measure();
    const mo = new MutationObserver(measure);
    mo.observe(el, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, []);

  const cls = [
    "group relative",
    empty ? "hidden" : "",
    item.cellClassName ?? "",
    dragging ? "opacity-40" : "",
    over && !dragging ? "rounded-xl ring-2 ring-accent/70" : "",
  ].filter(Boolean).join(" ");

  return (
    <div
      ref={cellRef}
      className={cls}
      onDragOver={onOver}
      onDrop={onDrop}
      onDragEnd={onEnd}
    >
      {!empty && item.tip && (
        <div className="absolute right-9 top-2 z-20">
          <InfoTip text={item.tip} />
        </div>
      )}
      {!empty && (
        <button
          type="button"
          draggable
          aria-label="Перетащить плашку"
          title="Перетащить, чтобы изменить порядок"
          onDragStart={(e) => {
            onStart();
            e.dataTransfer.effectAllowed = "move";
            try { e.dataTransfer.setData("text/plain", item.id); } catch { /* ignore */ }
            if (cellRef.current) {
              try { e.dataTransfer.setDragImage(cellRef.current, 24, 16); } catch { /* ignore */ }
            }
          }}
          onDragEnd={onEnd}
          className="absolute right-2 top-2 z-20 flex h-5 w-5 cursor-grab select-none items-center justify-center rounded border border-line bg-panel2/80 text-[13px] leading-none text-dim opacity-60 backdrop-blur-sm transition hover:border-ru-blue hover:text-ru-white hover:opacity-100 active:cursor-grabbing"
        >
          ⠿
        </button>
      )}
      <div ref={innerRef}>{item.node}</div>
    </div>
  );
}

/** A drag-to-reorder grid. Order is persisted per `storageKey` in localStorage
 *  and shared across pages that use the same key (e.g. all country pages). */
export default function SortableGrid({
  storageKey, items, defaultOrder,
}: {
  storageKey: string;
  items: SortableItem[];
  defaultOrder: string[];
}) {
  const [order, setOrder] = useState<string[]>(defaultOrder);
  const dragId = useRef<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);

  // Load the saved order once; append any default ids added since (new panels).
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const saved = JSON.parse(raw);
        if (Array.isArray(saved)) {
          setOrder([...saved, ...defaultOrder.filter((id) => !saved.includes(id))]);
        }
      }
    } catch { /* ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  const persist = (next: string[]) => {
    setOrder(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* ignore */ }
  };

  const move = (target: string) => {
    const src = dragId.current;
    if (!src || src === target) return;
    const next = order.filter((id) => id !== src);
    const ti = next.indexOf(target);
    if (ti < 0) return;
    next.splice(ti, 0, src);
    persist(next);
  };

  const clear = () => { dragId.current = null; setDragging(null); setOver(null); };

  const reset = () => {
    try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
    setOrder(defaultOrder);
  };

  const byId = new Map(items.map((it) => [it.id, it]));
  const rendered: SortableItem[] = [];
  for (const id of order) { const it = byId.get(id); if (it) rendered.push(it); }
  for (const it of items) if (!order.includes(it.id)) rendered.push(it);

  return (
    <>
      <div className="col-span-full -mb-1 flex items-center justify-end gap-3 text-[11px] text-dim">
        <span className="hidden sm:inline">наведите на плашку и потяните ⠿, чтобы изменить порядок</span>
        <button onClick={reset} className="underline hover:text-ru-white">сбросить раскладку</button>
      </div>
      {rendered.map((it) => (
        <Cell
          key={it.id}
          item={it}
          dragging={dragging === it.id}
          over={over === it.id}
          onStart={() => { dragId.current = it.id; setDragging(it.id); }}
          onOver={(e) => {
            if (!dragId.current) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            if (over !== it.id) setOver(it.id);
          }}
          onDrop={(e) => { e.preventDefault(); move(it.id); clear(); }}
          onEnd={clear}
        />
      ))}
    </>
  );
}
