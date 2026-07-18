"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, CircleAlert, LoaderCircle } from "lucide-react";
import SiteHeader from "@/components/SiteHeader";
import SignalEvidence from "@/components/SignalEvidence";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import { api } from "@/lib/api";
import type { SignalDetail } from "@/lib/types";

function isAbort(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

function loadError(reason: unknown): string {
  if (reason instanceof Error && reason.message.startsWith("404 ")) {
    return "Сигнал не найден или его доказательства больше недоступны.";
  }
  return "Не удалось загрузить доказательства сигнала.";
}

export default function SignalDetailClient({ signalId }: { signalId: number }) {
  const { storiesNavigation, earlyWarningRadar } = useFeatureFlags();
  const [detail, setDetail] = useState<SignalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setDetail(null);

    api.signalDetail(signalId, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) setDetail(payload);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted && !isAbort(reason)) setError(loadError(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [reload, signalId]);

  return (
    <main className="mx-auto max-w-[1240px] px-3 pb-16">
      <SiteHeader active="/signals" />

      <div className="pt-7">
        <Link
          href="/signals"
          className="inline-flex min-h-11 items-center gap-2 text-xs uppercase tracking-wide text-dim transition-colors hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          <ArrowLeft aria-hidden="true" size={15} />
          все сигналы
        </Link>
      </div>

      {loading && (
        <div role="status" className="py-20 text-center text-sm text-dim">
          <LoaderCircle aria-hidden="true" size={24} className="mx-auto mb-3 animate-spin motion-reduce:animate-none" />
          Загружаем доказательства сигнала…
        </div>
      )}

      {!loading && error && (
        <div role="alert" className="mx-auto mt-14 max-w-xl border-y border-ru-red/40 py-10 text-center">
          <CircleAlert aria-hidden="true" size={22} className="mx-auto mb-3 text-ru-red" />
          <p className="text-sm text-dim">{error}</p>
          <button
            type="button"
            onClick={() => setReload((value) => value + 1)}
            className="mt-4 min-h-11 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
          >
            повторить
          </button>
        </div>
      )}

      {!loading && detail && (
        <div className="reveal reveal-1 mt-5">
          <SignalEvidence detail={detail} storiesEnabled={storiesNavigation} radarEnabled={earlyWarningRadar} />
        </div>
      )}
    </main>
  );
}
