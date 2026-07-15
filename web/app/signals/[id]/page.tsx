import { notFound } from "next/navigation";
import { readFeatureFlags } from "@/lib/features.server";
import SignalDetailClient from "./SignalDetailClient";

export default async function SignalDetailPage({ params }: { params: Promise<{ id: string }> }) {
  if (!readFeatureFlags().signalDetail) notFound();

  const { id: rawId } = await params;
  if (!/^\d+$/.test(rawId)) notFound();
  const signalId = Number(rawId);
  if (!Number.isSafeInteger(signalId) || signalId <= 0) notFound();

  return <SignalDetailClient signalId={signalId} />;
}
