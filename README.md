# История кошелька Polymarket (Polygon RPC → PostgreSQL)

Скрипт **сам** восстанавливает on-chain историю кошелька Polymarket на сети Polygon.

Не используются API Polymarket и чужие индексаторы. Источник правды — логи контрактов через `eth_getLogs`. Текущие балансы считаются из переводов и сверяются с блокчейном через `balanceOf` / `balanceOfBatch`.

По умолчанию берётся кошелёк `0x46b353667fd7d846af3bbeda6584b0e5b883d3de` (это proxy, не обычный EOA). Можно указать любой другой адрес.

## Что считается

| Токен | Контракт | События |
|-------|----------|---------|
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | ERC-20 `Transfer` |
| pUSD | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | ERC-20 `Transfer` |
| Позиции рынков (CTF) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | ERC-1155 `TransferSingle` / `TransferBatch` |

Баланс позиции = сумма входящих минус сумма исходящих. Переводы «сам себе» не меняют сумму.

Сверка идёт **на блоке, до которого доиндексировали**, а не на «latest». Иначе кошелёк успеет сделать новые сделки, пока идёт проверка.

## Что нужно на машине

- Python 3.11+ (проверялось на 3.14)
- PostgreSQL 14+ **или** Docker
- Доступ в интернет к Polygon RPC

Полное совпадение балансов с цепью возможно только если RPC отдаёт **старые** `eth_getLogs` (archive / не обрезанная история). Бесплатные ноды вроде PublicNode обычно хранят только последние ~80 тысяч блоков — на них история обрежется, и часть балансов не сойдётся.

Рабочий публичный вариант с архивом: `https://polygon.gateway.tenderly.co`. Надёжнее — свой ключ Alchemy / QuickNode.

## 1. Клонировать и поставить Python-зависимости

В PowerShell из корня репозитория:

```powershell
cd polymarket_wallet_indexer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

Linux / macOS:

```bash
cd polymarket_wallet_indexer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

## 2. Поднять PostgreSQL

Проще всего Docker (порт 5432, пользователь/пароль/база `polymarket`):

```powershell
docker compose up -d
```

Строка подключения:

```
postgresql://polymarket:polymarket@127.0.0.1:5432/polymarket
```

Если Docker нет — поставьте PostgreSQL локально, создайте пользователя и базу `polymarket` и пропишите свой `DATABASE_URL`.

Схема таблиц создаётся командой `init-db` (файл `schema.sql`). Повторный запуск безопасен.

## 3. Файл настроек `.env`

Скопируйте пример и поправьте RPC:

```powershell
copy .env.example .env
```

Минимальный рабочий `.env`:

```
POLYGON_RPC_URL=https://polygon.gateway.tenderly.co
DATABASE_URL=postgresql://polymarket:polymarket@127.0.0.1:5432/polymarket
WALLET=0x46b353667fd7d846af3bbeda6584b0e5b883d3de
START_BLOCK=27065000
CHUNK_SIZE=100000
CONFIRMATIONS=5
```

| Переменная | Смысл |
|------------|--------|
| `POLYGON_RPC_URL` | HTTP JSON-RPC Polygon (нужен архив для полной истории) |
| `DATABASE_URL` | Postgres, формат SQLAlchemy/psycopg |
| `WALLET` | Адрес кошелька (0x…) |
| `START_BLOCK` | Нижняя граница скана. Для этого кошелька активность начинается около блока `80813600`; `27065000` — безопасный «с появления CTF» |
| `CHUNK_SIZE` | Сколько блоков в одном `eth_getLogs`. Для Tenderly нормально `100000`. PublicNode часто режет до `10000` |
| `CONFIRMATIONS` | Не индексировать самые свежие N блоков (защита от реоргов) |
| `RPC_TIMEOUT` / `RPC_RETRIES` | Таймаут и число повторов (по умолчанию 60 с / 8) |

## 4. Запуск

Все команды из каталога `polymarket_wallet_indexer` с активированным `.venv`.

Создать таблицы:

```powershell
python -m src init-db
```

Полный цикл: индекс + сверка + краткий отчёт:

```powershell
python -m src run
```

Первый прогон по этому кошельку занимает **десятки минут или часы** (порядка 13 млн блоков от первой активности до головы цепи). Прогресс — в tqdm. Можно прервать Ctrl+C и запустить снова: продолжится с `last_indexed_block`.

По шагам:

```powershell
python -m src index
python -m src verify
python -m src report
```

Полезные флаги:

```powershell
python -m src run --wallet 0x... --start-block 80813600
python -m src run --rpc https://polygon.gateway.tenderly.co
python -m src run --no-discover --skip-history-probe
python -m src report --all-zero
```

- `--no-discover` — не искать первую активность, брать `START_BLOCK` как есть.
- `--skip-history-probe` — не бинарным поиском определять, с какого блока RPC ещё отдаёт логи (нужно, если вы уже знаете, что нода архивная).
- `--all-zero` — в отчёте показать и нулевые позиции.

Эквивалент: `python run.py run`.

## 5. Как понять, что всё сошлось

После `verify` / `run` в консоли:

```
Verify: erc20 N ok / 0 fail; erc1155 N ok / 0 fail
```

Код выхода `2` — есть расхождения. В Postgres:

- `erc20_transfers` / `erc1155_transfers` — все переводы;
- `token_balances` — посчитанный баланс, on-chain баланс, флаг `matched`;
- `sync_state` — до какого блока дошли;
- `verify_runs` — история сверок.

Ненулевые CTF-позиции у активного кошелька — сотни тысяч строк. Отчёт в консоли печатает только первые 40.

## Типичные проблемы

**«History has been pruned» / балансы не сходятся.** RPC обрезал старые логи. Смените URL на archive (Tenderly / Alchemy / QuickNode) и переиндексируйте с первого блока активности.

**«exceed maximum block range».** Уменьшите `CHUNK_SIZE` (например до `10000` или `2000`).

**429 / тормоза.** Бесплатный RPC режет частоту. Подождите и продолжите тот же `index` — курсор уже в базе.

**Не коннектится к Postgres.** Проверьте, что контейнер/служба слушает порт из `DATABASE_URL`, и что база с таким именем существует.

**Сверка «зелёная», а в Polymarket UI другие цифры.** UI считает открытые рынки, кэш и резолвы по-своему. Этот инструмент сверяется только с токенами в контракте CTF / USDC.e / pUSD.

## Структура

```
polymarket_wallet_indexer/
  src/           # CLI и логика
  schema.sql     # таблицы Postgres
  docker-compose.yml
  requirements.txt
  .env.example
```
