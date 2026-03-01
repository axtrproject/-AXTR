import statistics
from typing import List, Dict
from app.core.models import RawTransaction, Mode
from app.engine.base import BaseFeatureEngine

class TimingEngine(BaseFeatureEngine):
    async def extract(self, wallet: str, transactions: List[RawTransaction], mode: Mode) -> Dict:
        if len(transactions) < 2:
            return {"avg_interval": 0, "std_dev": 0, "burst_freq": 0, "activity_24_7": False}

        times = sorted([tx.block_time for tx in transactions])
        intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
        
        avg_interval = statistics.mean(intervals)
        std_dev = statistics.stdev(intervals) if len(intervals) > 1 else 0
        
        # Burst frequency: txs within short time (e.g., < 10s)
        bursts = [i for i in intervals if i < 10]
        burst_freq = len(bursts) / len(intervals)
        
        # 24/7 activity check (simplified: check if txs span across different hours)
        hours = set([time % 86400 // 3600 for time in times])
        activity_24_7 = len(hours) >= 18 # Active in at least 18 different hours of the day

        return {
            "avg_interval": avg_interval,
            "std_dev": std_dev,
            "burst_freq": burst_freq,
            "activity_24_7": activity_24_7,
            "intervals": intervals
        }

    def score(self, features: Dict, mode: Mode) -> int:
        score = 0
        std_dev = features.get("std_dev", 0)
        avg_interval = features.get("avg_interval", 0)
        
        # 1. Perfectly even intervals (+10)
        # If std_dev is very low compared to avg_interval
        if avg_interval > 0 and (std_dev / avg_interval) < 0.05:
            score += 10
            
        # 2. Repeating burst patterns (+10)
        if features.get("burst_freq", 0) > 0.5:
            score += 10
            
        # 3. 24/7 without pauses (+10)
        if features.get("activity_24_7", False):
            score += 10
            
        # Cap score based on mode (TZ requirement)
        if mode == Mode.MAINNET:
            return min(score, 20)
        return min(score, 30)
