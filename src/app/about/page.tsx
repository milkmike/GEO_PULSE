"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { API_URL } from "@/lib/api";

/* ── Types ────────────────────────────────────────────── */
interface LiveStats {
  total_articles: number;
  total_analyzed: number;
  active_sources: number;
  total_relevant: number;
  newest_article: string;
}

interface SourceInfo {
  tier: string;
}

interface TierCount {
  tier: string;
  count: number;
  desc: string;
  icon: string;
}

/* ── Static Data ──────────────────────────────────────── */

const TIER_META: Record<string, { desc: string; icon: string; order: number }> = {
  official:           { desc: "Госагентства, МИД, Минобороны, центробанки", icon: "🏛️", order: 1 },
  mainstream:         { desc: "Крупные национальные СМИ, ТВ-порталы", icon: "📰", order: 2 },
  independent:        { desc: "Независимые медиа, расследования", icon: "🔍", order: 3 },
  social:             { desc: "Telegram-каналы, соцмедиа", icon: "💬", order: 4 },
  domestic_opposition:{ desc: "Оппозиционные издания", icon: "⚡", order: 5 },
  western_proxy:      { desc: "Зарубежные СМИ о регионе", icon: "🌐", order: 6 },
  analytics:          { desc: "Аналитические центры, эксперты", icon: "📊", order: 7 },
};

const TIER_LABELS: Record<string, string> = {
  official: "Official",
  mainstream: "Mainstream",
  independent: "Independent",
  social: "Social",
  domestic_opposition: "Opposition",
  western_proxy: "Western Proxy",
  analytics: "Analytics",
};

const SCALE = [
  { range: "+30° … +60°", label: "Сотрудничество", example: "Беларусь", emoji: "🔥", color: "#ef4444", bg: "rgba(239,68,68,0.08)" },
  { range: "+10° … +30°", label: "Партнёрство", example: "Таджикистан", emoji: "🟠", color: "#f97316", bg: "rgba(249,115,22,0.08)" },
  { range: "-10° … +10°", label: "Нейтралитет", example: "Узбекистан", emoji: "⚪", color: "#a1a1aa", bg: "rgba(161,161,170,0.08)" },
  { range: "-30° … -10°", label: "Охлаждение", example: "Казахстан", emoji: "🔵", color: "#3b82f6", bg: "rgba(59,130,246,0.08)" },
  { range: "-60° … -30°", label: "Напряжение", example: "Молдова", emoji: "❄️", color: "#6366f1", bg: "rgba(99,102,241,0.08)" },
];

const COUNTRIES = [
  { flag: "🇰🇿", name: "Казахстан", code: "KZ" },
  { flag: "🇺🇿", name: "Узбекистан", code: "UZ" },
  { flag: "🇰🇬", name: "Кыргызстан", code: "KG" },
  { flag: "🇹🇯", name: "Таджикистан", code: "TJ" },
  { flag: "🇹🇲", name: "Туркменистан", code: "TM" },
  { flag: "🇦🇿", name: "Азербайджан", code: "AZ" },
  { flag: "🇦🇲", name: "Армения", code: "AM" },
  { flag: "🇬🇪", name: "Грузия", code: "GE" },
  { flag: "🇲🇩", name: "Молдова", code: "MD" },
  { flag: "🇧🇾", name: "Беларусь", code: "BY" },
];

const ROADMAP = [
  {
    phase: "Работает сейчас",
    status: "active" as const,
    items: [
      { title: "191 источник в 10 странах по 7 тирам", done: true },
      { title: "AI-анализ каждой статьи (sentiment, action level, entities)", done: true },
      { title: "Температурный индекс с экспоненциальным затуханием (τ=14d)", done: true },
      { title: "Сюжетные нити с embedding-кластеризацией (Jina AI)", done: true },
      { title: "Нарративный расклад — спектр, динамика, сравнение, heatmap", done: true },
      { title: "Аудиторный сплит — как двуязычные СМИ пишут для разных аудиторий", done: true },
      { title: "Геополитический пульс — горячие точки и лента событий", done: true },
      { title: "Голосования ООН и торговые данные для верификации", done: true },
      { title: "Система алертов — аномалии температуры и маркеры на графиках", done: true },
      { title: "AI-дайджесты по каждой стране (ежедневно)", done: true },
    ],
  },
  {
    phase: "Ближайшие планы",
    status: "next" as const,
    items: [
      { title: "🔔 Telegram-уведомления — мгновенные алерты при аномалиях", done: false },
      { title: "🧬 Narrative DNA — отслеживание происхождения и мутации нарративов", done: false },
      { title: "📈 Предиктивная аналитика — прогноз трендов на 7–14 дней", done: false },
    ],
  },
  {
    phase: "Перспектива",
    status: "future" as const,
    items: [
      { title: "🦠 Contagion Map — визуализация распространения нарративов между странами", done: false },
      { title: "⚖️ Source Reliability Score — автоматический рейтинг достоверности", done: false },
      { title: "🤖 Public API — webhook-подписки для внешних потребителей", done: false },
    ],
  },
];

const USE_CASES = [
  {
    title: "Геополитическая аналитика",
    desc: "Отслеживание динамики отношений между Россией и странами СНГ в реальном времени. Раннее обнаружение изменений тональности.",
    icon: "🌐",
    audience: "Аналитики, исследователи",
  },
  {
    title: "Медийный мониторинг",
    desc: "Какие сюжеты доминируют в медиаполе каждой страны? Как меняется повестка? Какие нарративы набирают скорость?",
    icon: "📺",
    audience: "PR, коммуникации",
  },
  {
    title: "Risk Intelligence",
    desc: "Система раннего предупреждения: резкие скачки температуры → сигнал для оценки рисков бизнес-операций в регионе.",
    icon: "⚠️",
    audience: "Risk management, compliance",
  },
  {
    title: "Академические исследования",
    desc: "Количественный анализ медийного восприятия с объективной верификацией (ООН, торговля). Исторические данные с 2022 года.",
    icon: "🎓",
    audience: "Университеты, think tanks",
  },
];

/* ── Components ───────────────────────────────────────── */

function StatCard({ value, label, icon }: { value: string; label: string; icon: string }) {
  return (
    <div className="text-center p-4">
      <div className="text-3xl mb-1">{icon}</div>
      <div className="text-2xl md:text-3xl font-bold text-white">{value}</div>
      <div className="text-xs text-muted-foreground mt-1">{label}</div>
    </div>
  );
}

function PipelineStep({ step, index, totalSources }: { step: { num: string; title: string; subtitle: string; desc: string; icon: string; color: string; details: string[] }; index: number; totalSources: number }) {
  // Replace source count dynamically in subtitle
  const subtitle = step.num === "01" 
    ? `${totalSources} источников · 10 стран · 7 тиров`
    : step.subtitle;

  return (
    <div className="relative">
      {index < 4 && (
        <div className="hidden lg:block absolute top-12 left-[calc(100%+0.5rem)] w-8 border-t border-dashed border-zinc-700" />
      )}
      <Card className="border-border bg-card hover:border-blue-500/20 transition-all h-full group">
        <CardContent className="p-6">
          <div className="flex items-start gap-3 mb-3">
            <div
              className="w-10 h-10 rounded-lg flex items-center justify-center text-lg shrink-0"
              style={{ backgroundColor: step.color + "15", border: `1px solid ${step.color}33` }}
            >
              {step.icon}
            </div>
            <div>
              <span className="text-[10px] font-mono tracking-widest" style={{ color: step.color }}>
                ШАГ {step.num}
              </span>
              <h3 className="font-bold text-foreground text-lg leading-tight">{step.title}</h3>
            </div>
          </div>
          <p className="text-xs text-muted-foreground mb-3 font-medium" style={{ color: step.color + "aa" }}>
            {subtitle}
          </p>
          <p className="text-sm text-muted-foreground leading-relaxed mb-4">{step.desc}</p>
          <div className="space-y-1.5">
            {step.details.map((d) => (
              <div key={d} className="flex items-center gap-2 text-xs text-zinc-500">
                <div className="w-1 h-1 rounded-full shrink-0" style={{ backgroundColor: step.color }} />
                <span>{d}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────── */

export default function AboutPage() {
  const [stats, setStats] = useState<LiveStats | null>(null);
  const [tiers, setTiers] = useState<TierCount[]>([]);
  const [totalSources, setTotalSources] = useState(191);

  useEffect(() => {
    // Fetch live stats
    fetch(`${API_URL}/api/v1/stats?days=0`)
      .then(r => r.json())
      .then(d => setStats(d))
      .catch(() => {});

    // Fetch sources to compute tier counts
    fetch(`${API_URL}/api/v1/sources`)
      .then(r => r.json())
      .then(d => {
        const sources: SourceInfo[] = d.sources || [];
        setTotalSources(sources.length);
        const counts: Record<string, number> = {};
        sources.forEach(s => {
          const t = s.tier || "unknown";
          counts[t] = (counts[t] || 0) + 1;
        });
        const tierList: TierCount[] = Object.entries(counts)
          .map(([tier, count]) => ({
            tier: TIER_LABELS[tier] || tier,
            count,
            desc: TIER_META[tier]?.desc || tier,
            icon: TIER_META[tier]?.icon || "📄",
            order: TIER_META[tier]?.order || 99,
          }))
          .sort((a, b) => (a as TierCount & { order: number }).order - (b as TierCount & { order: number }).order);
        setTiers(tierList);
      })
      .catch(() => {});
  }, []);

  // Dynamic stats for the stats bar
  const liveStatCards = [
    { value: stats ? `${(stats.total_articles / 1000).toFixed(1)}K` : "…", label: "статей обработано", icon: "📰" },
    { value: String(totalSources), label: "источников", icon: "📡" },
    { value: "10", label: "стран", icon: "🌍" },
    { value: "7", label: "тиров доверия", icon: "🏛️" },
    { value: "30м", label: "цикл сбора", icon: "⏱️" },
    { value: "24/7", label: "автоматический мониторинг", icon: "🔄" },
  ];

  const PIPELINE_STEPS = [
    {
      num: "01",
      title: "Сбор данных",
      subtitle: `${totalSources} источников · 10 стран · 7 тиров`,
      desc: "Автоматический парсинг RSS-фидов и веб-страниц каждые 30 минут. Источники ранжированы по 7 уровням доверия — от госагентств и МИД до аналитических центров и Telegram-каналов.",
      icon: "📡",
      color: "#3b82f6",
      details: ["RSS + Web Scraping", "Мультиязычный сбор (ru, en, kk, uz, ky, hy, ka, ro)", "pg_trgm дедупликация", "Автоопределение языка (langdetect)"],
    },
    {
      num: "02",
      title: "AI-анализ",
      subtitle: "Claude Sonnet 4 · OpenRouter",
      desc: "Каждая статья проходит через LLM-классификатор: определяется тип события, уровень воздействия (1–6), тональность, ключевые персоны и геополитический контекст.",
      icon: "🧠",
      color: "#8b5cf6",
      details: ["Sentiment analysis (-3…+3)", "Action level classification (6 уровней)", "Entity & actor extraction", "Event type categorization", "6 компонентов: diplomatic, military, economic, cultural, security"],
    },
    {
      num: "03",
      title: "Температурный движок",
      subtitle: "Экспоненциальное затухание · τ=14 дней",
      desc: "Агрегированный индикатор от -60° до +60° рассчитывается с учётом веса источника, типа события, уровня воздействия, кластерного затухания и свежести.",
      icon: "🌡️",
      color: "#ef4444",
      details: ["sentiment × source_tier × action_multiplier", "Exponential decay (τ=14 дней)", "Cluster dedup factor 0.2^n", "Anomaly detection + pattern recognition", "Ежечасный пересчёт"],
    },
    {
      num: "04",
      title: "Сюжетные нити",
      subtitle: "Jina AI Embeddings · pgvector",
      desc: "AI группирует статьи в сюжетные нити через embedding-кластеризацию, отслеживает развитие каждой истории во времени и определяет фазу жизненного цикла.",
      icon: "🧵",
      color: "#f59e0b",
      details: ["Jina AI embeddings (1024d) + pgvector", "Arc phases: emerging → developing → escalating → peak → cooling", "Velocity & importance scoring", "Cross-source correlation", "Аудиторный сплит двуязычных СМИ"],
    },
    {
      num: "05",
      title: "Верификация",
      subtitle: "ООН · IMF DOTS · ФТС",
      desc: "Медийное восприятие проверяется объективными метриками — голосования ООН (2014–2023) и торговые потоки дают опорные точки для калибровки.",
      icon: "✅",
      color: "#10b981",
      details: ["UN General Assembly voting alignment", "IMF DOTS trade data", "Media-vs-reality correlation"],
    },
  ];

  return (
    <div className="space-y-16 pb-16">
      {/* ── Hero ── */}
      <section className="relative text-center py-16 overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-blue-500/5 via-transparent to-transparent" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-blue-500/8 rounded-full blur-[120px]" />

        <div className="relative z-10">
          <div className="flex items-center justify-center gap-3 mb-2">
            <span className="text-5xl md:text-6xl font-black tracking-tight text-blue-500">GEO</span>
            <span className="text-5xl md:text-6xl font-black tracking-tight text-white">PULSE</span>
          </div>
          <div className="flex items-center justify-center gap-2 mb-6">
            <div className="h-px w-12 bg-gradient-to-r from-transparent to-blue-500/50" />
            <span className="text-xs tracking-[0.3em] uppercase text-blue-400/70 font-medium">Geopolitical Intelligence Platform</span>
            <div className="h-px w-12 bg-gradient-to-l from-transparent to-blue-500/50" />
          </div>
          <p className="text-lg md:text-xl text-zinc-400 max-w-3xl mx-auto leading-relaxed mb-2">
            Платформа мониторинга и анализа медийной температуры отношений
            <br className="hidden md:block" />
            стран постсоветского пространства с Россией
          </p>
          <p className="text-sm text-zinc-600 max-w-2xl mx-auto">
            AI-анализ {totalSources} медиаисточников по 7 тирам доверия · Температурный индекс в реальном времени ·
            Сюжетные нити · Аудиторный сплит · Верификация данными ООН и торговой статистикой
          </p>
        </div>
      </section>

      {/* ── Stats bar (live) ── */}
      <section>
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2 rounded-2xl border border-border bg-card/50 p-4">
          {liveStatCards.map((s) => (
            <StatCard key={s.label} {...s} />
          ))}
        </div>
      </section>

      {/* ── Problem / Why ── */}
      <section className="max-w-4xl mx-auto">
        <h2 className="text-2xl font-bold text-center mb-8">
          <span className="text-blue-400">Зачем</span> это нужно
        </h2>
        <div className="grid md:grid-cols-2 gap-6">
          <Card className="border-red-500/20 bg-red-500/[0.03]">
            <CardContent className="p-6">
              <h3 className="text-lg font-semibold text-red-400 mb-3">❌ Без GEO PULSE</h3>
              <ul className="space-y-2 text-sm text-zinc-400">
                <li className="flex gap-2"><span className="text-red-500/50">—</span>Ручной мониторинг десятков СМИ на разных языках</li>
                <li className="flex gap-2"><span className="text-red-500/50">—</span>Субъективные оценки без количественных метрик</li>
                <li className="flex gap-2"><span className="text-red-500/50">—</span>Пропущенные сигналы и запоздалая реакция</li>
                <li className="flex gap-2"><span className="text-red-500/50">—</span>Нет исторического контекста для сравнения</li>
                <li className="flex gap-2"><span className="text-red-500/50">—</span>Разрыв между медийным шумом и реальностью</li>
              </ul>
            </CardContent>
          </Card>
          <Card className="border-green-500/20 bg-green-500/[0.03]">
            <CardContent className="p-6">
              <h3 className="text-lg font-semibold text-green-400 mb-3">✅ С GEO PULSE</h3>
              <ul className="space-y-2 text-sm text-zinc-400">
                <li className="flex gap-2"><span className="text-green-500/50">+</span>Автоматический охват {totalSources} источников 24/7</li>
                <li className="flex gap-2"><span className="text-green-500/50">+</span>Единая температурная шкала -60°…+60°</li>
                <li className="flex gap-2"><span className="text-green-500/50">+</span>Раннее обнаружение изменений тональности</li>
                <li className="flex gap-2"><span className="text-green-500/50">+</span>Нарративный расклад — кто что говорит и почему</li>
                <li className="flex gap-2"><span className="text-green-500/50">+</span>Верификация через голосования ООН и торговлю</li>
              </ul>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* ── How it works — Pipeline ── */}
      <section>
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold mb-2">
            <span className="text-blue-400">Как</span> это работает
          </h2>
          <p className="text-sm text-zinc-500">Полностью автоматизированный пайплайн от сбора до визуализации</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
          {PIPELINE_STEPS.map((step, i) => (
            <PipelineStep key={step.num} step={step} index={i} totalSources={totalSources} />
          ))}
        </div>
      </section>

      {/* ── Temperature Scale ── */}
      <section className="max-w-3xl mx-auto">
        <h2 className="text-2xl font-bold text-center mb-8">
          🌡️ <span className="text-blue-400">Шкала</span> температуры
        </h2>
        <div className="space-y-2">
          {SCALE.map((s) => (
            <div
              key={s.range}
              className="flex items-center gap-4 rounded-xl px-5 py-3 border border-border/50"
              style={{ backgroundColor: s.bg }}
            >
              <span className="text-2xl w-8 text-center">{s.emoji}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm font-bold" style={{ color: s.color }}>{s.range}</span>
                  <span className="text-sm font-semibold text-foreground">{s.label}</span>
                </div>
              </div>
              <Badge variant="secondary" className="text-[10px] shrink-0">{s.example}</Badge>
            </div>
          ))}
        </div>
        <p className="text-xs text-center text-zinc-600 mt-4">
          Формула: sentiment × source_weight × action_multiplier × decay(τ=14d) × cluster_factor · Обновляется ежечасно
        </p>
      </section>

      {/* ── Source Tiers (live) ── */}
      <section>
        <h2 className="text-2xl font-bold text-center mb-2">
          📡 <span className="text-blue-400">Источники</span>
        </h2>
        <p className="text-sm text-zinc-500 text-center mb-8">
          {totalSources} медиаисточников · 10 стран · ранжированы по 7 тирам доверия
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {(tiers.length > 0 ? tiers : [
            { tier: "Official", count: 81, desc: "Госагентства, МИД, Минобороны, центробанки", icon: "🏛️" },
            { tier: "Mainstream", count: 39, desc: "Крупные национальные СМИ, ТВ-порталы", icon: "📰" },
            { tier: "Independent", count: 21, desc: "Независимые медиа, расследования", icon: "🔍" },
            { tier: "Social", count: 21, desc: "Telegram-каналы, соцмедиа", icon: "💬" },
          ]).map((t) => (
            <Card key={t.tier} className="border-border bg-card hover:border-blue-500/20 transition-all">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className="text-lg">{t.icon}</span>
                    <span className="font-semibold text-foreground text-sm">{t.tier}</span>
                  </div>
                  <Badge variant="secondary" className="text-xs font-mono">{t.count}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">{t.desc}</p>
              </CardContent>
            </Card>
          ))}
        </div>
        <div className="flex flex-wrap justify-center gap-2 mt-6">
          {COUNTRIES.map((c) => (
            <Badge key={c.code} variant="outline" className="text-sm px-3 py-1.5 border-border">
              {c.flag} {c.name}
            </Badge>
          ))}
        </div>
      </section>

      {/* ── Key Findings ── */}
      <section className="max-w-4xl mx-auto">
        <h2 className="text-2xl font-bold text-center mb-8">
          🔬 <span className="text-blue-400">Ключевые</span> находки
        </h2>
        <div className="grid gap-4 md:grid-cols-2">
          {[
            {
              icon: "📡",
              title: "Медиа опережают дипломатию",
              desc: "Сдвиги медийного тона стабильно предшествуют официальным дипломатическим действиям на 2–4 недели.",
              color: "#3b82f6",
            },
            {
              icon: "🪞",
              title: "Молдова и Грузия — зеркала",
              desc: "Температурные кривые почти идентичны, формируя «прозападный коридор» на карте региона.",
              color: "#8b5cf6",
            },
            {
              icon: "⚖️",
              title: "Центральная Азия — нейтральный пояс",
              desc: "5 стран кучкуются вокруг 0° (±15°), отражая стратегию многовекторной политики.",
              color: "#f59e0b",
            },
            {
              icon: "🔬",
              title: "Аудиторный сплит",
              desc: "Двуязычные СМИ пишут одну новость по-разному: Vlast.kz → EN (-2.0) vs RU (+1.0). 37 таких источников обнаружено.",
              color: "#ef4444",
            },
          ].map((finding) => (
            <Card key={finding.title} className="border-border bg-card">
              <CardContent className="p-5">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xl">{finding.icon}</span>
                  <h3 className="font-bold text-foreground">{finding.title}</h3>
                </div>
                <p className="text-sm text-muted-foreground leading-relaxed">{finding.desc}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* ── Use Cases ── */}
      <section>
        <h2 className="text-2xl font-bold text-center mb-8">
          🎯 <span className="text-blue-400">Кому</span> это полезно
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {USE_CASES.map((uc) => (
            <Card key={uc.title} className="border-border bg-card hover:border-blue-500/20 transition-all">
              <CardContent className="p-6">
                <div className="flex items-start gap-3">
                  <span className="text-3xl">{uc.icon}</span>
                  <div>
                    <h3 className="font-bold text-foreground mb-1">{uc.title}</h3>
                    <p className="text-sm text-muted-foreground leading-relaxed mb-2">{uc.desc}</p>
                    <Badge variant="secondary" className="text-[10px]">{uc.audience}</Badge>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* ── Roadmap ── */}
      <section className="max-w-4xl mx-auto">
        <h2 className="text-2xl font-bold text-center mb-2">
          🗺️ <span className="text-blue-400">Roadmap</span>
        </h2>
        <p className="text-sm text-zinc-500 text-center mb-8">Куда мы движемся</p>
        <div className="space-y-6">
          {ROADMAP.map((phase) => (
            <div key={phase.phase} className="relative">
              <div className="flex items-center gap-3 mb-3">
                <div
                  className={`w-3 h-3 rounded-full shrink-0 ${
                    phase.status === "active"
                      ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]"
                      : phase.status === "next"
                        ? "bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.3)]"
                        : "bg-zinc-700"
                  }`}
                />
                <h3 className="font-bold text-lg text-foreground">{phase.phase}</h3>
                {phase.status === "active" && (
                  <Badge className="bg-green-500/10 text-green-400 border-green-500/30 text-[10px]">Live</Badge>
                )}
                {phase.status === "next" && (
                  <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/30 text-[10px]">Планируется</Badge>
                )}
              </div>
              <div className="ml-6 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {phase.items.map((item) => (
                  <div
                    key={item.title}
                    className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm transition-all ${
                      item.done
                        ? "border-green-500/20 bg-green-500/[0.03] text-zinc-300"
                        : "border-border bg-card text-zinc-400 hover:border-blue-500/20"
                    }`}
                  >
                    <span className="shrink-0 mt-0.5">{item.done ? "✅" : "🔮"}</span>
                    <span>{item.title}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Tech Stack ── */}
      <section className="max-w-3xl mx-auto">
        <h2 className="text-2xl font-bold text-center mb-8">
          🛠️ <span className="text-blue-400">Технологический</span> стек
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {[
            { layer: "Frontend", tech: "Next.js 16 · React 19 · TypeScript · Tailwind CSS v4 · shadcn/ui · Recharts · Plotly", icon: "🖥️" },
            { layer: "Backend", tech: "Python 3.11 · FastAPI · Uvicorn · 40 REST endpoints", icon: "⚙️" },
            { layer: "AI/ML", tech: "Claude Sonnet 4 (OpenRouter) · Jina AI Embeddings (1024d) · pgvector", icon: "🧠" },
            { layer: "Data", tech: "PostgreSQL · pgvector · Redis · RSS/Web Scraping · langdetect", icon: "💾" },
            { layer: "Infra", tech: "Docker Compose (11 контейнеров) · VPS · GitHub", icon: "🐳" },
            { layer: "Verification", tech: "UN Digital Library API · IMF DOTS · ФТС России", icon: "📊" },
          ].map((s) => (
            <Card key={s.layer} className="border-border bg-card">
              <CardContent className="p-4">
                <div className="flex items-center gap-2 mb-1">
                  <span>{s.icon}</span>
                  <span className="text-sm font-bold text-foreground">{s.layer}</span>
                </div>
                <p className="text-xs text-muted-foreground">{s.tech}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* ── Footer ── */}
      <section className="text-center py-12">
        <div className="max-w-2xl mx-auto">
          <p className="text-2xl font-bold text-foreground mb-3">
            Данные формируют картину. <span className="text-blue-400">AI находит смысл.</span>
          </p>
          <p className="text-sm text-zinc-500 mb-6">
            GEO PULSE — это не просто мониторинг. Это количественный подход к пониманию геополитической динамики постсоветского пространства.
          </p>
          <div className="flex items-center justify-center gap-2 text-xs text-zinc-600">
            <span className="font-bold text-blue-500">GEO PULSE</span>
            <span>· Geopolitical Intelligence Platform ·</span>
            <span>{stats ? `${stats.total_articles.toLocaleString("ru-RU")} статей проанализировано` : ""}</span>
          </div>
        </div>
      </section>
    </div>
  );
}
