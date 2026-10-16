import { notFound } from "next/navigation";
import { readFeatureFlags } from "@/lib/features.server";
import PageShell from "@/components/ui/PageShell";
import SignalDetailClient from "./SignalDetailClient";

export default async function SignalDetailPage({ params }: { params: Promise<{ id: string }> }) {
  if (!readFeatureFlags().signalDetail) notFound();

  const { id: rawId } = await params;
  if (!/^\d+$/.test(rawId)) notFound();
  const signalId = Number(rawId);
  if (!Number.isSafeInteger(signalId) || signalId <= 0) notFound();

  return <PageShell active="/signals"><SignalDetailClient signalId={signalId} /></PageShell>;
}
