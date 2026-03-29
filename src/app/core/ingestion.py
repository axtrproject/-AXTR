import httpx
import asyncio
import logging
from typing import List, Dict, Optional
from app.core.config import settings
from app.core.models import Mode, RawTransaction, RawInstruction
from app.db.mongodb import get_raw_db

logger = logging.getLogger(__name__)

class IngestionService:
    def __init__(self):
        self.headers = {"Content-Type": "application/json"}

    def _get_rpc_url(self, mode: Mode) -> str:
        if mode == Mode.MAINNET:
            return settings.SOLANA_RPC_URL_MAINNET
        elif mode == Mode.TESTNET:
            return settings.SOLANA_RPC_URL_TESTNET
        else:
            return settings.SOLANA_RPC_URL_DEVNET

    async def _post(self, method: str, params: List, mode: Mode, retries: int = 3) -> Optional[Dict]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        rpc_url = self._get_rpc_url(mode)
        
        for attempt in range(retries):
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(rpc_url, json=payload, headers=self.headers)
                    
                    if response.status_code == 429:
                        wait_time = (2 ** attempt) + 1
                        logger.warning(f"Rate limited (429) on {method}. Waiting {wait_time}s... (Attempt {attempt+1}/{retries})")
                        await asyncio.sleep(wait_time)
                        continue
                        
                    response.raise_for_status()
                    data = response.json()
                    
                    if "error" in data:
                        err = data["error"]
                        # Some RPCs return 429 inside the JSON error field
                        if isinstance(err, dict) and (err.get("code") == -32005 or "rate limit" in str(err).lower()):
                            wait_time = (2 ** attempt) + 1
                            logger.warning(f"RPC Rate Limit error on {method}. Waiting {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                            
                        logger.error(f"RPC Error ({method}) on {mode}: {err}")
                        return None
                    return data.get("result")
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait_time = (2 ** attempt) + 1
                        await asyncio.sleep(wait_time)
                        continue
                    logger.error(f"HTTP Status Error ({method}) on {mode}: {e}")
                    return None
                except Exception as e:
                    logger.error(f"HTTP Error ({method}) on {mode}: {e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(1)
                        continue
                    return None
        return None

    async def get_signatures(self, address: str, mode: Mode, limit: int = 100, before: Optional[str] = None) -> List[str]:
        params = [address, {"limit": limit}]
        if before:
            params[1]["before"] = before
            
        result = await self._post("getSignaturesForAddress", params, mode)
        if not result:
            return []
        return [item["signature"] for item in result]

    async def get_active_wallets_for_programs(self, program_ids: List[str], mode: Mode, target_count: int = 1000) -> List[str]:
        """
        Достает уникальные кошельки, которые взаимодействовали с указанными программами.
        Оптимизировано: пагинация по сигнатурам и пакетная обработка.
        """
        logger.info(f"Fetching active wallets for programs: {program_ids} (target: {target_count})")
        unique_wallets = set()
        
        for prog_id in program_ids:
            if len(unique_wallets) >= target_count:
                break
            
            logger.info(f"Scanning program {prog_id} for active wallets...")
            last_sig = None
            consecutive_empty = 0
            
            # Пагинация пока не наберем нужное кол-во или не кончатся транзакции
            while len(unique_wallets) < target_count and consecutive_empty < 3:
                # Берем пачку подписей (макс 1000 за раз)
                signatures = await self.get_signatures(prog_id, mode, limit=1000, before=last_sig)
                
                if not signatures:
                    logger.warning(f"⚠️ No more signatures found for program {prog_id}. Stopping search for this program.")
                    break
                
                last_sig = signatures[-1]
                logger.info(f"[*] Processing batch of {len(signatures)} signatures (last: {last_sig})...")
                
                # Обрабатываем подписи пачками по 25
                batch_size = 25
                found_in_batch_total = 0
                
                for i in range(0, len(signatures), batch_size):
                    if len(unique_wallets) >= target_count:
                        break
                    
                    batch = signatures[i:i+batch_size]
                    tasks = [self.get_transaction(sig, mode) for sig in batch]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    found_in_subbatch = 0
                    for tx_data in results:
                        if isinstance(tx_data, dict) and "transaction" in tx_data:
                            try:
                                message = tx_data["transaction"]["message"]
                                if "accountKeys" in message:
                                    signer = message["accountKeys"][0]
                                    if isinstance(signer, dict):
                                        signer = signer.get("pubkey")
                                    
                                    if signer and signer not in program_ids:
                                        if signer not in unique_wallets:
                                            unique_wallets.add(signer)
                                            found_in_subbatch += 1
                                            
                                            # Сохраняем транзакцию сразу, так как это прямое доказательство связи
                                            # Это решит проблему "No relevant transactions found" позже
                                            await self._parse_and_save_rpc_transaction(tx_data, signer, mode)
                            except Exception as e:
                                logger.error(f"Error processing tx during program scan: {e}")
                                continue
                    
                    found_in_batch_total += found_in_subbatch
                    # Небольшая пауза между батчами
                    await asyncio.sleep(0.3)
                
                if found_in_batch_total == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0
                    
                logger.info(f"Current progress: {len(unique_wallets)}/{target_count} wallets found.")
                
                # Если уже набрали достаточно - выходим из пагинации для этой программы
                if len(unique_wallets) >= target_count:
                    break
        
        if len(unique_wallets) < target_count:
            logger.warning(f"⚠️ Could only find {len(unique_wallets)} unique wallets, but {target_count} were requested. Programs might be inactive or dominated by few users.")
            
        return list(unique_wallets)[:target_count]

    async def get_transaction(self, signature: str, mode: Mode) -> Optional[Dict]:
        return await self._post("getTransaction", [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}], mode)

    def _get_helius_url(self, mode: Mode) -> Optional[str]:
        if mode == Mode.MAINNET:
            return f"{settings.HELIUS_API_URL_MAINNET}/addresses"
        elif mode == Mode.DEVNET:
            return f"{settings.HELIUS_API_URL_DEVNET}/addresses"
        return None

    async def get_helius_transactions(self, wallet: str, mode: Mode) -> List[Dict]:
        base_url = self._get_helius_url(mode)
        if not base_url:
            return []
        
        url = f"{base_url}/{wallet}/transactions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, params={"api-key": settings.HELIUS_API_KEY, "limit": 100})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Helius API Error for {wallet} on {mode}: {e.response.text}")
                return []
            except Exception as e:
                logger.error(f"Helius API Error for {wallet} on {mode}: {e}")
                return []

    async def ingest_wallet(self, wallet: str, mode: Mode, limit: int = 100, depth: int = 0):
        """
        Загружает транзакции кошелька из Helius или стандартного RPC.
        depth: текущая глубина рекурсии для поиска связей (для Layer Analysis 3+).
        """
        if depth > 3: # Limit depth to avoid infinite loops and excessive RPC calls
            return

        logger.info(f"Starting ingestion for wallet: {wallet} (Mode.{mode}, Depth: {depth})")
        
        # Check if we already have enough data for this wallet recently
        raw_db = await get_raw_db()
        existing_count = await raw_db.raw_transactions.count_documents({"wallet": wallet, "mode": mode})
        if existing_count >= 5 and depth > 0: # For funders, 5 txs is enough to find their funder
            logger.info(f"Already have data for funder {wallet}, skipping deep ingest.")
        else:
            # Если это плейсхолдер - выходим сразу, чтобы не тратить запросы
            if wallet.startswith("WalletAddressPlaceholder_"):
                logger.warning(f"Skipping ingestion: {wallet} is a placeholder address.")
                return

            # Try Helius Enhanced API first if supported
            helius_txs = await self.get_helius_transactions(wallet, mode)
            
            if helius_txs:
                logger.info(f"Using Helius Enhanced API for {wallet} ({len(helius_txs)} txs)")
                
                new_txs = []
                new_instructions = []
                
                # Apply limit
                for tx in helius_txs[:limit]:
                    sig = tx.get("signature")
                    if not sig: continue
                    
                    exists = await raw_db.raw_transactions.find_one({"signature": sig, "wallet": wallet})
                    if exists: continue

                    try:
                        amount_change = 0
                        for transfer in tx.get("nativeTransfers", []):
                            if transfer.get("toUserAccount") == wallet:
                                amount_change += transfer.get("amount", 0)
                            if transfer.get("fromUserAccount") == wallet:
                                amount_change -= transfer.get("amount", 0)

                        # Token changes (New: Token Analysis Support)
                        token_changes = []
                        for t_transfer in tx.get("tokenTransfers", []):
                            token_changes.append({
                                "mint": t_transfer.get("mint"),
                                "from": t_transfer.get("fromUserAccount"),
                                "to": t_transfer.get("toUserAccount"),
                                "amount": t_transfer.get("tokenAmount", 0)
                            })

                        raw_tx = RawTransaction(
                            signature=sig,
                            block_time=tx.get("timestamp", 0),
                            slot=tx.get("slot", 0),
                            fee=tx.get("fee", 0),
                            status="success" if tx.get("transactionError") is None else "error",
                            processed=False,
                            mode=mode,
                            fee_payer=tx.get("feePayer"),
                            amount_change=amount_change,
                            compute_units=tx.get("computeUnitsConsumed", 0)
                        )
                        
                        tx_dict = raw_tx.model_dump()
                        tx_dict["wallet"] = wallet 
                        tx_dict["token_changes"] = token_changes
                        new_txs.append(tx_dict)
                        
                        # Extract ALL instructions
                        for inst in tx.get("instructions", []):
                            new_instructions.append(RawInstruction(
                                wallet=wallet,
                                tx_signature=sig,
                                program_id=inst.get("programId", "unknown"),
                                instruction_type=inst.get("type", "unknown"),
                                data=str(inst.get("data", "")),
                                accounts=[str(acc) for acc in inst.get("accounts", [])],
                                mode=mode
                            ).model_dump())

                            for inner in inst.get("innerInstructions", []):
                                new_instructions.append(RawInstruction(
                                    wallet=wallet,
                                    tx_signature=sig,
                                    program_id=inner.get("programId", "unknown"),
                                    instruction_type=inner.get("type", "unknown"),
                                    data=str(inner.get("data", "")),
                                    accounts=[str(acc) for acc in inner.get("accounts", [])],
                                    mode=mode
                                ).model_dump())
                    except Exception as e:
                        logger.error(f"Error parsing Helius tx {sig}: {e}")

                if new_txs:
                    await raw_db.raw_transactions.insert_many(new_txs)
                if new_instructions:
                    await raw_db.raw_instructions.insert_many(new_instructions)
            else:
                # Fallback to standard RPC
                signatures = await self.get_signatures(wallet, mode, limit=limit)
                if signatures:
                    for sig in signatures[:limit]:
                        exists = await raw_db.raw_transactions.find_one({"signature": sig, "wallet": wallet})
                        if exists: continue
                        tx_data = await self.get_transaction(sig, mode)
                        if tx_data:
                            await self._parse_and_save_rpc_transaction(tx_data, wallet, mode)
                            await asyncio.sleep(0.1)

        # --- PHASE 2: Recursive Ingestion for Layer Analysis ---
        earliest_tx = await raw_db.raw_transactions.find_one(
            {"wallet": wallet, "mode": mode},
            sort=[("block_time", 1)]
        )
        
        if earliest_tx and earliest_tx.get("fee_payer") and earliest_tx["fee_payer"] != wallet:
            funder = earliest_tx["fee_payer"]
            if funder not in settings.CEX_ADDRESSES:
                logger.info(f"🔗 Found funder {funder} for {wallet}. Ingesting funder data (depth {depth+1})...")
                await self.ingest_wallet(funder, mode, limit=20, depth=depth+1)

    async def _parse_and_save_rpc_transaction(self, tx_data: Dict, wallet: str, mode: Mode):
        """Парсит стандартный JSON RPC ответ транзакции и сохраняет в БД."""
        raw_db = await get_raw_db()
        sig = tx_data.get("transaction", {}).get("signatures", [None])[0]
        if not sig:
            return

        # Проверка на дубликат перед сохранением
        exists = await raw_db.raw_transactions.find_one({"signature": sig, "wallet": wallet})
        if exists:
            return

        try:
            # Calculate amount change for the wallet
            account_keys_raw = tx_data.get("transaction", {}).get("message", {}).get("accountKeys") or []
            
            # Normalize account_keys
            account_keys = []
            for k in account_keys_raw:
                if isinstance(k, dict):
                    account_keys.append(k.get("pubkey", ""))
                else:
                    account_keys.append(str(k))
            
            # Add loaded addresses from lookup tables if present
            loaded_addresses = tx_data.get("meta", {}).get("loadedAddresses") or {}
            if loaded_addresses:
                account_keys.extend(loaded_addresses.get("writable") or [])
                account_keys.extend(loaded_addresses.get("readonly") or [])

            try:
                wallet_index = account_keys.index(wallet)
                pre_balances = tx_data.get("meta", {}).get("preBalances", [])
                post_balances = tx_data.get("meta", {}).get("postBalances", [])
                amount_change = post_balances[wallet_index] - pre_balances[wallet_index]
            except (ValueError, IndexError):
                amount_change = 0

            compute_units = tx_data.get("meta", {}).get("computeUnitsConsumed", 0)

            raw_tx = RawTransaction(
                signature=sig,
                block_time=tx_data.get("blockTime", 0),
                slot=tx_data.get("slot", 0),
                fee=tx_data.get("meta", {}).get("fee", 0),
                status="success" if tx_data.get("meta", {}).get("err") is None else "error",
                processed=False,
                mode=mode,
                fee_payer=account_keys[0] if account_keys else None,
                amount_change=amount_change,
                compute_units=compute_units
            )
            
            tx_dict = raw_tx.model_dump()
            tx_dict["wallet"] = wallet 
            
            await raw_db.raw_transactions.update_one(
                {"signature": sig, "wallet": wallet},
                {"$set": tx_dict},
                upsert=True
            )
            
            # Ingest instructions
            message = tx_data.get("transaction", {}).get("message", {})
            all_instructions = []
            
            # Top-level
            for idx, inst in enumerate(message.get("instructions") or []):
                all_instructions.append(("top", idx, inst))
            
            # Inner
            inner_instructions_list = tx_data.get("meta", {}).get("innerInstructions") or []
            for inner_group in inner_instructions_list:
                parent_idx = inner_group.get("index", 0)
                for idx, inst in enumerate(inner_group.get("instructions") or []):
                    all_instructions.append((f"inner_{parent_idx}", idx, inst))

            new_instrs = []
            for type_str, idx, inst in all_instructions:
                try:
                    p_id_index = inst.get("programIdIndex")
                    if p_id_index is not None and p_id_index < len(account_keys):
                        program_id = account_keys[p_id_index]
                    else:
                        program_id = inst.get("programId", "unknown")

                    inst_accounts = []
                    for acc_idx in inst.get("accounts") or []:
                        if isinstance(acc_idx, int) and acc_idx < len(account_keys):
                            inst_accounts.append(account_keys[acc_idx])
                        else:
                            inst_accounts.append(str(acc_idx))

                    raw_inst = RawInstruction(
                        wallet=wallet,
                        tx_signature=sig,
                        program_id=str(program_id),
                        instruction_type=type_str,
                        data=str(inst.get("data", "")),
                        accounts=inst_accounts,
                        mode=mode
                    )
                    new_instrs.append(raw_inst.model_dump())
                except Exception as e:
                    logger.error(f"Error parsing instruction in {sig}: {e}")

            if new_instrs:
                # Use update with upsert for each instruction to avoid duplicates
                for instr in new_instrs:
                    await raw_db.raw_instructions.update_one(
                        {
                            "tx_signature": instr["tx_signature"], 
                            "wallet": instr["wallet"],
                            "program_id": instr["program_id"],
                            "data": instr["data"]
                        },
                        {"$set": instr},
                        upsert=True
                    )
        except Exception as e:
            logger.error(f"Error parsing RPC tx {sig}: {e}")

ingestion_service = IngestionService()
