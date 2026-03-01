import statistics
from typing import List, Dict
from app.core.models import RawTransaction, Mode
from app.engine.base import BaseFeatureEngine
from app.db.mongodb import get_raw_db

class EconomicEngine(BaseFeatureEngine):
    async def extract(self, wallet: str, transactions: List[RawTransaction], mode: Mode) -> Dict:
        if not transactions:
            return {
                "roi": 0.0,
                "zero_loss_variance": 0.0,
                "fee_efficiency": 0.0,
                "capital_reuse": 0.0,
                "cluster_roi_dispersion": 1.0,
                "profit_to_volume_ratio": 0.0,
                "token_pnl": 0.0
            }
            
        raw_db = await get_raw_db()
        mode_val = mode.value if hasattr(mode, 'value') else mode

        # 1. ROI = profit / time
        total_profit_sol = sum(tx.amount_change for tx in transactions) / 1e9 # Convert lamports to SOL
        
        # Token Analysis (PNL from SPL tokens)
        token_pnl = 0.0
        
        # Let's fetch the raw documents to get 'token_changes'
        sigs = [tx.signature for tx in transactions]
        raw_docs = await raw_db.raw_transactions.find({"signature": {"$in": sigs}}).to_list(length=None)
        
        for doc in raw_docs:
            for t_change in doc.get("token_changes", []):
                if t_change["to"] == wallet:
                    token_pnl += t_change["amount"]
                if t_change["from"] == wallet:
                    token_pnl -= t_change["amount"]

        # Rough normalization: assume tokens are worth something
        # In a real system, we would fetch prices.
        total_profit = total_profit_sol + (token_pnl * 0.0001) 
        total_volume = sum(abs(tx.amount_change) for tx in transactions) / 1e9
        
        start_time = min(tx.block_time for tx in transactions)
        end_time = max(tx.block_time for tx in transactions)
        duration_hours = max((end_time - start_time) / 3600, 0.01)
        
        roi = total_profit / duration_hours
        profit_to_volume_ratio = total_profit / total_volume if total_volume > 0 else 0
        
        # 2. Zero-Loss Analysis
        # ВАЖНО: Профессиональные трейдеры тоже могут иметь низкую дисперсию убытков.
        # Чтобы отличить их от ботов, мы смотрим на количество транзакций и использование Jito/MEV.
        profits_sol = [tx.amount_change / 1e9 for tx in transactions]
        zero_loss_variance = statistics.variance(profits_sol) if len(profits_sol) > 1 else 999999
        
        # Анализ проскальзывания и "чистоты" сделок
        is_high_frequency = len(transactions) / duration_hours > 50 # > 50 tx/hour
        mev_usage = 0
        for doc in raw_docs:
            # Поиск взаимодействия с Jito или MEV-протоколами
            for inst in doc.get("instructions", []):
                if "Jito" in str(inst) or "MEV" in str(inst):
                    mev_usage += 1
        
        # Если это высокочастотный трейдинг с MEV - это скорее бот, чем профи-человек
        if is_high_frequency and mev_usage > 0:
            zero_loss_variance = 0.000000001 # Принудительно низкая вариативность для ботов
        elif not is_high_frequency and zero_loss_variance < 0.001:
            # Для людей-профи с низкой вариативностью даем "скидку"
            zero_loss_variance = 0.5 
        
        # 3. fee efficiency = fees relative to median
        avg_fee = sum(tx.fee for tx in transactions) / len(transactions)
        fee_efficiency = 5000 / avg_fee if avg_fee > 0 else 1.0

        # 4. Cluster ROI Dispersion (New feature)
        # Find other wallets with the same funder
        earliest_tx = min(transactions, key=lambda x: x.block_time)
        funder = earliest_tx.fee_payer
        
        cluster_roi_dispersion = 1.0 # Default high dispersion
        if funder:
            # Aggregate total profit and time for cluster wallets
            pipeline = [
                {"$match": {"fee_payer": funder, "mode": mode_val}},
                {"$group": {
                    "_id": "$wallet",
                    "total_profit": {"$sum": "$amount_change"},
                    "start": {"$min": "$block_time"},
                    "end": {"$max": "$block_time"}
                }},
                {"$limit": 50}
            ]
            cluster_data = await raw_db.raw_transactions.aggregate(pipeline).to_list(length=None)
            
            if len(cluster_data) > 2:
                rois = []
                for d in cluster_data:
                    dur = max((d["end"] - d["start"]) / 3600, 0.01)
                    rois.append(d["total_profit"] / dur)
                
                mean_roi = statistics.mean(rois)
                if mean_roi != 0:
                    cluster_roi_dispersion = statistics.stdev(rois) / abs(mean_roi)
                else:
                    cluster_roi_dispersion = 0.0 # Identical zero profit

        # 5. Capital reuse
        total_outflow = sum(abs(tx.amount_change) for tx in transactions if tx.amount_change < 0)
        capital_reuse = 0.0
        if total_outflow > 0:
            total_inflow = max(sum(tx.amount_change for tx in transactions if tx.amount_change > 0), 1)
            if total_outflow / total_inflow > 0.9:
                capital_reuse = 1.0

        return {
            "roi": roi,
            "zero_loss_variance": zero_loss_variance,
            "fee_efficiency": fee_efficiency,
            "capital_reuse": capital_reuse,
            "cluster_roi_dispersion": cluster_roi_dispersion,
            "profit_to_volume_ratio": profit_to_volume_ratio,
            "token_pnl": token_pnl,
            "total_profit": total_profit,
            "avg_fee": avg_fee
        }

    def score(self, features: Dict, mode: Mode) -> int:
        score = 0
        
        # 1. ROI scoring (+10)
        roi = features.get("roi", 0)
        if roi > 1.0: # 1 SOL per hour is high
            score += 10
            
        # 2. Zero-loss variance scoring (+10)
        variance = features.get("zero_loss_variance", 999999)
        if variance < 0.001: # Very low variance in SOL
            score += 10
            
        # 3. Fee efficiency (+10)
        efficiency = features.get("fee_efficiency", 0)
        if efficiency > 1.5:
            score += 10

        # 4. Low Cluster ROI Dispersion (+15)
        # If dispersion < 0.1, it's a very suspicious farm
        dispersion = features.get("cluster_roi_dispersion", 1.0)
        if dispersion < 0.1:
            score += 15

        # 5. High Profit to Volume ratio (+10)
        # Bots often extract maximum value with minimum volume
        if features.get("profit_to_volume_ratio", 0) > 0.8:
            score += 10
            
        # Cap score based on mode (TZ requirement: mainnet only)
        if mode != Mode.MAINNET:
            return 0
        return min(score, 30)
