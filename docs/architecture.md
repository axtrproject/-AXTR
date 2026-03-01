Обновлённое ТЗ с единым скором 0–100, модель сохранена, логика уточнена.

1. Общая архитектура
Режимы

mode = testnet | mainnet

Единое ядро анализа

Разные веса и активные фичи по режимам

Поток данных

Solana RPC / Indexer

DB_1 Raw (грязные данные)

Feature Engine

Scoring Engine (0–100)

DB_2 Verified (проверенные данные)

API / Direct Query

Все компоненты:

async

без shared-state

коммуникация через очереди / message bus

2. Источники данных
Ончейн

Transactions

Instructions

Accounts

Programs (смарт-контракты)

Logs

Block time

Производные (вычисляемые)

Call sequence

Time deltas

Graph relations

Economic flows

3. Базы данных
DB_1 — Raw (временная)

Хранит сырые события, без логики.

Коллекции:

raw_transactions

raw_instructions

raw_accounts

Очистка (TTL):

testnet: 7–14 дней

mainnet: 30–60 дней

после feature extraction помечаются processed=true

DB_2 — Verified (основная)
Коллекция users
{
  "_id": 102341,
  "wallet": "So1aNa...",
  "first_seen": 1700000000,
  "last_seen": 1700009999,

  "mode": "mainnet",

  "features": {
    "timing": {},
    "graph": {},
    "behavior": {},
    "economic": {}
  },

  "scores": {
    "total": 78,
    "timing": 18,
    "graph": 32,
    "behavior": 15,
    "economic": 13
  },

  "confidence": 0.88,

  "labels": ["high-risk", "likely-sybil"],

  "history": [
    { "ts": 1700001000, "total": 61 },
    { "ts": 1700005000, "total": 78 }
  ]
}


_id — внутренний автоинкрементный ID (0 → ∞), не связан с адресом.

4. Feature Engine (логика анализа)
Timing (testnet + mainnet)

Макс вклад:

testnet: 30

mainnet: 20

Фичи:

avg tx interval

std deviation

burst frequency

активность 24/7

Скоринг:

идеально ровные интервалы → +10

повторяющиеся burst-паттерны → +10

24/7 без пауз → +10

Graph (ключевая часть)

Макс вклад:

testnet: 40

mainnet: 30

Фичи:

shared funding source

common signers

contract overlap

cluster size

Скоринг:

cluster size > N → +15

общий funding → +15

общий deployer / signer → +10

Behavior

Макс вклад:

testnet: 30

mainnet: 20

Фичи:

identical instruction order

повтор calldata

стабильные compute units

Скоринг:

sequence similarity > 0.9 → +15

повтор calldata → +10

compute-стабильность → +5

Economic (только mainnet)

Макс вклад: 30

Фичи:

instant profit

zero-loss loops

capital reuse

fee optimization

Скоринг:

risk-free паттерн → +10

одинаковый ROI → +10

идеальные fee → +10

5. Scoring Engine (0–100)
Формула
TOTAL_SCORE = timing + graph + behavior + economic

Веса по режимам

Testnet

Timing — 30

Graph — 40

Behavior — 30

Economic — 0

Mainnet

Timing — 20

Graph — 30

Behavior — 20

Economic — 30

Интерпретация

0–20 — человек

21–40 — подозрительный

41–60 — semi-bot / фермер

61–80 — бот

81–100 — сибил / кластер

6. Очистка данных

Raw → processed после извлечения фич

Async cleaner:

удаляет processed + expired

Без блокировок и стопов пайплайна

7. API (внешний доступ)

REST / FastAPI

GET /wallet/{address}

Возвращает:

scores (0–100)

confidence

labels

краткие причины (top-фичи)

POST /batch

список адресов

асинхронная обработка

GET /explain/{address}

какие фичи дали вклад в скор

8. Не-API доступ (единичные запросы)

CLI / internal service

прямой async-доступ к Verified DB

wallet → _id → модель пользователя

Используется для аналитики, админки, внутренних сервисов.

9. Асинхронность

Python 3.11

asyncio

каждый модуль — отдельный consumer

взаимодействие через async queues

отсутствие прямых вызовов между модулями

10. Итоговая концепция

Testnet → поведенческий анти-фарм

Mainnet → экономический анти-бот

Один кошелёк = динамическая модель

Скор — вероятность, не приговор

Система самоочищающаяся и расширяемая

Это не детектор. Это движок доверия для Solana.