from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from app.core.models import UserVerified, Mode
from app.db.mongodb import get_verified_db
from app.core.pipeline import pipeline
from typing import List
import re

router = APIRouter()

def validate_wallet(address: str):
    if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
        raise HTTPException(status_code=400, detail="Invalid Solana wallet address")

@router.get("/wallet/{address}")
async def get_wallet_score(address: str, mode: Mode = Mode.MAINNET):
    validate_wallet(address)
    db = await get_verified_db()
    
    mode_val = mode.value if hasattr(mode, 'value') else mode
    user_data = await db.verified_wallets.find_one({"wallet": address, "mode": mode_val})
    
    if not user_data:
        # Trigger analysis if not found
        await pipeline.add_to_queue(address, mode)
        return {"status": "Analysis triggered. Please check back in a few seconds.", "wallet": address}
        
    user = UserVerified(**user_data)
    return {
        "scores": user.scores,
        "confidence": user.confidence,
        "labels": user.labels,
        "features": user.features,
        "last_seen": user.last_seen
    }

@router.post("/batch")
async def batch_process(addresses: List[str], mode: Mode = Mode.MAINNET):
    if len(addresses) > 100:
        raise HTTPException(status_code=400, detail="Batch size exceeds limit (100)")
    for address in addresses:
        validate_wallet(address)
        await pipeline.add_to_queue(address, mode)
    return {"status": "processing", "count": len(addresses)}

@router.get("/explain/{address}")
async def explain_score(address: str, mode: Mode = Mode.MAINNET):
    validate_wallet(address)
    db = await get_verified_db()
    
    mode_val = mode.value if hasattr(mode, 'value') else mode
    user_data = await db.verified_wallets.find_one({"wallet": address, "mode": mode_val})
    
    if not user_data:
        raise HTTPException(status_code=404, detail="Wallet not found")
        
    user = UserVerified(**user_data)
    return {
        "wallet": user.wallet,
        "features": user.features,
        "scores": user.scores,
        "confidence": user.confidence
    }
