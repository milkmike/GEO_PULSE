import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const SCALE = [
  { level: "💥💥💥", range: "6 — Военные действия", mult: "×15", color: "bg-red-500/20 border-red-500/40 text-red-300" },
  { level: "💥💥", range: "5 — Разрыв/выход", mult: "×12", color: "bg-red-500/15 border-red-500/30 text-red-400" },
  { level: "💥", range: "4 — Санкции/запрет", mult: "×8", color: "bg-orange-500/15 border-orange-500/30 text-orange-400" },
  { level: "⚡⚡⚡", range: "3 — Соглашение", mult: "×5", color: "bg-yellow-500/15 border-yellow-500/30 text-yellow-400" },
  { level: "⚡⚡", range: "2 — Переговоры/визит", mult: "×3", color: "bg-blue-500/15 border-blue-500/30 text-blue-400" },
  { level: "⚡", range: "1 — Заявление", mult: "×1", color: "bg-gray-500/15 border-gray-500/30 text-gray-400" },
];

const INSIGHTS = [
  {
    title: "Беларусь — абсолютный лидер по «теплу»",
    text: "Температура стабильно +20°..+40°. Союзное государство — не фигура речи, а ежедневная реальность.",
    emoji: "🇧🇾",
    color: "border-red-500/30",
  },
  {
    title: "Молдова — самая «холодная» страна",
    text: "-30°..-40°. Европейский курс, конфликт вокруг Приднестровья, антироссийская риторика.",
    emoji: "🇲🇩",
    color: "border-blue-500/30",
  },
  {
    title: "Центральная Азия — зона прагматизма",
    text: "Температура около нуля. Сближение через экономику. Минимум политической риторики.",
    emoji: "🏔️",
    color: "border-yellow-500/30",
  },
  {
    title: "Кавказ — тектонический разлом",
    text: "Грузия и Армения в минусе. Азербайджан балансирует. Карабахский фактор создаёт непредсказуемость.",
    emoji: "⛰️",
    color: "border-orange-500/30",
  },
  {
    title: "ООН коррелирует с температурой, но не линейно",
    text: "Беларусь совпадает с Россией в 81%, Молдова — в 44%. Страны со средним совпадением (~70%) — в зоне наибольшей непредсказуемости.",
    emoji: "🗳️",
    color: "border-purple-500/30",
  },
];

const COUNTRIES = [
  "🇰🇿 Казахстан", "🇦🇲 Армения", "🇺🇿 Узбекистан", "🇰🇬 Кыргызстан",
  "🇹🇯 Таджикистан", "🇹🇲 Туркменистан", "🇦🇿 Азербайджан",
  "🇬🇪 Грузия", "🇲🇩 Молдова", "🇧🇾 Беларусь",
];

const TECH = [
  { label: "Сбор", value: "RSS + web scraping" },
  { label: "Анализ", value: "LLM (OpenRouter)" },
  { label: "Хранение", value: "PostgreSQL" },
  { label: "Визуализация", value: "Next.js + shadcn/ui + Recharts" },
  { label: "Деплой", value: "Docker" },
];

const DATA_SOURCES = [
  { label: "Медийный sentiment", value: "обновляется каждые 6 часов" },
  { label: "Голосования ООН", value: "2014-2023 (UN Digital Library)" },
  { label: "Торговля", value: "UN Comtrade API + ФТС России" },
];

export default function AboutPage() {
  return (
    <div className="space-y-12 pb-12">
      {/* Hero */}
      <section className="text-center py-8">
        <div className="flex items-center justify-center gap-3 mb-4">
          <span className="text-4xl font-bold tracking-wider text-blue-500">GEO</span>
          <span className="text-4xl font-bold tracking-wider text-white">PULSE</span>
        </div>
        <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
          Аналитическая платформа мониторинга медийной температуры отношений стран СНГ с Россией
        </p>
      </section>

      {/* How it works */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-center">⚙️ Как это работает</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { step: "1", title: "Сбор", desc: "100+ СМИ из 10 стран, на русском и национальных языках", icon: "📥" },
            { step: "2", title: "Анализ", desc: "AI классифицирует каждую статью: тип события, уровень воздействия (1-6), sentiment", icon: "🧠" },
            { step: "3", title: "Температура", desc: "Агрегированный индикатор (-100°..+100°) с экспоненциальным затуханием (τ=14 дней)", icon: "🌡️" },
            { step: "4", title: "Верификация", desc: "Объективные метрики (ООН, торговля) для корреляции с медийным восприятием", icon: "✅" },
          ].map((item) => (
            <Card key={item.step} className="border-border bg-card hover:border-blue-500/30 transition-all">
              <CardContent className="p-5">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-2xl">{item.icon}</span>
                  <span className="text-sm font-bold text-blue-400">Шаг {item.step}</span>
                </div>
                <h3 className="font-semibold text-foreground mb-1">{item.title}</h3>
                <p className="text-sm text-muted-foreground">{item.desc}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* Temperature Scale */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-center">🌡️ Шкала температуры</h2>
        <Card className="border-border bg-card overflow-x-auto">
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="px-4 py-3 font-medium">Уровень</th>
                  <th className="px-4 py-3 font-medium">Диапазон</th>
                  <th className="px-4 py-3 font-medium text-right">Множитель</th>
                </tr>
              </thead>
              <tbody>
                {SCALE.map((row) => (
                  <tr key={row.range} className={`border-b border-border/50 ${row.color}`}>
                    <td className="px-4 py-3 text-lg">{row.level}</td>
                    <td className="px-4 py-3 font-medium">{row.range}</td>
                    <td className="px-4 py-3 text-right font-bold">{row.mult}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </section>

      {/* Coverage */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-center">🗺️ Покрытие — 10 стран</h2>
        <div className="flex flex-wrap justify-center gap-2">
          {COUNTRIES.map((c) => (
            <Badge key={c} variant="secondary" className="text-sm px-3 py-1.5">
              {c}
            </Badge>
          ))}
        </div>
      </section>

      {/* Key Insights */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-center">💡 5 ключевых инсайтов</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {INSIGHTS.map((insight, i) => (
            <Card key={i} className={`border-border bg-card ${insight.color} hover:bg-white/[0.03] transition-all`}>
              <CardContent className="p-5">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-2xl">{insight.emoji}</span>
                  <Badge variant="secondary" className="text-[10px]">{i + 1}</Badge>
                </div>
                <h3 className="font-semibold text-foreground mb-2">{insight.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{insight.text}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* Tech & Data */}
      <section className="grid gap-6 md:grid-cols-2">
        <div className="space-y-4">
          <h2 className="text-xl font-semibold">🛠️ Технологии</h2>
          <Card className="border-border bg-card">
            <CardContent className="p-4 space-y-2">
              {TECH.map((t) => (
                <div key={t.label} className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">{t.label}</span>
                  <span className="font-medium text-foreground">{t.value}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
        <div className="space-y-4">
          <h2 className="text-xl font-semibold">📦 Данные</h2>
          <Card className="border-border bg-card">
            <CardContent className="p-4 space-y-2">
              {DATA_SOURCES.map((d) => (
                <div key={d.label} className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">{d.label}</span>
                  <span className="font-medium text-foreground">{d.value}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </section>
    </div>
  );
}
