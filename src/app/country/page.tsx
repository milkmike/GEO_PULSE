"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useRouter } from "next/navigation";
import CountryCard from "@/components/CountryCard";
import { getCountries, type Country } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";

export default function CountryListPage() {
  const router = useRouter();
  const [countries, setCountries] = useState<Country[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCountries(365).then((d) => {
      setCountries(
        d.countries.sort((a, b) => b.temperature - a.temperature)
      );
      setLoading(false);
    });
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push("/")}
          className="text-muted-foreground hover:text-foreground"
        >
          ← Обзор
        </Button>
        <h1 className="text-2xl font-bold">🏳️ Все страны</h1>
      </div>
      {loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[...Array(12)].map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {countries.map((c) => (
            <CountryCard key={c.code} country={c} />
          ))}
        </div>
      )}
    </div>
  );
}
