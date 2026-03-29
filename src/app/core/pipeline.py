import asyncio
from typing import List, Optional
from app.core.models import Mode, UserVerified, RawTransaction, HistoryItem
from app.db.mongodb import get_raw_db, get_verified_db
from app.engine.scoring import ScoringEngine
from app.core.ingestion import ingestion_service
import time

import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Pipeline:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.scoring_engine = ScoringEngine()

    async def add_to_queue(self, wallet: str, mode: Mode, program_ids: Optional[List[str]] = None, skip_ingest: bool = False):
        await self.queue.put((wallet, mode, program_ids, skip_ingest))

    async def process_wallet(self, wallet: str, mode: Mode, program_ids: Optional[List[str]] = None, skip_ingest: bool = False):
        raw_db = await get_raw_db()
        verified_db = await get_verified_db()
        
        mode_val = mode.value if hasattr(mode, 'value') else mode

        # 1. Проверка: если кошелек уже есть и изменений нет - скипаем
        existing_user_data = await verified_db.verified_wallets.find_one({"wallet": wallet, "mode": mode_val})
        
        # 0. Ingest data from RPC (Helius) - только если не просили скипнуть
        if not skip_ingest:
            logger.info(f"Ingesting data for {wallet}...")
            await ingestion_service.ingest_wallet(wallet, mode)
        
        # Проверяем, появились ли новые необработанные транзакции
        # Если переданы program_ids, учитываем их при поиске транзакций (через инструкции)
        query = {"wallet": wallet, "mode": mode_val, "processed": False}
        
        new_tx_count = await raw_db.raw_transactions.count_documents(query)
        
        if existing_user_data and new_tx_count == 0:
            logger.info(f"⏩ SKIP {wallet}: Already analyzed and no new transactions found in Rawbd.")
            return

        # 2. Fetch raw data for the wallet
        # Если заданы фильтры по программам, нужно найти транзакции, которые взаимодействовали с этими программами
        if program_ids:
            # Находим сигнатуры транзакций, где участвовали выбранные программы
            # Используем $in для программ и фильтруем по кошельку и режиму
            instr_query = {
                "wallet": wallet, 
                "mode": mode_val, 
                "$or": [
                    {"program_id": {"$in": program_ids}},
                    {"accounts": {"$in": program_ids}}
                ]
            }
            instr_cursor = raw_db.raw_instructions.find(instr_query)
            relevant_sigs = [instr["tx_signature"] for instr in await instr_cursor.to_list(length=5000)]
            
            if relevant_sigs:
                logger.info(f"🔍 Found {len(relevant_sigs)} relevant instructions for {wallet} matching programs.")
                query["signature"] = {"$in": list(set(relevant_sigs))}
            else:
                # ВАЖНО: Если транзакций с целевыми программами не найдено, 
                # мы все равно продолжаем анализ, но с пустым набором релевантных подписей.
                # Это позволит учитывать другие факторы (например, кто спонсировал кошелек).
                logger.info(f"ℹ️ No direct program matches for {wallet}, but proceeding with general analysis.")
                query["wallet"] = wallet

        cursor = raw_db.raw_transactions.find(query)
        transactions_data = await cursor.to_list(length=1000)
        
        if not transactions_data and not existing_user_data:
            logger.warning(f"❌ SKIP {wallet}: No relevant transactions found in blockchain for this wallet and selected programs.")
            return

        transactions = [RawTransaction(**tx) for tx in transactions_data]
        
        if len(transactions) < 5:
            logger.info(f"ℹ️ Wallet {wallet} has very few transactions ({len(transactions)}). Confidence will be lower.")

        # 3. Analyze
        if existing_user_data:
            existing_user_data.pop("_id", None)
            previous_user = UserVerified(**existing_user_data)
        else:
            previous_user = None
        
        analysis_result = await self.scoring_engine.analyze(wallet, transactions, mode, previous_user=previous_user)
        
        # Log alerts if any
        if analysis_result["features"].alerts:
            for alert in analysis_result["features"].alerts:
                logger.warning(f"!!! ALERT for {wallet}: {alert}")

        # 3. Store Result
        # Важно: преобразуем объекты Pydantic в dict для MongoDB, включая список history
        new_history_item = HistoryItem(
            ts=int(time.time()), 
            total=analysis_result["scores"].total,
            timing=analysis_result["scores"].timing,
            graph=analysis_result["scores"].graph,
            behavior=analysis_result["scores"].behavior,
            economic=analysis_result["scores"].economic
        )
        
        history_list = (previous_user.history if previous_user else []) + [new_history_item]
        
        user_data = {
            "wallet": wallet,
            "mode": mode_val,
            "first_seen": previous_user.first_seen if previous_user else int(time.time()),
            "last_seen": int(time.time()),
            "features": analysis_result["features"].model_dump(),
            "scores": analysis_result["scores"].model_dump(),
            "confidence": analysis_result["confidence"],
            "profile": analysis_result.get("profile", "Unknown"),
            "reason_codes": analysis_result.get("reason_codes", []),
            "labels": previous_user.labels if previous_user else [],
            "history": [h.model_dump() if hasattr(h, 'model_dump') else h for h in history_list]
        }
        
        await verified_db.verified_wallets.update_one(
            {"wallet": wallet, "mode": mode_val},
            {"$set": user_data},
            upsert=True
        )

        # Mark processed
        if transactions_data:
            sigs = [tx["signature"] for tx in transactions_data]
            await raw_db.raw_transactions.update_many(
                {"signature": {"$in": sigs}},
                {"$set": {"processed": True}}
            )
        
        logger.info(f"Successfully processed wallet {wallet} (Score: {analysis_result['scores'].total})")

    async def worker(self):
        logger.info("Pipeline worker started")
        while True:
            item = await self.queue.get()
            try:
                if len(item) == 4:
                    wallet, mode, program_ids, skip_ingest = item
                else:
                    wallet, mode, program_ids = item
                    skip_ingest = False
                    
                logger.info(f"Processing wallet: {wallet} ({mode})")
                await self.process_wallet(wallet, mode, program_ids, skip_ingest)
                logger.info(f"Successfully processed wallet: {wallet}")
            except Exception as e:
                logger.error(f"🔴 CRITICAL ERROR analyzing {wallet}: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    async def start_workers(self, num_workers: int = 5):
        self.workers = [asyncio.create_task(self.worker()) for _ in range(num_workers)]

pipeline = Pipeline()
