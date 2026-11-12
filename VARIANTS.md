# Два варианта запуска: веб (Streamlit) и локальный ПК (MCP)

Готовые самостоятельные копии лежат в репозитории:

| Папка | Что это | Куда подключать |
| --- | --- | --- |
| `streamlit_web/` | веб-интерфейс поиска и резюме | браузер, Streamlit Community Cloud |
| `local_pc/` | MCP-сервер по stdio, 9 инструментов | Atomic Chat, Cursor, Claude Desktop, VS Code |

Каждая папка самодостаточна: в ней есть код, `config.yaml`, точные
`requirements.txt`, скрипты установки/запуска и свой `README.md`. Виртуальное
окружение (`.venv`) создаётся внутри папки при установке. Папки не зависят друг
от друга: можно взять только одну.

Актуальные ZIP-архивы (собираются из белого списка файлов, без `.venv`,
логов, истории и ключей):

```sh
python3 tools/build_variants.py --archives
```

В репозитории: `downloads/streamlit_web.zip`, `downloads/local_pc.zip`.
Эти архивы локальные (в git не коммитятся).

---

## Общее: установите Python 3.11+

- Windows: <https://www.python.org/downloads/> → при установке включите
  **Add python.exe to PATH**. Проверка: `py -3.11 --version`.
- macOS: `brew install python@3.11`, проверка `python3 --version`.
- Debian/Ubuntu: `sudo apt update && sudo apt install -y python3.11 python3.11-venv`,
  проверка `python3.11 --version`.

Нужен интернет: для установки пакетов и для самих поисковых запросов.
LLM, ключи и GPU **не нужны** — резюме строится локально (Sumy LSA).

---

## Вариант 1 — Streamlit (веб)

### Windows PowerShell

```powershell
cd C:\путь\к\streamlit_web
.\setup.ps1
```

Если PowerShell запрещает скрипты:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Запуск:

```powershell
.\start.ps1
Start-Process http://127.0.0.1:8501
```

Другой порт: `.\start.ps1 --server.port 8502`. Останов: `Ctrl+C`.

### Linux / macOS

```sh
cd /путь/к/streamlit_web
sh setup.sh
# если python3 младше 3.11:
# PYTHON=python3.11 sh setup.sh
```

Запуск:

```sh
sh start.sh
# откройте http://127.0.0.1:8501 (останов: Ctrl+C)
```

### Что должно появиться при установке

```
No broken requirements found.
OK: standalone Streamlit UI, default sources, actual EN/RU LSA.
No external API requests were made.
```

Повторная офлайн-проверка без переустановки:
`.venv\Scripts\python.exe check_setup.py` (Windows) /
`.venv/bin/python check_setup.py` (Linux/macOS).

### Публикация (Streamlit Community Cloud)

Streamlit Cloud собирает приложение из **GitHub-репозитория**, а не из ZIP:

```sh
cd /путь/к/streamlit_web
git init
git add .
git commit -m "Streamlit kit"
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/ИМЯ_РЕПОЗИТОРИЯ.git
git push -u origin main
```

Затем <https://share.streamlit.io> → **Create app** → репозиторий, ветка
`main`, **Main file path**: `streamlit_app.py`, Python 3.11 → **Deploy**.
`.streamlit/config.toml` и `requirements.txt` уже в нужных местах.

Полученный адрес `https://….streamlit.app` — это **только веб-страница**, не
MCP-эндпоинт и не ваш компьютер: локальная Ollama там недоступна, LLM в этой
копии выключена (`ui.enable_llm: false`). Ключи в репозиторий не добавляйте.

---

## Вариант 2 — локальный ПК (MCP для Atomic Chat)

### Windows PowerShell

```powershell
cd C:\путь\к\local_pc
.\setup.ps1
.\.venv\Scripts\python.exe tools\mcp_config.py --format atomic --output atomic-chat.json
Get-Content .\atomic-chat.json -Encoding UTF8
```

Если файл уже создан после переноса папки — обновить осознанно:

```powershell
.\.venv\Scripts\python.exe tools\mcp_config.py --format atomic --output atomic-chat.json --force
```

### Linux / macOS

```sh
cd /путь/к/local_pc
sh setup.sh
.venv/bin/python tools/mcp_config.py --format atomic --output atomic-chat.json
cat atomic-chat.json
```

Ожидаемый результат установки:

```
No broken requirements found.
OK: real MCP stdio handshake, 9 tools, 6 sources, actual Russian LSA.
No source API requests were made.
```

Файл `atomic-chat.json` содержит три поля с **вашими** абсолютными путями:

```json
{
  "command": "C:\\путь\\к\\local_pc\\.venv\\Scripts\\python.exe",
  "args": ["C:\\путь\\к\\local_pc\\mcp_server.py"],
  "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
}
```

### Подключение в Atomic Chat

1. **Settings → MCP Servers → Add MCP Server**.
2. Транспорт — **STDIO (Local)**.
3. Вставьте JSON из `atomic-chat.json` (или заполните Command / Arguments
   путями из него; `cwd` не нужен).
4. Сохраните, включите сервер; в чате включите коннектор и инструменты.
5. Первый запрос: «Вызови `list_sources` и покажи список источников» —
   должен быть виден реальный вызов инструмента.

Требуется модель с поддержкой вызова инструментов (tool calling), например
Qwen3-8B Q4_K_M, если позволяет железо (на карточке модели минимум 8 ГБ
памяти; фактическую посильность определяет только ваш ПК).

Девять инструментов: `list_sources`, `search_publications`,
`search_europepmc`, `search_minzdrav`, `search_openalex`, `search_arxiv`,
`search_archive`, `search_habr`, `summarize_text`.

### Проверки и живые запросы без чата

```powershell
.\.venv\Scripts\python.exe check_setup.py
.\.venv\Scripts\python.exe demo_search.py --source both --rows 3
```

```sh
.venv/bin/python check_setup.py
.venv/bin/python demo_search.py --source both --rows 3
```

`check_setup.py` работает офлайн. `demo_search.py` ходит в реальные источники
(нужен интернет) и печатает JSON с `items`, `errors`, `warnings`, `partial`.

---

## Частые вопросы

- **Нужен ли MCP в варианте Streamlit?** Нет: веб-интерфейс вызывает
  Python-пайплайн напрямую. MCP — отдельный вариант для чат-хостов.
- **Можно ли использовать оба одновременно?** Да, папки независимы.
- **Streamlit-URL как MCP-сервер?** Нет. Это не MCP-эндпоинт; MCP здесь —
  только локальный stdio-процесс. Во вкладке **MCP** веб-версии есть живая
  песочница всех 9 инструментов (тот же сервисный слой и проверки, что у
  stdio-сервера), чтобы преподаватель мог попробовать поведение инструментов
  без установки. Это не протокол MCP и не замена локальному `local_pc`.
- **Почему «ничего не найдено»?** Смотрите `errors`/`warnings`: ошибка
  источника не равна отсутствию публикаций. Для старых КР расширьте даты.
- **Что с Минздравом?** Возвращаются метаданные каталога КР (официальный API
  либо явно помеченное зеркало), не полный текст рекомендаций.
- **Персональные данные пациентов?** Не отправляйте их в чат или публичный
  веб-интерфейс.

---

## Что проверено, а что нет

Проверено в этом репозитории: установка обеих копий в чистые venv из их
собственных `requirements.txt` (Windows-скрипты проверены статически —
PowerShell в среде сборки отсутствует), офлайн-проверки обеих копий
(`check_setup.py`), запуск Streamlit из `streamlit_web` (`HTTP 200`), реальный
MCP stdio-хендшейк `local_pc`, генерация `atomic-chat.json` во всех режимах,
`demo_search.py --help`, 7 регрессионных тестов сборки/архивов.

Не проверено: подключение к вашему Atomic Chat, публичный деплой в Streamlit
Cloud (нужен ваш GitHub-аккаунт), живые ответы источников из этой среды —
исходящие HTTPS-запросы из песочницы к внешним API заблокированы на уровне
сети, поэтому живой поиск нужно проверить на вашем ПК.

Схема агентов и полная документация проекта: `README.md`, `USAGE.md`,
`diagr/agents.html`.
