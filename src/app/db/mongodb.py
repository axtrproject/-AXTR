import logging
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from typing import Optional

logger = logging.getLogger(__name__)

class MongoDB:
    client: Optional[AsyncIOMotorClient] = None
    db_raw = None
    db_verified = None

    async def connect(self):
        if self.client is not None:
            return

        try:
            # Настройка клиента с таймаутами для стабильности
            self.client = AsyncIOMotorClient(
                settings.MONGODB_URL,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            
            # Проверка соединения (ping)
            await self.client.admin.command('ping')
            
            self.db_raw = self.client[settings.DB_RAW_NAME]
            self.db_verified = self.client[settings.DB_VERIFIED_NAME]
            
            logger.info(f"Successfully connected to MongoDB: {settings.MONGODB_URL}")
            logger.info(f"Databases: Raw={settings.DB_RAW_NAME}, Verified={settings.DB_VERIFIED_NAME}")
            
            # Автоматическое создание индексов для оптимизации
            await self.ensure_indexes()
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            self.client = None
            raise

    async def ensure_indexes(self):
        """
        Создает необходимые индексы для ускорения поиска и обеспечения уникальности данных.
        """
        if self.db_raw is None or self.db_verified is None:
            return

        try:
            # 1. Индексы для Raw Transactions
            # Поиск по кошельку и режиму (основной запрос в pipeline и engines)
            await self.db_raw.raw_transactions.create_index([("wallet", 1), ("mode", 1)])
            indexes = await self.db_raw.raw_transactions.index_information()
            sig_index = indexes.get("signature_1")
            if sig_index is None:
                try:
                    await self.db_raw.raw_transactions.create_index([("signature", 1)], unique=True, name="signature_1")
                except Exception as e:
                    logger.warning(f"Signature index unique create failed, fallback to non-unique: {e}")
                    await self.db_raw.raw_transactions.create_index([("signature", 1)], name="signature_1")
            # Поиск по времени для сортировки (Graph analysis)
            await self.db_raw.raw_transactions.create_index([("block_time", -1)])
            # Для массового обновления статуса обработки
            await self.db_raw.raw_transactions.create_index([("processed", 1)])

            # 2. Индексы для Raw Instructions
            await self.db_raw.raw_instructions.create_index([("wallet", 1), ("mode", 1)])
            await self.db_raw.raw_instructions.create_index([("program_id", 1)])

            # 3. Индексы для Verified Wallets (результаты)
            await self.db_verified.verified_wallets.create_index([("wallet", 1), ("mode", 1)], unique=True)
            # Для гистограмм и фильтрации по риску в глобальном отчете
            await self.db_verified.verified_wallets.create_index([("scores.total", 1)])
            # Для поиска по фундерам (Graph cluster analysis)
            await self.db_verified.verified_wallets.create_index([("features.graph.root_funder", 1)])
            await self.db_verified.api_tokens.create_index([("token", 1)], unique=True)

            logger.info("⚡ MongoDB indexes ensured (Performance Optimization Active)")
        except Exception as e:
            logger.error(f"Failed to ensure MongoDB indexes: {e}")

    async def close(self):
        if self.client:
            self.client.close()
            self.client = None
            self.db_raw = None
            self.db_verified = None
            logger.info("MongoDB connection closed")

db = MongoDB()

async def get_raw_db():
    if db.db_raw is None:
        await db.connect()
    return db.db_raw

async def get_verified_db():
    if db.db_verified is None:
        await db.connect()
    return db.db_verified
