from typing import List, Dict
from app.core.models import RawTransaction, Mode
from app.engine.base import BaseFeatureEngine
from app.db.mongodb import get_raw_db
from app.core.config import settings

class GraphEngine(BaseFeatureEngine):
    async def extract(self, wallet: str, transactions: List[RawTransaction], mode: Mode) -> Dict:
        if not transactions:
            return {
                "shared_funder": None, 
                "root_funder": None, 
                "cluster_size": 0, 
                "is_cex_funded": False, 
                "contract_intersections": [], 
                "is_cex_funded": False, 
                "common_signers": 0,
                "funding_delay": 0,
                "sync_start_count": 0,
                "indirect_funder": None,
                "graph_depth": 1,
                "funding_path": [],
                "recognition_bonus": 0
            }

        raw_db = await get_raw_db()
        mode_val = mode.value if hasattr(mode, 'value') else mode
        
        # 1. Identify Direct Funder
        # Ensure transactions are sorted by time to find the real EARLIEST funder
        sorted_txs = sorted(transactions, key=lambda x: x.block_time)
        earliest_tx = sorted_txs[0]
        direct_funder = earliest_tx.fee_payer
        
        # Recognition: Check if we already have this funder in evidence for other wallets
        recognition_bonus = 0
        if direct_funder and direct_funder != wallet and direct_funder not in settings.CEX_ADDRESSES:
            from app.db.mongodb import get_verified_db
            v_db = await get_verified_db()
            
            # Find if this funder is already "blacklisted" or associated with high-risk wallets
            known_association = await v_db.verified_wallets.find_one({
                "features.evidence.shared_funders": direct_funder,
                "scores.total": {"$gt": 60}
            })
            if known_association:
                # print(f"DEBUG: Recognition triggered for funder {direct_funder} via wallet {known_association['wallet']}")
                recognition_bonus = 15

        # 2. Deep Layer Analysis (BFS Depth 3)
        # We look for the "Root Funder" by tracing back funding sources
        current_funder = direct_funder
        path = [wallet]
        depth = 1
        is_cex_funded = current_funder in settings.CEX_ADDRESSES

        if not is_cex_funded and current_funder != wallet:
            path.append(current_funder)
            # Trace back up to 5 levels (Layer Analysis 3+)
            for d in range(2, 6):
                # Find who funded the current_funder
                # Optimization: we only care about the very first transaction that funded this wallet
                parent_tx = await raw_db.raw_transactions.find_one({
                    "wallet": current_funder,
                    "mode": mode_val
                }, sort=[("block_time", 1)])
                
                if parent_tx and parent_tx.get("fee_payer") and parent_tx["fee_payer"] != current_funder:
                    next_funder = parent_tx["fee_payer"]
                    if next_funder in path: # Circular funding detection
                        break
                    current_funder = next_funder
                    path.append(current_funder)
                    depth = d
                    if current_funder in settings.CEX_ADDRESSES:
                        is_cex_funded = True
                        break
                else:
                    break
        
        root_funder = current_funder

        # 3. Temporal Delay: Funding -> First Activity
        funding_delay = 0
        if direct_funder != wallet:
            wallet_txs = [tx for tx in sorted_txs if tx.fee_payer == wallet]
            if wallet_txs:
                funding_delay = wallet_txs[0].block_time - earliest_tx.block_time

        # 4. Cluster Size via Root Funder (Depth 1-3 Cluster)
        # Now we look for all wallets that share the same ROOT funder
        cluster_pipeline = [
            {"$match": {"fee_payer": root_funder, "mode": mode_val}},
            {"$group": {
                "_id": "$wallet",
                "first_activity": {"$min": "$block_time"}
            }},
            {"$limit": 200} # Increased limit for deep clusters
        ]
        cluster_cursor = raw_db.raw_transactions.aggregate(cluster_pipeline)
        cluster_wallets = await cluster_cursor.to_list(length=None)
        cluster_size = len(cluster_wallets)

        # 5. Synchronous Start Analysis (using root cluster)
        sync_start_count = 0
        if cluster_size > 1:
            my_start = earliest_tx.block_time
            sync_start_count = sum(1 for w in cluster_wallets if abs(w["first_activity"] - my_start) < 60)

        # 6. Contract Overlap (Common interaction targets)
        program_cursor = raw_db.raw_instructions.find({"wallet": wallet, "mode": mode_val})
        wallet_programs = await program_cursor.to_list(length=100)
        program_ids = list(set(inst["program_id"] for inst in wallet_programs))

        intersection_count = 0
        common_signers_count = 0
        
        if cluster_size > 1:
            other_wallets = [w["_id"] for w in cluster_wallets if w["_id"] != wallet]
            
            if program_ids:
                overlap_pipeline = [
                    {"$match": {
                        "wallet": {"$in": other_wallets[:100]}, # Performance limit
                        "program_id": {"$in": program_ids},
                        "mode": mode_val
                    }},
                    {"$group": {"_id": "$wallet"}},
                    {"$count": "overlap"}
                ]
                overlap_result = await raw_db.raw_instructions.aggregate(overlap_pipeline).to_list(length=1)
                intersection_count = overlap_result[0]["overlap"] if overlap_result else 0

            # 7. Common Signers
            all_fee_payers = list(set(tx.fee_payer for tx in transactions if tx.fee_payer != direct_funder))
            if all_fee_payers:
                signers_pipeline = [
                    {"$match": {
                        "wallet": {"$in": other_wallets[:100]},
                        "fee_payer": {"$in": all_fee_payers},
                        "mode": mode_val
                    }},
                    {"$group": {"_id": "$wallet"}},
                    {"$count": "common_signers"}
                ]
                signers_result = await raw_db.raw_transactions.aggregate(signers_pipeline).to_list(length=1)
                common_signers_count = signers_result[0]["common_signers"] if signers_result else 0

        return {
            "shared_funder": direct_funder,
            "root_funder": root_funder,
            "cluster_size": cluster_size,
            "is_cex_funded": is_cex_funded,
            "contract_intersections": intersection_count,
            "common_signers": common_signers_count,
            "cluster_ratio": (intersection_count + common_signers_count) / cluster_size if cluster_size > 0 else 0,
            "funding_delay": funding_delay,
            "sync_start_count": sync_start_count,
            "graph_depth": depth,
            "funding_path": path,
            "recognition_bonus": recognition_bonus
        }

    def score(self, features: Dict, mode: Mode) -> int:
        score = 0
        
        # 1. Significant cluster size (+10 to +25)
        cluster_size = features.get("cluster_size", 0)
        if cluster_size > 50:
            score += 25
        elif cluster_size > 10:
            score += 10
            
        # 2. High contract overlap within cluster (+15)
        if features.get("cluster_ratio", 0) > 0.5 and cluster_size > 5:
            score += 15
            
        # 3. Common signers (+10)
        if features.get("common_signers", 0) > 0:
            score += 10
            
        # 4. Direct/Root wallet-to-wallet funding (+10 to +30)
        if features.get("root_funder") and not features.get("is_cex_funded"):
            depth = features.get("graph_depth", 1)
            if depth >= 3:
                score += 30 # High obfuscation
            elif depth == 2:
                score += 20
            else:
                score += 10

        # 5. Temporal Connectivity: Synchronous Start (+15)
        if features.get("sync_start_count", 0) > 5:
            score += 15
        
        # 6. Temporal Connectivity: Short Funding Delay (+10)
        delay = features.get("funding_delay", 999999)
        if 0 < delay < 30:
            score += 10
            
        # 7. Recognition Bonus (+15)
        score += features.get("recognition_bonus", 0)
            
        # Cap score based on mode (TZ requirement)
        if mode == Mode.MAINNET:
            return min(score, 30)
        return min(score, 40)
