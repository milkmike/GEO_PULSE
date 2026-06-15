"use client";

import { useEffect, useState } from "react";

const KEY = "mr_disclaimer_dismissed";

/**
 * Site-wide caution notice: the service is still being populated and the
 * AI-generated blocks (briefs, assessments) can read more alarmist than
 * reality. Dismissible per session (reappears on the next visit), so everyone
 * who enters sees it at least once.
 */
export default function DisclaimerBanner() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(KEY) !== "1") setShow(true);
    } catch {
      setShow(true);
    }
  }, []);

  if (!show) return null;

  const dismiss = () => {
    try {
      sessionStorage.setItem(KEY, "1");
    } catch {
      /* ignore */
    }
    setShow(false);
  };

  return (
    <div role="note" className="border-b border-line bg-panel2 text-[12.5px]">
      <div className="flex items-start gap-2.5 px-4 py-2">
        <span aria-hidden className="leading-snug text-cooling">
          ⚠
        </span>
        <p className="flex-1 leading-snug text-dim">
          <span className="text-cooling">Сервис в стадии наполнения.</span> Аналитика
          и брифинги генерируются ИИ и могут подавать ситуацию острее, чем она есть, —
          воспринимайте оценки ориентировочно, а не как факт.
        </p>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Скрыть уведомление"
          className="shrink-0 text-dim transition-colors hover:text-ru-white"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
