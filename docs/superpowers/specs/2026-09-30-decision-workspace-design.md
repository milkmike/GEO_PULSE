# Рабочее место «Россия и мир»

Пользователь согласовал шесть виджетов и запуск агентов GPT-6 Sol. Экран помогает одному русскоязычному аналитику увидеть изменения, относящиеся к России, гражданам и организациям, затем проверить основания. Это развитие существующего сайта; карта, поиск, радар и досье сохраняются ниже рабочего места.

## Поведение

На главной над картой: «Где требуется внимание сегодня» — список стран с новыми относящимися к ним публикациями, затем выбранная страна (URL `?country=RS`). Далее «Страна за 60 секунд», «Кто какую позицию занимает», «Что меняется для российских граждан и организаций», «Темы для разговора», «Насколько полна картина». Выбор страны доступен независимо от наличия новых сообщений. Срез 24 часа / 7 суток по дате публикации; отдельно видны дата сбора и обработки. Статус источника — сообщение СМИ, не подтверждённый факт или действующая норма.

Утверждения извлекаются из одной публикации со ссылкой и точной цитатой. Обязательно явное основание связи с Россией и отдельно со страной. География издателя — только покрытие, не участник события. Позиции акторов и практические изменения не выводятся из тональности или action_level. Не создавать прогнозы, рекомендации убеждения, согласие сторон или независимое подтверждение. Вопросы к проверке допустимы как вопросы. Нет доказательств — явное пустое состояние. Перепечатки не считаются независимыми подтверждениями.

## Хранилище и извлечение

Новая таблица `article_decision_annotations`: article_id BIGINT PRIMARY KEY FK articles ON DELETE CASCADE; source_title TEXT NOT NULL; source_excerpt TEXT NOT NULL; annotation JSONB NOT NULL; model TEXT NOT NULL; version TEXT NOT NULL; analyzed_at TIMESTAMPTZ DEFAULT now(). Migration 037; тот же DDL в data/init.sql. Исходный excerpt = left(COALESCE(NULLIF(body,''),summary,''),4000). Сохранять только при совпадении текущих title/excerpt; API тоже проверяет совпадение. Исходные тексты никогда не изменяются.

`annotation` имеет строгую форму:
```
{
  "relevant": true,
  "headline_ru": "Заголовок на русском",
  "summary_ru": "Краткое сообщение с сохранением атрибуции и неопределённости",
  "russia_explanation_ru": "Как именно затронуты Россия, граждане или организации",
  "russia_evidence_quote": "точная подстрока title + newline + excerpt",
  "countries": [{"code":"RS","evidence_quote":"точная подстрока"}],
  "kind": "decision|conflict|cooperation|position|incident|other",
  "positions": [{"actor":"Имя или организация","actor_type":"government|business|media|ngo|other","position_ru":"Что заявлено или сделано, по сообщению источника","evidence_quote":"точная подстрока"}],
  "changes": [{"category":"travel|work|education|culture|restrictions|safety|other","change_ru":"Конкретное изменение для граждан или организаций РФ, с сохранением стадии предложения/решения/вступления в силу","evidence_quote":"точная подстрока"}]
}
```
Для relevant=false: пустые страны/позиции/изменения, пустое основание; headline_ru/summary_ru могут быть пустыми. Максимум 4 страны, 2 позиции и 2 изменения. Все цитаты непустые точные подстроки исходного текста; строки ограничены, URL/HTML не принимаются как генерируемый текст. Коды стран проверяются по БД, RU исключается из направлений. Экстрактор называет статью недоверенными данными, требует воздерживаться при отсутствии явной связи. Машинное извлечение остаётся помеченным в интерфейсе.

Фоновый цикл в existing agenda worker под его advisory lock, отдельный try/except. Модель deepseek/deepseek-v4-flash через существующий BudgetedChat, 1000 output tokens, <=32000 input bytes, максимум 4 запроса/цикл, один материал/запрос. Сначала reserve_request в СУЩЕСТВУЮЩЕЙ кампании Jev с лимитом $3, затем HTTP, затем finish_request с фактической стоимостью. Никаких новых бюджетов или повторных попыток того же source hash/version/model, включая timeout. Сбой не отменяет Jev/переводы. Нулевой бюджет/нет ключа отключает извлечение. Публикации до 7 дней и не из будущего, не дубли; кандидаты распределяются по verified publisher countries, приоритет analysis.is_relevant. Реальные расходы осуществляет только root после проверки кода.

## API contract

Read-only `GET /api/v2/decision-workspace?country=RS`. Никаких вызовов моделей из запроса. Допустимы реальные двухбуквенные коды из countries; неизвестные/неправильные — 422. При отсутствии параметра первая страна из attention, иначе RS или первая доступная.

```
{
 as_of: string,
 country: {code:string,name:string,region:string},
 countries: [{code:string,name:string,region:string}],
 attention: [{code:string,name:string,count_24h:number,count_7d:number,reason:string,latest_at:string}],
 brief: {day:Evidence[],week:Evidence[]},
 positions: [{id:string,actor:string,actor_type:string,position_ru:string,evidence_quote:string,evidence:Evidence}],
 changes: [{id:string,category:string,change_ru:string,evidence_quote:string,evidence:Evidence}],
 topics: [{id:string,title:string,question:string,evidence:Evidence[]}],
 coverage: {
   collected_from_country_7d:number, reviewed_from_country_7d:number,
   relevant_to_country_7d:number, publisher_families:number, local_publisher_families:number,
   last_collected_at:string|null,last_published_at:string|null,last_analyzed_at:string|null,
   independent_confirmation:'not_assessed',truncated:boolean,limitations:string[]
 }
}
```
`Evidence`: `{article_id:number,title_ru:string,title_original:string,url:string|null,publisher_name:string,publisher_country_code:string|null,published_at:string,collected_at:string,russia_explanation_ru:string,russia_evidence_quote:string,country_evidence_quote:string,summary_ru:string,kind:string}`. API использует src public URL sanitizer, не возвращает небезопасную ссылку. Издатель проверяется через article_country_facts, исключая unverified. Содержимое аннотации не должно менять имена издателей/даты/URL.

Запросы ограничены по времени и объёму; лимит 1000 свежих аннотированных публикаций, по 8 day/week, 8 positions/changes, 6 topics, attention до 20 стран. При ограничении выборки `truncated=true` и явное ограничение покрытия. Для counts по текущим публикациям использовать агрегаты, не зависящие от лимита detail, либо явно пометить ограниченную выборку. publisher family = нормализованный domain, не source_id. Нельзя писать «N независимых подтверждений».

## Дизайн и проверка

Сохраняем ink-dark/serif/headings/mono текущего сайта; ясная иерархия и компактные строки, не шесть одинаковых декоративных карточек. Выбранная страна, дата среза, реальные цитаты раскрываются. Нативный select, кнопки/ссылки, focus, 390/768/1440px. Loading/error/retry/empty/partial states. При смене страны старый ответ не перетирает новый. Проверить границы суток/недели, future/old, stale source cache, nonrelevant, иной publisher country, дубли, опасные URL, нет источников, неизвестная страна, переходы клавиатурой. На проде проверить несколько реальных цитат и видимые результаты; не выдавать частичный корпус за всю страну.
