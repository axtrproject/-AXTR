import asyncio
import sys
import os

# Add the project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.scoring import ScoringEngine
from app.core.models import RawTransaction, Mode
import time

async def test_scoring():
    engine = ScoringEngine()
    
    # Mock some transactions
    wallet = "So1aNa..."
    current_time = int(time.time())
    
    # 1. Perfectly even intervals (should give +10)
    txs = []
    for i in range(10):
        txs.append(RawTransaction(
            signature=f"sig_{i}",
            block_time=current_time + (i * 60), # Exactly 60s interval
            slot=1000 + i,
            fee=5000,
            status="success",
            mode=Mode.MAINNET
        ))
        
    result = await engine.analyze(wallet, txs, Mode.MAINNET)
    print(f"Total score for even intervals: {result['scores'].total}")
    print(f"Timing score: {result['scores'].timing}")
    print(f"Interpretation: {engine.interpret(result['scores'].total)}")

    # 2. Burst patterns (should give +10)
    txs = []
    for i in range(10):
        txs.append(RawTransaction(
            signature=f"sig_burst_{i}",
            block_time=current_time + (i * 2), # 2s interval (burst)
            slot=2000 + i,
            fee=5000,
            status="success",
            mode=Mode.MAINNET
        ))
        
    result = await engine.analyze(wallet, txs, Mode.MAINNET)
    print(f"\nTotal score for bursts: {result['scores'].total}")
    print(f"Timing score: {result['scores'].timing}")
    print(f"Interpretation: {engine.interpret(result['scores'].total)}")

if __name__ == "__main__":
    asyncio.run(test_scoring())
