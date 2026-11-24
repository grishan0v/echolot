# Слой `.claude/` и интеграция с агентами

[← Оглавление](README.md) · [README](../../README.ru.md) · [English](../agent-layer.md) · **Русский**

```bash
cd ~/StudioProjects/my-app
echolot init
```

Устанавливает в проект инструкции по работе с инструментом:

```
.claude/
├── skills/echolot/
│   ├── SKILL.md              /echolot — точка входа: читает статус и маршрутизирует
│   └── references/           схемы отчёта, конфига, имён ART и захвата трейса
├── agents/perf-hunter.md     субагент: итеративный цикл поиска
├── commands/echolot-setup.md /echolot-setup — создание конфига
├── commands/echolot-hunt.md  /echolot-hunt — поиск причины регрессии
├── commands/echolot-reflect.md /echolot-reflect — анализ прошедшей сессии
├── settings.json             разрешение на вызов echolot без подтверждений
└── echolot-layer.json        манифест установленных файлов для doctor
```

[`echolot ui`](ui.md#настройка-проекта) запускает `echolot init` автоматически
при нажатии **Set it up** в новом проекте.

`/echolot` — единая точка входа. Он запускает `echolot`, считывает `next`
(`echolot status --next`: `upgrade`, `init`, `doctor`, `setup`,
`fix-config`, `resume-or-new`, `init-force`, `fix-settings`, `hunt`) и
выбирает нужное действие.

Слова после `/echolot` — это просьба, и расследование нужно не для каждой.
ANR, зависание или issue из консоли крэшей ведут в `echolot anr <file>`;
вопрос о трейсах, которые уже на диске, без устройства — в `echolot analyze`
и `echolot report`; замер без регрессии («запиши базовую линию», «просто
замерь») — в `echolot collect` и `echolot analyze`. Эти три пути идут без
`--hunt`, вне любого расследования. Только регрессия, которую надо найти,
ведёт в `echolot-hunt`, и при открытом расследовании человека сначала
спрашивают, продолжить его или начать новое: новое закрывает открытое
(#497).

Шаблон хранится **внутри Python-пакета**, а в проект попадает копия с
манифестом `echolot-layer.json`. При обновлении `echolot` команда
`echolot init` обновляет нетронутые файлы и не перезаписывает файлы,
изменённые вами вручную (для полной перезаписи нужен флаг `--all`, который
скилл никогда не запускает без подтверждения). Если слой записан более
новой версией `echolot`, старая версия отказывается его перезаписывать
(`next = upgrade`). `.claude/settings.json` объединяется аккуратно, без
перезаписи ваших настроек, а в `.gitignore` добавляются `/.echolot/` и
`/local.yml`.

### Приватная установка

Если репозиторий общий или клиентский и вы не хотите коммитить файлы агентов,
используйте `echolot init --private`: все созданные пути прописываются в
`.git/info/exclude` (в том числе в git worktree), разрешения пишутся в
`.claude/settings.local.json`, отслеживаемые файлы вроде `AGENTS.md` не
трогаются, а выбор сохраняется в `.echolot/hosts.json`. Вернуться к общей
установке можно командой `echolot init --shared`.

## Почему CLI, а не MCP-сервер

- Инструменты MCP постоянно занимают контекстное окно, даже когда не
  используются.
- CLI вызывается через bash и ничего не стоит в простое.
- CLI работает в CI без всякой модели.
- Ставится одной командой без настройки конфигов клиента.

Для клиентов без `.claude/` (Cursor, Gemini CLI, Copilot, Codex без плагина)
`echolot init` записывает короткие указатели в `AGENTS.md`, `GEMINI.md`,
`.cursor/rules/echolot.mdc`, `.github/copilot-instructions.md` (а для Codex
ещё и `.codex/rules/echolot.rules`), направляя агента к `echolot guide`:

```bash
echolot guide          # как работать с инструментом
echolot guide setup    # сборка echolot.yml
echolot guide hunt     # цикл поиска
echolot guide anr      # разбор и измерение ANR
```

Выбор агентов сохраняется в `.echolot/hosts.json` (флаг `--for claude,cursor`
или `--for all`). Знания не дублируются по разным файлам — они печатаются
напрямую установленным CLI.

### Плагин: единый набор скиллов для Claude Code и Codex

`plugins/echolot/` — единый плагин с четырьмя скиллами (`echolot`,
`echolot-setup`, `echolot-hunt`, `echolot-reflect`). Чтобы укладываться в
лимит Codex в 4000 байт на скилл и не дублировать инструкции, каждый скилл
остаётся короткой точкой входа и подтягивает детали через `echolot guide <topic>`.

### Установка плагина

```bash
pipx install echolot
```

- **Claude Code:** `/plugin marketplace add grishan0v/echolot`, затем
  `/plugin install echolot@echolot` (или из shell: `claude plugin marketplace
  add grishan0v/echolot` и `claude plugin install echolot@echolot`; требуется
  git 2.36+). Точка входа: `/echolot:echolot`.
- **Codex:** `codex plugin marketplace add grishan0v/echolot`, затем
  `codex plugin add echolot@echolot`. Точка входа: `$echolot`.
- **Cursor:** автоматически подхватывает плагины, включённые в Claude Code;
  без Claude Code скопируйте плагин локально командой
  `echolot plugin cursor` (в `~/.cursor/plugins/local/echolot`) и
  перезапустите Cursor. Также плагин опубликован в каталоге
  [cursor.directory/plugins/echolot](https://cursor.directory/plugins/echolot).

При использовании плагина запускайте `echolot init --for plugin` (или
`--for plugin,codex` в Codex), чтобы не устанавливать дубликат `.claude/`.

### Песочница без сети

По умолчанию песочница Codex блокирует даже `localhost`, который нужен
`trace_processor` и `adb`. Выход — разрешить команду `echolot` вне песочницы, либо
записать правило `.codex/rules/echolot.rules` через `echolot init` (для
доверенного проекта или глобально в `~/.codex/rules/`). В Claude Code при
включённой песочнице добавьте `"echolot *"` в `sandbox.excludedCommands`.

Рядом с правилом для `echolot` в том же файле ещё два: для `./gradlew` и для
`adb`. Расследование пересобирает приложение и ставит его на телефон, а
Gradle нужна сеть и запись в `~/.gradle`, adb — его сервер на localhost. С
ними Codex запускает эти две команды без вопросов в любой сессии этого
проекта, как и `echolot`; [агент со страницы](ui.md#с-codex) даёт запущенному
Codex именно эти правила и ничего больше. Файл, который записал echolot
постарше, только с правилом для `echolot`, узнаётся по тексту: `echolot`,
`status` и `doctor` говорят, что он от более старого echolot, и называют
`init`, который его обновит; `init` обновляет его, раз его никто не правил.

## Почему цикл поиска живёт в субагенте

Внутри цикла накапливается много шума: таблицы, поиски по коду, правки
временных маркеров и несколько итераций перезаписи. В основном контексте
окно забивается за два раунда. Субагент (`perf-hunter`) работает в чистом
окне и возвращает только структурированный итог из восьми полей (`Place`,
`Evidence`, `Mechanism`, `Suggestion`, `Confidence`, `Ruled out`,
`Also measured`, `Cleanup`).

Протокол жёстко ограничен `loop.max_rounds`, временные маркеры разрешены
только внутри `instrumentation.allowed` с префиксом `AGENTTMP_` и удаляются
в конце при любом исходе.

## Расследования и архив

`.echolot/hunt.json` хранит текущее открытое расследование (вопрос, время,
конфиг), чтобы при возврате через неделю `echolot` не путал старые трейсы с
новым вопросом. При запуске нового расследования старое архивируется в
`.echolot/hunts/<n>/` вместе со ссылками на его отчёты `reports/001.json…`
и набором трейсов.

К расследованию относится только тот запуск, который об этом говорит:
`collect --hunt <n>` и `analyze --hunt <n>`, с номером, который `echolot hunt`
напечатал при открытии. Команда hunt и `perf-hunter` передают его в каждом
запуске. Запуск без `--hunt` идёт вне расследования: под него ничего не
записывается, `touched_at` не обновляется, идущий `collect` не заменяет его
работу, а по умолчанию берётся первый сценарий (#498). Раунд расследования,
который такой запуск отодвинул, всё равно записывается под расследование:
это его данные.

Если `hunt.json` есть, но не читается (обрезан, пуст или это JSON, но не
объект), ничего не останавливается. `status`, `hunt` и стартовая страница
`echolot ui` пишут на месте расследования «`.echolot/hunt.json` does not load:
… — the open investigation is unknown», а следующий `echolot hunt "<q>"`
переносит файл в `.echolot/hunts/hunt.json.unreadable-<stamp>` и открывает
новое расследование рядом. Поле, в котором лежит не то, что пишет туда echolot
(`"reports": "abc"`, число в `opened_at`), считается неизвестным, остальное
показывается.

Расследование, запущенное из `echolot ui`
([Агент со страницы](ui.md#агент-со-страницы)), открывает сама страница:
`echolot hunt "<the question>" --since unknown`, с вопросом из отчёта. Затем она
запускает Claude Code с `/echolot-hunt` и запросом, в котором сказано, что
расследование открыто, какой у него номер, в какую папку отложены трейсы
отчёта, что за клавиатурой никого нет и что агенту можно запускать. Всё, что
команда спросила бы у человека, в запросе уже есть, поэтому она продолжает
открытое расследование и ничего не спрашивает. Codex получает тот же запрос, и
в нём сказано, что шаги, которые дала бы `/echolot-hunt`, печатает
`echolot guide hunt`. Расследование закрывает агент, как и в любом другом
случае, вердиктом. Если агент закончил без вердикта,
расследование остаётся открытым, и кнопка страницы **Close the
investigation** закрывает его через `echolot hunt --done` с фразой о том, к
чему оно пришло.

### Закрытие с вердиктом

Помимо текстового `echolot hunt --done "<text>"`, расследование можно закрыть
структурированным JSON-вердиктом:

```bash
echolot hunt --done --verdict .echolot/verdict.json     # файл или - для stdin
echolot hunt --verdict .echolot/verdict.json            # только проверка без закрытия
```

Команда проверяет каждую ссылку в `evidence` и `inside` против реально
зафиксированных отчётов этого расследования (отклоняя выдуманные строки с
кодом 2) и сверяет оставшиеся маркеры в коде с полем `cleanup`.

### Ответ агента в виде страницы

```bash
echolot hunt --show --html                          # .echolot/hunts/<n>/answer.html
echolot hunt --show 2 --html                        # конкретное расследование
echolot hunt --done --verdict verdict.json --html   # закрыть и сразу собрать страницу
```

Генерирует автономный HTML-файл `answer.html` с таблицей причин, графиками
сработавших строк по раундам, кнопкой перепроверки и деталями каждого шага
(то же самое, что показывает вкладка **Agent's answer** в `echolot ui`).

### Из командной строки

```bash
echolot hunt "cold start was 3s, now 7s" --since "the tab redesign"
echolot hunt --list
echolot hunt --show 2
echolot hunt --resume
echolot hunt --done "..."
```

## Интерактивная настройка (`/echolot-setup`)

Вместо ручного заполнения ~25 полей `echolot.yml` агент сначала сканирует
проект (`echolot scan`), снимает пробный трейс, смотрит реальных кандидатов
(`echolot probe`) и задаёт человеку только те несколько вопросов, ответов на
которые нет ни в коде, ни в трейсе, записывая источник подтверждения
(`_source: confirmed_by_user` и `_evidence`).
