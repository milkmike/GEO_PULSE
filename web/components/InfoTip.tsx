"use client";

/** Small "i" badge that reveals a plain-language explanation of a block on
 *  hover/focus. Positioning is left to the caller (wrap in an absolute box). */
export default function InfoTip({ text }: { text: string }) {
  return (
    <span className="group/tip relative inline-flex align-middle">
      <button
        type="button"
        aria-label="Что это за данные?"
        className="flex h-5 w-5 items-center justify-center rounded-full border border-line bg-panel2/80 text-[11px] font-semibold leading-none text-dim backdrop-blur-sm transition hover:border-ru-blue hover:text-ru-white"
      >
        i
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute right-0 top-7 z-30 w-64 rounded-lg border border-line bg-panel px-3 py-2 text-[11px] font-normal normal-case leading-snug tracking-normal text-fg opacity-0 shadow-xl transition-opacity duration-150 group-hover/tip:opacity-100 group-focus-within/tip:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}
