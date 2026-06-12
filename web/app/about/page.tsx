"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Meta } from "@/lib/types";

interface SourceEntry {
  id: number;
  active: boolean;
  article_count: number;
  country_code: string;
}

interface StatsState {
  total: number;
  active: number;
  articles: number;
  countries: number;
  loaded: boolean;
}

const SCALE_LEVELS = [
  { label: "Враждебный", range: "< −70", bg: "bg-red-600" },
  { label: "Напряжённость", range: "−70…−40", bg: "bg-orange-500" },
  { label: "Охлаждение", range: "−40…−15", bg: "bg-yellow-500" },
  { label: "Нейтральный", range: "−15…+15", bg: "bg-zinc-400" },
  { label: "Партнёр", range: "+15…+45", bg: "bg-emerald-400" },
  { label: "Союзник", range: "≥ +45", bg: "bg-emerald-600" },
] as const;

export default function AboutPage() {
  const [stats, setStats] = useState<StatsState>({
    total: 0,
    active: 0,
    articles: 0,
    countries: 0,
    loaded: false,
  });

  useEffect(() => {
    Promise.allSettled([api.sources(), api.meta()]).then(([sourcesResult, metaResult]) => {
      const sources: SourceEntry[] =
        sourcesResult.status === "fulfilled" ? sourcesResult.value.sources : [];
      const meta: Meta | null =
        metaResult.status === "fulfilled" ? metaResult.value : null;

      setStats({
        total: sources.length,
        active: sources.filter((s) => s.active).length,
        articles: sources.reduce((sum, s) => sum + (s.article_count ?? 0), 0),
        countries: meta?.countries?.length ?? 0,
        loaded: true,
      });
    });
  }, []);

  const fmt = (n: number) => n.toLocaleString("ru");
  const placeholder = "…";

  return (
    <main className="mx-auto max-w-3xl px-3 pb-10">
      {/* Header */}
      <header className="flex items-center gap-3 py-3">
        <Link href="/" className="text-xs text-dim hover:text-accent">
          ← на главную
        </Link>
        <h1 className="text-base font-semibold">О проекте</h1>
      </header>

      {/* Hero */}
      <section className="mb-4">
        <p className="text-base font-bold leading-snug text-white">
          GEO PULSE — попытка честно ответить на один вопрос: как мир сегодня относится к России?
        </p>
        <p className="mt-2 text-sm text-dim">
          Каждый час система читает сотни СМИ на десятках языков, считает «градусник отношений»
          для 99 стран и показывает результат — без купюр и без украшательств.
        </p>
      </section>

      {/* Live stats strip */}
      <section className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="card px-4 py-3 text-center">
          <div className="text-xl font-bold text-accent">
            {stats.loaded ? fmt(stats.total) : placeholder}
          </div>
          <div className="mt-0.5 text-[11px] text-dim">источников</div>
        </div>
        <div className="card px-4 py-3 text-center">
          <div className="text-xl font-bold text-accent">
            {stats.loaded ? fmt(stats.active) : placeholder}
          </div>
          <div className="mt-0.5 text-[11px] text-dim">активных</div>
        </div>
        <div className="card px-4 py-3 text-center">
          <div className="text-xl font-bold text-accent">
            {stats.loaded ? fmt(stats.articles) : placeholder}
          </div>
          <div className="mt-0.5 text-[11px] text-dim">статей собрано</div>
        </div>
        <div className="card px-4 py-3 text-center">
          <div className="text-xl font-bold text-accent">
            {stats.loaded ? fmt(stats.countries) : placeholder}
          </div>
          <div className="mt-0.5 text-[11px] text-dim">стран</div>
        </div>
      </section>

      {/* Section: Mission */}
      <section className="card mb-3 px-5 py-4">
        <h2 className="card-title mb-3">Зачем этот проект</h2>
        <p className="mb-3 text-sm text-dim">
          Изнутри страны трудно увидеть себя со стороны. Мы в России мало можем сами себя оценить
          и понять — а что на самом деле думают о нас люди в других странах, что пишут про нас их
          СМИ, обычно остаётся за кадром: до нас доходят либо пересказы, либо чьи-то интерпретации.
        </p>
        <p className="mb-3 text-sm text-dim">
          GEO PULSE создан для тех, кто работает с другими странами от лица России и в интересах
          России: для дипломатов и международников, экспортёров и команд выхода на зарубежные
          рынки, специалистов по коммуникациям и GR, аналитиков и исследователей. Инструмент
          решает три рабочие задачи:
        </p>
        <ul className="mb-3 space-y-3 text-sm text-dim">
          <li>
            <span className="font-semibold text-white">
              Понимать контекст страны до начала работы с ней.
            </span>{" "}
            Как медиа страны говорят о России прямо сейчас, какие темы доминируют, что произошло
            за последние месяцы, где отношения теплеют, а где остывают — и почему. Досье страны
            собирает это на одном экране: индекс, события, темы, торговля, голосования в ООН.
          </li>
          <li>
            <span className="font-semibold text-white">
              Точнее считывать целевые аудитории.
            </span>{" "}
            В одной и той же стране официальная пресса, мейнстрим, независимые медиа и оппозиция
            говорят о России по-разному — это разные аудитории с разным отношением. Мы разделяем
            источники по тирам и показываем расхождения явно: с кем и на каком языке имеет смысл
            разговаривать.
          </li>
          <li>
            <span className="font-semibold text-white">
              Работать на опережение.
            </span>{" "}
            Сигнальный движок ловит аномалии — всплески внимания к России, резкие сдвиги тона,
            замалчивание — раньше, чем они становятся очевидными. Это даёт время подготовить
            позицию, а не реагировать постфактум.
          </li>
        </ul>
        <p className="mb-3 text-sm text-dim">
          Мы верим, что чем точнее российские специалисты понимают контекст и аудитории других
          стран, тем качественнее идёт диалог — и тем привлекательнее Россия выглядит в глазах
          мира: не за счёт громких слов, а за счёт понимания.
        </p>
        <p className="text-sm text-dim">
          При этом GEO PULSE — не пропаганда и не её разоблачение. Это измерительный инструмент:
          показываем как есть, объясняем методику и честно говорим о её пределах — завышенные
          ожидания подводят специалиста так же, как и отсутствие данных.
        </p>
      </section>

      {/* Section: RRI methodology */}
      <section className="card mb-3 px-5 py-4">
        <h2 className="card-title mb-3">Как работает «градусник отношений»</h2>
        <p className="mb-4 text-sm text-dim">
          Для каждой из 99 стран мы считаем{" "}
          <span className="font-semibold text-white">
            индекс отношений с Россией (RRI)
          </span>{" "}
          — число от −100 до +100. Это не «правда в последней инстанции», а взвешенная сумма
          проверяемых фактов и измеримого медиафона.
        </p>
        <p className="mb-3 text-sm text-dim">Индекс складывается из трёх частей:</p>

        <div className="mb-4 space-y-4">
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-accent">
              1. Структурный слой — медленный фундамент
            </h3>
            <p className="text-sm text-dim">
              То, что не меняется от одной новости: в каких блоках состоит страна (ОДКБ, ЕАЭС,
              СНГ, ШОС, БРИКС — плюс к индексу; НАТО, ЕС, G7 — минус), ввела ли она санкции
              против России, входит ли в перечень «недружественных», как голосует в Генассамблее
              ООН по сравнению с Россией. Каждый фактор имеет фиксированный вес — все веса открыты
              в коде.
            </p>
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-accent">
              2. Медиа-слой — быстрый пульс
            </h3>
            <p className="text-sm text-dim">
              Как СМИ самой страны пишут о России прямо сейчас. Для 10 стран постсоветского
              пространства мы читаем их собственные медиа напрямую — 191 источник, каждая статья
              проходит AI-анализ тональности. Для остальных 89 стран используем{" "}
              <a
                href="https://www.gdeltproject.org/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent hover:underline"
              >
                GDELT
              </a>{" "}
              — глобальную базу, которая ежедневно измеряет тон упоминаний России в прессе каждой
              страны.
            </p>
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-accent">
              3. Бусты событий
            </h3>
            <p className="text-sm text-dim">
              Резкие события — военные инциденты, разрывы отношений, крупные соглашения — на 14
              дней сдвигают индекс вверх или вниз (не более чем на ±15 пунктов), а потом их
              влияние сходит на нет.
            </p>
          </div>
        </div>

        <div className="mb-4">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-accent">
            Полы и потолки
          </h3>
          <p className="text-sm text-dim">
            Жёсткие факты ограничивают индекс независимо от медийного шума: у страны, ведущей
            боевые действия против России, индекс не поднимется выше −75, как бы ни менялся тон
            прессы; у участника Союзного государства не опустится ниже +25. Так одна громкая
            статья не сможет «перекрасить» карту.
          </p>
        </div>

        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-accent">
            Шесть уровней
          </h3>
          {/* Colored horizontal bar */}
          <div className="overflow-x-auto">
            <div className="flex min-w-[420px] rounded-md overflow-hidden">
              {SCALE_LEVELS.map((lvl) => (
                <div key={lvl.label} className={`flex-1 ${lvl.bg} px-1 py-2`} />
              ))}
            </div>
            <div className="flex min-w-[420px] mt-1">
              {SCALE_LEVELS.map((lvl) => (
                <div key={lvl.label} className="flex-1 px-1 text-center">
                  <div className="text-[10px] font-semibold leading-tight text-white">
                    {lvl.label}
                  </div>
                  <div className="text-[9px] leading-tight text-dim">{lvl.range}</div>
                </div>
              ))}
            </div>
          </div>
          <p className="mt-3 text-sm text-dim">
            Структурный и медийный слои смешиваются с разными весами: там, где мы читаем местные
            СМИ напрямую, медиа-сигналу доверия больше (55%); там, где работаем через GDELT,
            опора смещена на структурные факты (60%).
          </p>
        </div>
      </section>

      {/* Section: Data sources */}
      <section className="card mb-3 px-5 py-4">
        <h2 className="card-title mb-3">Откуда данные</h2>
        <ul className="mb-3 space-y-2 text-sm text-dim">
          <li>
            <span className="font-semibold text-white">191 источник в 10 странах СНГ</span> — от
            государственных агентств и МИД до независимых медиа, оппозиционных изданий и
            Telegram-каналов. Источники разделены на 7 «тиров доверия», и мы специально сравниваем,
            как одно событие звучит в разных тирах: расхождение между официальной и независимой
            прессой — само по себе сигнал.
          </li>
          <li>
            <span className="font-semibold text-white">147 мировых фидов из 82 стран</span> —
            международная пресса о России.
          </li>
          <li>
            <span className="font-semibold text-white">GDELT</span> — тон и объём упоминаний
            России в СМИ каждой из 99 стран, обновляется ежедневно.
          </li>
          <li>
            <span className="font-semibold text-white">Голосования Генассамблеи ООН</span> —
            объективная опора для структурного слоя.
          </li>
          <li>
            <span className="font-semibold text-white">Курсы валют ЦБ РФ</span> — финансовый фон
            для кросс-проверки медийных сигналов.
          </li>
        </ul>
        <p className="mb-3 text-sm text-dim">
          Помимо индекса система ищет аномалии: всплески внимания к России, резкие сдвиги тона,
          ситуации, когда о событии шумят независимые источники, а официальные молчат.
        </p>
        <p className="text-sm text-dim">
          Полный список источников с их статусом — на странице{" "}
          <Link href="/sources" className="text-accent hover:underline">
            Источники
          </Link>
          . Все веса, формулы и реестр стран открыты в коде проекта.
        </p>
      </section>

      {/* Section: Limitations */}
      <section className="card mb-3 px-5 py-4">
        <h2 className="card-title mb-3">Ограничения и честные оговорки</h2>
        <p className="mb-3 text-sm text-dim">
          Мы хотим, чтобы вы доверяли инструменту ровно настолько, насколько он того заслуживает.
          Поэтому прямо:
        </p>
        <ul className="space-y-3 text-sm text-dim">
          <li>
            <span className="font-semibold text-white">
              Тон СМИ — это не мнение народа.
            </span>{" "}
            Пресса страны может быть жёстче или мягче, чем настроения её жителей. Мы измеряем
            медиаполе, а не опросы общественного мнения.
          </li>
          <li>
            <span className="font-semibold text-white">
              Покрытие неравномерно.
            </span>{" "}
            По 10 странам СНГ мы читаем местные СМИ напрямую и глубоко; по остальным 89 опираемся
            на GDELT — это надёжный, но более грубый сигнал. Для малых стран с небольшим
            медиаполем дневные колебания могут быть шумом.
          </li>
          <li>
            <span className="font-semibold text-white">AI ошибается.</span> Тональность статей
            оценивает языковая модель. На больших объёмах ошибки усредняются, но отдельная статья
            может быть распознана неверно.
          </li>
          <li>
            <span className="font-semibold text-white">
              Веса — экспертные, а не богом данные.
            </span>{" "}
            Сколько «стоит» членство в ОДКБ или санкции — наше методологическое решение. Мы
            держим все веса открытыми, документируем спорные позиции реестра (статус Армении в
            ОДКБ, страны-партнёры БРИКС) и пересматриваем их; история изменений — в git.
          </li>
          <li>
            <span className="font-semibold text-white">
              Индекс — термометр, а не диагноз.
            </span>{" "}
            Он показывает температуру отношений, но не объясняет всю их глубину. Для понимания
            контекста смотрите досье страны: темы, события, сигналы и AI-брифинг.
          </li>
        </ul>
      </section>

      {/* Section: Open source & contacts */}
      <section className="card mb-3 px-5 py-4">
        <h2 className="card-title mb-3">Открытый код и контакты</h2>
        <p className="mb-3 text-sm text-dim">
          GEO PULSE — открытый проект под лицензией{" "}
          <span className="font-semibold text-white">AGPL-3.0</span>. Весь код, веса индекса и
          реестр стран доступны на GitHub — методику можно проверить, оспорить или улучшить:
        </p>
        <ul className="mb-3 space-y-2 text-sm text-dim">
          <li>
            <span className="font-semibold text-white">GitHub:</span>{" "}
            <a
              href="https://github.com/milkmike/GEO_PULSE"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              github.com/milkmike/GEO_PULSE
            </a>
          </li>
          <li>
            <span className="font-semibold text-white">Почта:</span>{" "}
            <a href="mailto:mishtkachenk@gmail.com" className="text-accent hover:underline">
              mishtkachenk@gmail.com
            </a>
          </li>
          <li>
            Часть методологий адаптирована из открытого проекта{" "}
            <a
              href="https://github.com/koala73/worldmonitor"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              World Monitor
            </a>{" "}
            (Elie Habib, AGPL-3.0) — спасибо автору.
          </li>
        </ul>
        <p className="mb-4 text-sm text-dim">
          Нашли ошибку в данных или несогласны с весом фактора? Напишите или откройте issue —
          спор о методике для нас ценнее согласия по инерции.
        </p>
        <p className="text-sm italic text-dim">
          GEO PULSE измеряет, как мир говорит о России, — чтобы мы научились лучше слышать мир.
        </p>
      </section>
    </main>
  );
}
