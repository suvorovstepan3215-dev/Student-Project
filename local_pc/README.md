# Вариант 2 — локальный ПК (MCP + Atomic Chat)

Самостоятельная копия проекта в виде **MCP-сервера по stdio**. Подключается к
Atomic Chat (или Cursor, Claude Desktop, VS Code и любому MCP-хосту). Chat
запускает локальный Python-процесс, видит девять инструментов и вызывает их.

Веб-интерфейс и планировщик в эту копию **не входят** — это вариант
`streamlit_web`. Здесь нужен Python 3.11+ и интернет только на время установки
и самих поисковых запросов.

> Это учебный проект. Ответы — справка по публикациям, не медицинская
> рекомендация. Карточка каталога КР — только метаданные, не полный текст КР.
> Модель формирует ответ, но поиск выполняют детерминированные Python-агенты.

## 0. Что понадобится

- Python 3.11+ (проверьте `py -3.11 --version` / `python3 --version`).
- Atomic Chat с моделью, поддерживающей вызов инструментов (tool calling),
  например Qwen3-8B Q4_K_M — если позволяет железо (на карточке модели
  указан минимум 8 ГБ памяти; реальную посильность проверяет только ваш ПК).
- Путь к папке без экзотики: **без кириллицы в пути** будет спокойнее для
  некоторых MCP-хостов, но код рассчитан и на кириллицу.

## 1. Установка (один раз)

Windows PowerShell, в папке `local_pc`:

```powershell
cd путь\к\local_pc
.\setup.ps1
```

Linux/macOS:

```sh
cd путь/к/local_pc
sh setup.sh
# при необходимости: PYTHON=python3.11 sh setup.sh
```

`setup` создаёт `.venv` рядом с `mcp_server.py`, ставит точные версии из
`requirements.txt` и запускает реальную MCP-проверку по stdio без внешних
API. Финал: `OK: real MCP stdio handshake, 9 tools, 6 sources, actual Russian LSA.`

## 2. Сгенерировать настройки для Atomic Chat

Абсолютные пути должны быть **вашими**, поэтому конфигурацию генерирует ваш
Python:

Windows:

```powershell
.\.venv\Scripts\python.exe tools\mcp_config.py --format atomic --output atomic-chat.json
Get-Content .\atomic-chat.json -Encoding UTF8
```

Linux/macOS:

```sh
.venv/bin/python tools/mcp_config.py --format atomic --output atomic-chat.json
cat atomic-chat.json
```

Файл содержит ровно три поля: `command` (путь к Python из `.venv`), `args`
(один путь к `mcp_server.py`) и `env` (`PYTHONUTF8`, `PYTHONIOENCODING`).
`cwd` в формате Atomic не нужен: сервер сам находит свои файлы.

Если файл уже есть (например, вы переместили папку), генератор откажется его
перезаписывать. Осознанно обновите:

```powershell
.\.venv\Scripts\python.exe tools\mcp_config.py --format atomic --output atomic-chat.json --force
```

```sh
.venv/bin/python tools/mcp_config.py --format atomic --output atomic-chat.json --force
```

Формат с обёрткой `mcpServers` (для других MCP-хостов):

```powershell
.\.venv\Scripts\python.exe tools\mcp_config.py
```

```sh
.venv/bin/python tools/mcp_config.py
```

## 3. Подключение в Atomic Chat

1. Atomic Chat → **Settings → MCP Servers → Add MCP Server**.
2. Тип транспорта **STDIO (Local)**.
3. Вставьте JSON из `atomic-chat.json` (или заполните Command/Arguments вручную
   путями из этого файла).
4. Сохраните и включите сервер; в чате включите коннектор и нужные инструменты.
5. Начните с запроса: **«Вызови list_sources и покажи список источников»**.
   Должен быть виден реальный вызов инструмента, а не просто текст ответа.

Если сервер не появился:

- проверьте, что `command` — это именно `.venv\Scripts\python.exe` из папки
  `local_pc`, а `args` — полный путь к `mcp_server.py`;
- проверьте, что в конфиге нет `cwd` и посторонних полей;
- перезапустите Atomic Chat после правки конфигурации;
- прочитайте сообщение об ошибке запуска сервера в самом Atomic Chat.

## 4. Ручные проверки (без чата)

Реальная MCP-проверка (без внешних запросов):

```powershell
.\.venv\Scripts\python.exe check_setup.py
```

```sh
.venv/bin/python check_setup.py
```

Живой запрос через MCP (нужен интернет; это не LLM):

```powershell
.\.venv\Scripts\python.exe demo_search.py --source both --rows 3
```

```sh
.venv/bin/python demo_search.py --source both --rows 3
```

Другие примеры:

```sh
python demo_search.py --source europepmc --topic diabetes --rows 5
.venv/bin/python demo_search.py --source minzdrav --date-from 2024-01-01 --rows 5
.venv/bin/python demo_search.py --source both --topic diabetes --topic "сахарный диабет" --date-from 2020-01-01
```

Вывод — JSON: `items`, `errors`, `warnings`, `partial`, `source_statuses`.
Код возврата 1, только если все источники упали и записей нет.

## 5. Тестовые запросы в Atomic Chat

1. `Вызови list_sources и покажи каталог источников.`
2. `Найди в Europe PMC публикации по diabetes за 2024-01-01..2024-12-31, 3 записи.
   Покажи заголовки, даты и ссылки.`
3. `Найди карточки КР Минздрава по «сахарный диабет» за 2023-01-01..сегодня,
   только метаданные и provenance.`
4. `Сделай summarize_text для этого текста: ... (2 предложения).`
5. `Сравни найденное и явно укажи, где были errors/warnings/partial.`

## 6. Границы, о которых стоит помнить

- Модель выбирает и вызывает tools сама; интернет ей нужен и при локальном
  инференсе — запросы к Europe PMC и Минздраву делают Python-агенты.
- Поиск MCP read-only: история не пишется, письма не отправляются,
  планировщик не запускается.
- `summarize_text` — локальное экстрактивное резюме Sumy LSA, не генерация LLM.
- Минздрав: официальный API и стороннее зеркало; зеркало явно помечается.
  Полный текст КР не скачивается и клиническая актуальность не проверяется.
- URL Streamlit (другого варианта) **не является** MCP-эндпоинтом. MCP здесь —
  только локальный stdio-процесс.
- Не отправляйте в чат персональные данные пациентов.

## 7. Что проверено и что нет

- Проверено офлайн: установка зависимостей, реальный stdio-хендшейк,
  9 инструментов, 6 источников, русский LSA, запуск `demo_search.py` с
  аргументами парсера.
- Не проверялось здесь: подключение к вашему Atomic Chat и живой поиск из этой
  копии (зависят от вашей сети и версии Atomic Chat).
