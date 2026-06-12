"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  Banknote,
  Compass,
  Crosshair,
  Database,
  Code2,
  Globe2,
  Landmark,
  Layers,
  Mail,
  Newspaper,
  Zap,
} from "lucide-react";
import SiteHeader from "@/components/SiteHeader";
import { api } from "@/lib/api";
import type { Meta, SourceRow } from "@/lib/types";

interface StatsState {
  total: number;
  active: number;
  articles: number;
  countries: number;
  loaded: boolean;
  error: boolean;
}

const SCALE_LEVELS = [
  { label: "Враждебный", range: "< −70", bg: "bg-hostile" },
  { label: "Напряжённость", range: "−70…−40", bg: "bg-tension" },
  { label: "Охлаждение", range: "−40…−15", bg: "bg-cooling" },
  { label: "Нейтральный", range: "−15…+15", bg: "bg-neutral" },
  { label: "Партнёр", range: "+15…+45", bg: "bg-partner" },
  { label: "Союзник", range: "≥ +45", bg: "bg-ally" },
] as const;

const TASKS = [
  {
    icon: Compass,
    title: "Понимать контекст страны до начала работы с ней.",
    text: "Как медиа страны говорят о России прямо сейчас, какие темы доминируют, что произошло за последние месяцы, где отношения теплеют, а где остывают — и почему. Досье страны собирает это на одном экране: индекс, события, темы, торговля, голосования в ООН.",
  },
  {
    icon: Crosshair,
    title: "Точнее считывать целевые аудитории.",
    text: "В одной и той же стране официальная пресса, мейнстрим, независимые медиа и оппозиция говорят о России по-разному — это разные аудитории с разным отношением. Мы разделяем источники по тирам и показываем расхождения явно: с кем и на каком языке имеет смысл разговаривать.",
  },
  {
    icon: Zap,
    title: "Работать на опережение.",
    text: "Сигнальный движок ловит аномалии — всплески внимания к России, резкие сдвиги тона, замалчивание — раньше, чем они становятся очевидными. Это даёт время подготовить позицию, а не реагировать постфактум.",
  },
] as const;

function SectionHead({ num, title }: { num: string; title: string }) {
  return (
    <div className="mb-5 mt-14">
      <div className="flex items-baseline gap-3">
        <span className="section-num">{num}</span>
        <h2 className="display text-[26px] leading-tight sm:text-[30px]">{title}</h2>
      </div>
      <span className="tricolor mt-3 max-w-[120px] opacity-70" aria-hidden="true" />
    </div>
  );
}

export default function AboutPage() {
  const [stats, setStats] = useState<StatsState>({
    total: 0, active: 0, articles: 0, countries: 0, loaded: false, error: false,
  });

  useEffect(() => {
    Promise.allSettled([api.sources(), api.meta()]).then(([sourcesResult, metaResult]) => {
      const allFailed =
        sourcesResult.status === "rejected" && metaResult.status === "rejected";
      const sources: SourceRow[] =
        sourcesResult.status === "fulfilled" ? sourcesResult.value.sources : [];
      const meta: Meta | null =
        metaResult.status === "fulfilled" ? metaResult.value : null;
      setStats({
        total: sources.length,
        active: sources.filter((s) => s.active).length,
        articles: sources.reduce((sum, s) => sum + (s.article_count ?? 0), 0),
        countries: meta?.countries?.length ?? 0,
        loaded: !allFailed,
        error: allFailed,
      });
    });
  }, []);

  const fmt = (n: number) => n.toLocaleString("ru");
  const ph = "…";
  const STATS = [
    [stats.total, "источников"],
    [stats.active, "активных"],
    [stats.articles, "статей собрано"],
    [stats.countries, "стран мира"],
  ] as const;

  return (
    <main className="mx-auto max-w-[760px] px-4 pb-20">
      <SiteHeader active="/about" />

      {/* ── hero ── */}
      <section className="pt-14">
        <p className="reveal reveal-1 section-num">О проекте</p>
        <h1 className="display reveal reveal-2 mt-4 text-[38px] leading-[1.12] sm:text-[52px]">
          Как мир сегодня относится к&nbsp;России?
        </h1>
        <p className="lead reveal reveal-3 mt-6">
          «Массаракш» — попытка честно ответить на один вопрос. Каждый час система
          читает сотни СМИ на десятках языков, считает «градусник отношений» для
          99 стран и показывает результат — без купюр и без украшательств.
        </p>

        {/* fact strip */}
        <div className="reveal reveal-5 mt-10 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4">
          {STATS.map(([v, label]) => (
            <div key={label} className="bg-panel px-4 py-4">
              <div className="tnum text-[22px] font-medium text-ru-white">
                {stats.loaded ? fmt(v as number) : ph}
              </div>
              <div className="mt-1 text-[11px] uppercase tracking-[0.14em] text-dim">
                {label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── 01 mission ── */}
      <SectionHead num="01" title="Зачем этот проект" />
      <section className="prose-editorial">
        <p className="dropcap mb-4">
          Изнутри страны трудно увидеть себя со стороны. Мы в России мало можем сами себя
          оценить и понять — а что на самом деле думают о нас люди в других странах, что
          пишут про нас их СМИ, обычно остаётся за кадром: до нас доходят либо пересказы,
          либо чьи-то интерпретации.
        </p>
        <p className="mb-6">
          «Массаракш» создан для тех, кто работает с другими странами от лица России и в
          интересах России: для дипломатов и международников, экспортёров и команд выхода
          на зарубежные рынки, специалистов по коммуникациям и GR, аналитиков и
          исследователей. Инструмент решает три рабочие задачи:
        </p>

        <div className="mb-8 space-y-3">
          {TASKS.map((t, i) => (
            <div
              key={i}
              className="card group flex gap-4 px-5 py-4 transition-transform hover:-translate-y-0.5"
            >
              <t.icon
                className="mt-1 h-5 w-5 shrink-0 text-ru-blue transition-colors group-hover:text-ru-red"
                strokeWidth={1.75}
                aria-hidden="true"
              />
              <div>
                <div className="display mb-1 text-[16.5px]">{t.title}</div>
                <p className="text-[14px] leading-relaxed text-dim">{t.text}</p>
              </div>
            </div>
          ))}
        </div>

        <blockquote className="pull-quote my-10 border-l-2 border-ru-red pl-6">
          …тем привлекательнее Россия выглядит в глазах мира: не за счёт громких слов,
          а за счёт понимания.
        </blockquote>
        <p className="mb-4">
          Мы верим, что чем точнее российские специалисты понимают контекст и аудитории
          других стран, тем качественнее идёт диалог — и тем привлекательнее Россия
          выглядит в глазах мира: не за счёт громких слов, а за счёт понимания.
        </p>
        <p>
          При этом «Массаракш» — <b>не пропаганда и не её разоблачение</b>. Это
          измерительный инструмент: показываем как есть, объясняем методику и честно
          говорим о её пределах — завышенные ожидания подводят специалиста так же, как и
          отсутствие данных.
        </p>
      </section>

      {/* ── 02 methodology ── */}
      <SectionHead num="02" title="Как работает «градусник отношений»" />
      <section className="prose-editorial">
        <p className="mb-6">
          Для каждой из 99 стран мы считаем <b>индекс отношений с Россией (RRI)</b> —
          число от −100 до +100. Это не «правда в последней инстанции», а взвешенная
          сумма проверяемых фактов и измеримого медиафона. Индекс складывается из трёх
          частей:
        </p>

        <div className="mb-8 space-y-6">
          {[
            {
              icon: Layers,
              n: "1",
              h: "Структурный слой — медленный фундамент",
              p: "То, что не меняется от одной новости: в каких блоках состоит страна (ОДКБ, ЕАЭС, СНГ, ШОС, БРИКС — плюс к индексу; НАТО, ЕС, G7 — минус), ввела ли она санкции против России, входит ли в перечень «недружественных», как голосует в Генассамблее ООН по сравнению с Россией. Каждый фактор имеет фиксированный вес — все веса открыты в коде.",
            },
            {
              icon: Activity,
              n: "2",
              h: "Медиа-слой — быстрый пульс",
              p: "Как СМИ самой страны пишут о России прямо сейчас. Для 10 стран постсоветского пространства мы читаем их собственные медиа напрямую — 191 источник, каждая статья проходит AI-анализ тональности. Для остальных 89 стран используем GDELT — глобальную базу, которая ежедневно измеряет тон упоминаний России в прессе каждой страны.",
            },
            {
              icon: Zap,
              n: "3",
              h: "Бусты событий",
              p: "Резкие события — военные инциденты, разрывы отношений, крупные соглашения — на 14 дней сдвигают индекс вверх или вниз (не более чем на ±15 пунктов), а потом их влияние сходит на нет.",
            },
          ].map((b) => (
            <div key={b.n} className="flex gap-4">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-panel">
                <b.icon className="h-4 w-4 text-ru-blue" strokeWidth={1.75} aria-hidden="true" />
              </div>
              <div>
                <h3 className="display mb-1 text-[17px]">{b.h}</h3>
                <p className="text-[14px] leading-relaxed text-dim">{b.p}</p>
              </div>
            </div>
          ))}
        </div>

        <aside className="card mb-8 border-l-2 border-l-ru-blue px-5 py-4">
          <h3 className="display mb-1 text-[16px]">Полы и потолки</h3>
          <p className="text-[14px] leading-relaxed text-dim">
            Жёсткие факты ограничивают индекс независимо от медийного шума: у страны,
            ведущей боевые действия против России, индекс не поднимется выше −75, как бы
            ни менялся тон прессы; у участника Союзного государства не опустится ниже
            +25. Так одна громкая статья не сможет «перекрасить» карту.
          </p>
        </aside>

        <h3 className="card-title mb-3">Шесть уровней шкалы</h3>
        <div className="overflow-x-auto">
          <div aria-hidden="true" className="tricolor-draw flex min-w-[460px] overflow-hidden rounded-md">
            {SCALE_LEVELS.map((lvl) => (
              <div key={lvl.label} className={`flex-1 ${lvl.bg} py-2.5`} />
            ))}
          </div>
          <div className="mt-2 flex min-w-[460px]">
            {SCALE_LEVELS.map((lvl) => (
              <div key={lvl.label} className="flex-1 px-1 text-center">
                <div className="text-[10.5px] font-semibold leading-tight text-ru-white">
                  {lvl.label}
                </div>
                <div className="tnum text-[9.5px] leading-tight text-dim">{lvl.range}</div>
              </div>
            ))}
          </div>
        </div>
        <p className="mt-5">
          Структурный и медийный слои смешиваются с разными весами: там, где мы читаем
          местные СМИ напрямую, медиа-сигналу доверия больше (55%); там, где работаем
          через GDELT, опора смещена на структурные факты (60%).
        </p>
      </section>

      {/* ── 03 data ── */}
      <SectionHead num="03" title="Откуда данные" />
      <section className="prose-editorial">
        <ul className="mb-6 space-y-4">
          {[
            {
              icon: Newspaper,
              b: "191 источник в 10 странах СНГ",
              t: " — от государственных агентств и МИД до независимых медиа, оппозиционных изданий и Telegram-каналов. Источники разделены на 7 «тиров доверия», и мы специально сравниваем, как одно событие звучит в разных тирах: расхождение между официальной и независимой прессой — само по себе сигнал.",
            },
            {
              icon: Globe2,
              b: "147 мировых фидов из 82 стран",
              t: " — международная пресса о России.",
            },
            {
              icon: Database,
              b: "GDELT",
              t: " — тон и объём упоминаний России в СМИ каждой из 99 стран, обновляется ежедневно.",
            },
            {
              icon: Landmark,
              b: "Голосования Генассамблеи ООН",
              t: " — объективная опора для структурного слоя.",
            },
            {
              icon: Banknote,
              b: "Курсы валют ЦБ РФ",
              t: " — финансовый фон для кросс-проверки медийных сигналов.",
            },
          ].map((d, i) => (
            <li key={i} className="flex gap-3">
              <d.icon className="mt-1 h-4 w-4 shrink-0 text-ru-blue" strokeWidth={1.75} aria-hidden="true" />
              <span>
                <b>{d.b}</b>
                {d.t}
              </span>
            </li>
          ))}
        </ul>
        <p className="mb-4">
          Помимо индекса система ищет аномалии: всплески внимания к России, резкие сдвиги
          тона, ситуации, когда о событии шумят независимые источники, а официальные
          молчат.
        </p>
        <p>
          Полный список источников с их статусом — на странице{" "}
          <Link href="/sources" className="text-accent underline-offset-4 hover:underline">
            Источники
          </Link>
          . Все веса, формулы и реестр стран открыты в коде проекта.
        </p>
      </section>

      {/* ── 04 limitations ── */}
      <SectionHead num="04" title="Ограничения и честные оговорки" />
      <section className="prose-editorial">
        <p className="mb-5">
          Мы хотим, чтобы вы доверяли инструменту ровно настолько, насколько он того
          заслуживает. Поэтому прямо:
        </p>
        <ol className="space-y-4">
          {[
            {
              b: "Тон СМИ — это не мнение народа.",
              t: " Пресса страны может быть жёстче или мягче, чем настроения её жителей. Мы измеряем медиаполе, а не опросы общественного мнения.",
            },
            {
              b: "Покрытие неравномерно.",
              t: " По 10 странам СНГ мы читаем местные СМИ напрямую и глубоко; по остальным 89 опираемся на GDELT — это надёжный, но более грубый сигнал. Для малых стран с небольшим медиаполем дневные колебания могут быть шумом.",
            },
            {
              b: "AI ошибается.",
              t: " Тональность статей оценивает языковая модель. На больших объёмах ошибки усредняются, но отдельная статья может быть распознана неверно.",
            },
            {
              b: "Веса — экспертные, а не богом данные.",
              t: " Сколько «стоит» членство в ОДКБ или санкции — наше методологическое решение. Мы держим все веса открытыми, документируем спорные позиции реестра (статус Армении в ОДКБ, страны-партнёры БРИКС) и пересматриваем их; история изменений — в git.",
            },
            {
              b: "Индекс — термометр, а не диагноз.",
              t: " Он показывает температуру отношений, но не объясняет всю их глубину. Для понимания контекста смотрите досье страны: темы, события, сигналы и AI-брифинг.",
            },
          ].map((d, i) => (
            <li key={i} className="flex gap-4">
              <span className="tnum mt-0.5 text-[13px] text-ru-red">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span>
                <b>{d.b}</b>
                {d.t}
              </span>
            </li>
          ))}
        </ol>
      </section>

      {/* ── 05 open source ── */}
      <SectionHead num="05" title="Открытый код и контакты" />
      <section className="prose-editorial">
        <p className="mb-5">
          «Массаракш» — открытый проект под лицензией <b>AGPL-3.0</b>. Весь код, веса
          индекса и реестр стран доступны на GitHub — методику можно проверить, оспорить
          или улучшить:
        </p>
        <ul className="mb-5 space-y-3">
          <li className="flex items-center gap-3">
            <Code2 className="h-4 w-4 shrink-0 text-ru-blue" strokeWidth={1.75} aria-hidden="true" />
            <a
              href="https://github.com/milkmike/GEO_PULSE"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent underline-offset-4 hover:underline"
            >
              github.com/milkmike/GEO_PULSE
            </a>
          </li>
          <li className="flex items-center gap-3">
            <Mail className="h-4 w-4 shrink-0 text-ru-blue" strokeWidth={1.75} aria-hidden="true" />
            <a
              href="mailto:mishtkachenk@gmail.com"
              className="text-accent underline-offset-4 hover:underline"
            >
              mishtkachenk@gmail.com
            </a>
          </li>
        </ul>
        <p className="mb-4 text-[14px] text-dim">
          Часть методологий адаптирована из открытого проекта{" "}
          <a
            href="https://github.com/koala73/worldmonitor"
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent underline-offset-4 hover:underline"
          >
            World Monitor
          </a>{" "}
          (Elie Habib, AGPL-3.0) — спасибо автору.
        </p>
        <p className="mb-10">
          Нашли ошибку в данных или несогласны с весом фактора? Напишите или откройте
          issue — спор о методике для нас ценнее согласия по инерции.
        </p>

        <footer className="border-t border-line pt-8 text-center">
          <p className="pull-quote mx-auto max-w-[540px] text-[20px]">
            «Массаракш» измеряет, как мир говорит о России, — чтобы мы научились лучше
            слышать мир.
          </p>
          <span className="tricolor mx-auto mt-8 max-w-[120px]" aria-hidden="true" />
        </footer>
      </section>
    </main>
  );
}
