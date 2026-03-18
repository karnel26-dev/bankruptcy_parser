# Bankruptcy Parser

Парсер данных о банкротстве физических лиц.
Работает с двумя источниками параллельно: **fedresurs.ru** и **kad.arbitr.ru**.

---

## Что делает

1. Читает список ИНН из `.xlsx`-файла
2. По каждому ИНН находит персону и дела о банкротстве на **fedresurs.ru**
3. Как только найдено дело — сразу запускает обработку на **kad.arbitr.ru**:
   - находит дело в картотеке арбитражных дел
   - скачивает последний документ (PDF)
4. Сохраняет всё в PostgreSQL

---

## Стек

| Компонент | Технология | Обоснование |
|-----------|-----------|-------------|
| Язык | Python 3.11 | Требование ТЗ |
| HTTP | `aiohttp` | Async I/O — оба парсера работают параллельно в одном event loop |
| Параллелизм | `asyncio` + `asyncio.Queue` | Нет threading/greenlet проблем; fedresurs и KAD координируются через очередь |
| ORM | SQLAlchemy 2.0 async | Требование ТЗ; `AsyncSession` + `asyncpg` driver |
| БД | PostgreSQL 16 | Требование ТЗ |
| Контейнеризация | Docker + Docker Compose | Требование ТЗ |
| Чтение xlsx | `openpyxl` | Без зависимостей от LibreOffice |
| HTML-парсинг | `beautifulsoup4` | Парсинг ответа `/Kad/SearchInstances` |
| Конфигурация | `python-dotenv` | 12-factor app |

---

## Архитектура

```
asyncio event loop
│
├── FedresursClient (aiohttp)          KadClient (aiohttp)
│   Semaphore(CONCURRENCY)             asyncio.Queue consumer
│                                      (последовательно)
│   ИНН → /backend/persons/fast   ──►  case_number → /Kad/SearchInstances
│       → /backend/persons/{guid}/     → /Kad/CaseDocumentsPage
│         bankruptcy                   → /Document/Pdf/...?isAddStamp=True
│   сохранить в БД                       JS-челлендж: hash = MD5(token+salto)
│   put(case_number) ───────────►      POST с token+hash → PDF
│                                      сохранить в БД
└── AsyncSession (asyncpg)
```

Как только fedresurs находит первое дело — KAD сразу начинает его обрабатывать, не дожидаясь остальных ИНН. `asyncio.Queue(maxsize=50)` создаёт backpressure.

---

## Структура проекта

```
bankruptcy_parser/
├── app/
│   ├── config.py                   # Все настройки из env-переменных
│   ├── db/
│   │   ├── models.py               # SQLAlchemy-модели
│   │   ├── repository.py           # Все операции с БД
│   │   └── session.py              # Async engine, get_session()
│   ├── parser/
│   │   ├── fedresurs_client.py     # Async HTTP-клиент fedresurs.ru
│   │   ├── kad_client.py           # Async HTTP-клиент kad.arbitr.ru
│   │   ├── kad_wasm.py             # Вычисление hash для PDF (MD5)
│   │   └── worker.py               # Координация двух парсеров
│   └── utils/
│       ├── logger.py               # Логирование в файл + stdout
│       ├── proxy.py                # Заглушка для прокси
│       └── xlsx_reader.py          # Чтение ИНН из xlsx
├── data/                           # Сюда кладём .xlsx файлы
├── logs/                           # Логи (появляется после запуска)
├── main.py                         # Точка входа (asyncio.run)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt                # Для Docker (pip)
├── pyproject.toml                  # Для локальной разработки (poetry)
├── .env
└── .env.example
```

---

## Схема базы данных

```
persons
  id            BIGSERIAL PK
  inn           VARCHAR(20) UNIQUE
  guid          VARCHAR(64)             — GUID на fedresurs.ru
  full_name     VARCHAR(512)
  created_at    TIMESTAMPTZ
  updated_at    TIMESTAMPTZ

legal_cases                             — дела с fedresurs.ru
  id                 BIGSERIAL PK
  person_id          FK → persons.id
  case_guid          VARCHAR(64)
  case_number        VARCHAR(64)        — напр. А32-28873/2024
  status_code        VARCHAR(128)
  status_name        VARCHAR(256)
  last_publish_date  TIMESTAMP
  last_publish_type  VARCHAR(256)
  raw_json           TEXT
  parsed_at          TIMESTAMPTZ
  updated_at         TIMESTAMPTZ
  UNIQUE (person_id, case_number)

case_documents                          — документы с kad.arbitr.ru
  id              BIGSERIAL PK
  legal_case_id   FK → legal_cases.id
  kad_case_id     VARCHAR(64)           — UUID дела в картотеке
  document_id     VARCHAR(64)           — UUID документа
  display_date    VARCHAR(32)           — напр. 19.11.2025
  document_date   TIMESTAMP
  file_name       VARCHAR(512)
  document_type   VARCHAR(256)
  content_types   TEXT
  download_url    TEXT
  pdf_content     BYTEA                 — сам PDF
  pdf_size        INTEGER
  is_downloaded   BOOLEAN
  parsed_at       TIMESTAMPTZ
  UNIQUE (legal_case_id, document_id)

parse_jobs                              — статус обработки ИНН (resume)
  id             BIGSERIAL PK
  inn            VARCHAR(20) UNIQUE
  status         VARCHAR(32)           — pending | done | not_found | error
  error_message  TEXT
  attempts       INTEGER
  created_at     TIMESTAMPTZ
  updated_at     TIMESTAMPTZ

kad_jobs                                — статус обработки дел KAD (resume)
  id             BIGSERIAL PK
  case_number    VARCHAR(64) UNIQUE
  status         VARCHAR(32)           — pending | done | not_found | error
  error_message  TEXT
  attempts       INTEGER
  created_at     TIMESTAMPTZ
  updated_at     TIMESTAMPTZ
```

---

## Установка и запуск

### Через Docker (рекомендуется)

**1. Клонируй репозиторий**
```bash
git clone <repo_url>
cd bankruptcy_parser
```

**2. Создай `.env`**
```bash
cp .env.example .env
```

**3. Положи файл с ИНН в папку `data/`**
```bash
cp /path/to/your/file.xlsx data/inn_list.xlsx
```

**4. Собери и запусти**
```bash
docker compose up --build
```

**С указанием конкретного файла:**
```bash
INPUT_FILE=/data/clients_march.xlsx docker compose up --build
```

**Повторный запуск (resume):**

Оба парсера поддерживают resume независимо. При перезапуске уже обработанные ИНН и дела пропускаются автоматически.

```bash
docker compose up
```

---

### Локально (без Docker)

```bash
poetry install
cp .env.example .env
# Отредактируй DATABASE_URL на локальный PostgreSQL
poetry run python main.py --input /path/to/inn_list.xlsx
```

---

## Как работает передача файла

```dockerfile
ENTRYPOINT ["python", "main.py"]               # фиксированная часть
CMD        ["--input", "/data/inn_list.xlsx"]  # аргументы по умолчанию
```

`command` в `docker-compose.yml` переопределяет только `CMD`:

| Команда | Выполняется в контейнере |
|---------|--------------------------|
| `docker compose up` | `python main.py --input /data/inn_list.xlsx` |
| `INPUT_FILE=/data/other.xlsx docker compose up` | `python main.py --input /data/other.xlsx` |
| `docker compose run --rm parser --input /data/other.xlsx` | `python main.py --input /data/other.xlsx` |

---

## Формат входного файла

`.xlsx`, первая строка — заголовок, ИНН в столбце **A**:

| ИНН |
|-----|
| 231138771115 |
| 771234567890 |

---

## Конфигурация

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://parser:parser@db:5432/bankruptcy` | Строка подключения |
| `CONCURRENCY` | `3` | Параллельных coroutine для fedresurs |
| `DELAY_BETWEEN` | `2.0` | Задержка между запросами (сек) |
| `REQUEST_TIMEOUT` | `30` | Таймаут HTTP-запроса (сек) |
| `MAX_RETRIES` | `3` | Максимум повторных попыток |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `LOG_FILE` | `/logs/parser.log` | Путь к файлу логов |

---

## Просмотр результатов

**Логи:**
```bash
docker compose logs -f parser
cat logs/parser.log
```

**Подключиться к БД:**
```bash
docker compose exec db psql -U parser -d bankruptcy
```

**Полезные SQL-запросы:**
```sql
-- Статус обработки ИНН
SELECT inn, status, attempts, error_message FROM parse_jobs;

-- Статус обработки дел KAD
SELECT case_number, status, attempts, error_message FROM kad_jobs;

-- Дела с документами
SELECT
    p.inn,
    p.full_name,
    lc.case_number,
    lc.status_name,
    cd.display_date,
    cd.file_name,
    cd.is_downloaded,
    cd.pdf_size
FROM case_documents cd
JOIN legal_cases lc ON lc.id = cd.legal_case_id
JOIN persons p ON p.id = lc.person_id
ORDER BY cd.document_date DESC;
```

**Через DBeaver / DataGrip:**
```
Host:     localhost
Port:     5432
Database: bankruptcy
User:     parser
Password: parser
```

---

## Обработка ошибок

| Ситуация | Поведение |
|----------|-----------|
| ИНН не найден в реестре | Статус `not_found`, обработка продолжается |
| Дел о банкротстве нет | Статус `not_found`, Person сохраняется |
| Дело не найдено в KAD | Статус `not_found` в `kad_jobs` |
| Сетевая ошибка / таймаут | Retry с экспоненциальной задержкой до `MAX_RETRIES` |
| PDF не скачан | `is_downloaded=false`, повторный запуск дозагрузит |
| Любое исключение | Логируется, задача помечается `error`, очередь продолжается |

---

## Прокси

Заглушка в `app/utils/proxy.py`. Для активации добавь список в `PROXY_LIST`:

```python
PROXY_LIST = [
    "http://user:pass@proxy1.example.com:8080",
]
```

---

## Как работает скачивание PDF с kad.arbitr.ru

Сайт защищает PDF-документы JS-челленджем:

1. `GET /Document/Pdf/{caseId}/{docId}/{fileName}?isAddStamp=True` → HTML с полями `token` и `salto`
2. `hash = MD5(token + salto)` — вычисляется в Python через `hashlib`
3. `POST` на тот же URL с телом `token={token}&hash={hash}` → PDF

Никакого браузера не требуется — алгоритм восстановлен из JS-кода страницы.
