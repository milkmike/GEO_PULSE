import type { ReactNode } from "react";
import type { TemperatureMethodology } from "@/lib/types";

const FIELD_LABELS: Record<string, string> = {
  window_days: "Окно, дней",
  kind: "Вид затухания",
  tau_seconds: "Параметр затухания, секунд",
  formula: "Формула",
  field: "Поле веса источника",
  default: "Вес по умолчанию",
  cluster_order: "Порядок кластеризации",
  base: "Базовый коэффициент",
  key: "Ключ события",
  minimum_raw_key_length: "Минимальная длина исходного ключа",
  normalization_after_gate: "Нормализация после проверки",
  unclustered_weight: "Вес без кластера",
  numerator: "Числитель",
  denominator: "Знаменатель",
  raw_sentiment: "Исходная тональность",
  factor: "Коэффициент нормализации",
  temperature_round_digits: "Знаков округления температуры",
  raw_sentiment_round_digits: "Знаков округления тональности",
  component_round_digits: "Знаков округления компонентов",
  minimum_points: "Минимум точек",
  z_threshold: "Порог z-оценки",
  provider: "Система анализа",
  requires_translation: "Нужен перевод",
  has_sentiment: "Есть оценка тональности",
  ignored_reposts: "Игнорировать репосты",
  sentiment: "Тональность",
  source_weight: "Вес источника",
  event_type: "Тип события",
  action_level: "Уровень действия",
  age_seconds: "Возраст публикации, секунд",
  reprint_count: "Число перепечаток",
  duplicate_index: "Порядок дубля в кластере",
  weighted_numerator: "Взвешенный числитель",
  weighted_denominator: "Взвешенный знаменатель",
  temperature: "Итоговая температура",
  source_country_match: "Страна источника совпадает",
  analysis_is_relevant: "Анализ подтвердил релевантность России",
  sentiment_required: "Тональность определена",
  backfill_excluded: "Архивная дозагрузка исключена",
  published_within_window: "Публикация входит во временное окно",
  history_points: "Число недавних измерений",
  minimum_samples: "Минимум измерений",
  threshold: "Порог тренда",
  rising_operator: "Условие роста",
  falling_operator: "Условие снижения",
  method: "Метод",
  warning_threshold: "Порог предупреждения",
  critical_threshold: "Критический порог",
  threshold_operator: "Условие порога",
  round_digits: "Знаков округления",
  zero_std_fallback: "Значение при нулевом отклонении",
  model_field: "Поле модели",
  prompt_version_field: "Поле версии промпта",
  provenance_is_per_article: "Происхождение хранится для каждой статьи",
  runtime_defined: "Значение определяется при выполнении",
  military: "Военное событие",
  diplomatic: "Дипломатическое событие",
  security: "Безопасность",
  economic: "Экономическое событие",
  cultural: "Культурное событие",
  unspecified: "Тип не определён",
};

const LIMITATION_LABELS: Record<string, string> = {
  coverage_is_limited_to_collected_and_successfully_analyzed_articles:
    "Учитываются только собранные и успешно проанализированные публикации.",
  source_weight_and_source_availability_can_bias_the_result:
    "Доступность источников и их веса могут смещать результат.",
  sentiment_and_event_classification_can_be_incorrect:
    "Классификация тональности и типа события может ошибаться.",
  repeated_event_detection_depends_on_event_key_quality:
    "Распознавание повторов зависит от качества ключа события.",
  temperature_is_not_the_broader_rri_and_does_not_measure_causation:
    "Температура — не общий индекс RRI и не доказательство причинности.",
};

const SECTION_LABELS: Record<string, string> = {
  input_eligibility: "Допуск публикаций",
  window: "Временное окно",
  time_decay: "Затухание со временем",
  source_weights: "Вес источника",
  event_type_weights: "Вес типа события",
  action_level_weights: "Вес уровня действия",
  reprint_importance: "Влияние перепечаток",
  event_clustering: "Кластеризация событий",
  cluster_diminishing: "Понижение веса дублей",
  aggregation: "Агрегация",
  normalization: "Нормализация",
  trend: "Тренд",
  anomaly: "Аномалия",
  upstream_analysis: "Предварительный анализ",
};

function readableKey(key: string): string {
  if (/^\d+$/.test(key)) return `Уровень действия ${key}`;
  return FIELD_LABELS[key] ?? key.replaceAll("_", " ");
}

function displayValue(value: unknown): ReactNode {
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "number") return String(value).replace("-", "−").replace(".", ",");
  if (value == null || value === "") return "не задано";
  return String(value);
}

function ValueRows({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="divide-y divide-line text-sm">
      {Object.entries(values).map(([key, value]) => (
        <div key={key} className="grid gap-1 py-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] sm:gap-5">
          <dt className="text-dim">{readableKey(key)}</dt>
          <dd className="tnum break-words text-ru-white">{displayValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function TechnicalGroup({ id, children }: { id: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel2 p-4">
      <h4 className="card-title mb-2">{SECTION_LABELS[id] ?? readableKey(id)}</h4>
      {children}
    </section>
  );
}

export default function TemperatureMethodologyBlock({ methodology }: { methodology: TemperatureMethodology }) {
  const { technical } = methodology;
  const technicalGroups: Array<[string, Record<string, unknown>]> = [
    ["input_eligibility", technical.input_eligibility],
    ["window", { window_days: technical.window_days }],
    ["time_decay", technical.time_decay],
    ["source_weights", technical.source_weights],
    ["event_type_weights", technical.event_type_weights],
    ["action_level_weights", technical.action_level_weights],
    ["reprint_importance", technical.reprint_importance],
    ["event_clustering", technical.event_clustering],
    ["cluster_diminishing", technical.cluster_diminishing],
    ["aggregation", technical.aggregation],
    ["normalization", technical.normalization],
    ["trend", technical.trend],
    ["anomaly", technical.anomaly],
    ["upstream_analysis", technical.upstream_analysis],
  ];

  return (
    <section className="prose-editorial" aria-labelledby="temperature-methodology-title">
      <header className="mb-6 border-l-2 border-ru-red pl-5">
        <h3 id="temperature-methodology-title" className="display text-[24px] leading-tight">{methodology.name}</h3>
        <p className="tnum mt-2 text-[11px] uppercase tracking-wide text-dim">
          версия методики {methodology.methodology_version}
        </p>
      </header>

      <div className="space-y-5">
        {methodology.plain_language.map((item) => (
          <section key={item.id} aria-labelledby={`temperature-plain-${item.id}`}>
            <h4 id={`temperature-plain-${item.id}`} className="display mb-1 text-[17px]">{item.title}</h4>
            <p className="text-[14px] leading-relaxed text-dim">{item.body}</p>
          </section>
        ))}
      </div>

      <details className="group mt-7 rounded-lg border border-line bg-panel">
        <summary className="min-h-11 cursor-pointer px-5 py-4 text-sm font-semibold text-ru-white marker:text-ru-blue focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          Технические критерии
        </summary>
        <div role="region" aria-label="Технические критерии" className="grid gap-3 border-t border-line p-4 md:grid-cols-2">
          {technicalGroups.map(([id, values]) => (
            <TechnicalGroup key={id} id={id}><ValueRows values={values} /></TechnicalGroup>
          ))}
        </div>
      </details>

      <details className="group mt-3 rounded-lg border border-line bg-panel">
        <summary className="min-h-11 cursor-pointer px-5 py-4 text-sm font-semibold text-ru-white marker:text-ru-blue focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          Пример расчёта
        </summary>
        <div role="region" aria-label="Пример расчёта" className="space-y-4 border-t border-line p-4">
          {methodology.worked_example.articles.map((article, index) => (
            <section key={index} className="rounded-lg border border-line bg-panel2 p-4">
              <h4 className="card-title mb-2">Публикация {index + 1}</h4>
              <ValueRows values={article} />
            </section>
          ))}
          <section className="rounded-lg border border-ru-blue/40 bg-ru-blue/5 p-4">
            <h4 className="card-title mb-2">Результат</h4>
            <ValueRows values={{
              weighted_numerator: methodology.worked_example.weighted_numerator,
              weighted_denominator: methodology.worked_example.weighted_denominator,
              temperature: methodology.worked_example.temperature,
            }} />
          </section>
        </div>
      </details>

      <section role="region" aria-label="Ограничения температуры" className="mt-7 border-t border-line pt-5">
        <h4 className="card-title">Ограничения температуры</h4>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-[14px] leading-relaxed text-dim">
          {methodology.limitations.map((limitation) => (
            <li key={limitation}>{LIMITATION_LABELS[limitation] ?? limitation}</li>
          ))}
        </ul>
      </section>
    </section>
  );
}
