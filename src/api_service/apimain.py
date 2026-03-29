from fastapi import FastAPI, Header, HTTPException, Depends, Query
from typing import Optional, List
import os, sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.db.mongodb import db, get_verified_db
from app.core.models import Mode
from app.core.pipeline import pipeline
import json
import re

async def require_token(x_api_token: Optional[str] = Header(None, alias="X-API-Token")):
    if not x_api_token:
        raise HTTPException(status_code=401, detail="API token required")
    v_db = await get_verified_db()
    token_doc = await v_db.api_tokens.find_one({"token": x_api_token, "active": True})
    if not token_doc:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return x_api_token

app = FastAPI(title="Access API")

@app.on_event("startup")
async def on_startup():
    await db.connect()

@app.on_event("shutdown")
async def on_shutdown():
    await db.close()

@app.get("/verified")
async def get_verified_wallets(
    count: int = Query(10, ge=1, le=100),
    mode: Mode = Mode.MAINNET,
    token: str = Depends(require_token)
):
    v_db = await get_verified_db()
    mode_val = mode.value if hasattr(mode, "value") else mode
    cursor = v_db.verified_wallets.find({"mode": mode_val}).sort("last_seen", -1).limit(count)
    rows = await cursor.to_list(length=count)
    results = []
    for row in rows:
        scores = row.get("scores", {})
        results.append({
            "wallet": row.get("wallet"),
            "score": scores.get("total", 0),
            "confidence": row.get("confidence", 0),
            "reason_codes": row.get("reason_codes", [])
        })
    return {"count": len(results), "results": results}

def _validate_wallet(address: str) -> bool:
    return re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address) is not None

def _load_program_ids() -> List[str]:
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.path.join(base, "programs.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [v["id"] for v in data.values() if "id" in v]

@app.get("/verified/wallet/{address}")
async def get_verified_wallet(
    address: str,
    mode: Mode = Mode.MAINNET,
    wait: int = Query(15, ge=0, le=60),
    token: str = Depends(require_token)
):
    if not _validate_wallet(address):
        raise HTTPException(status_code=400, detail="Invalid Solana wallet address")
    v_db = await get_verified_db()
    mode_val = mode.value if hasattr(mode, "value") else mode
    doc = await v_db.verified_wallets.find_one({"wallet": address, "mode": mode_val})
    if not doc:
        p_ids = _load_program_ids()
        try:
            await pipeline.process_wallet(address, mode, p_ids, skip_ingest=False)
        except Exception:
            pass
        if wait > 0:
            for _ in range(max(1, wait // 3)):
                found = await v_db.verified_wallets.find_one({"wallet": address, "mode": mode_val})
                if found:
                    doc = found
                    break
    if not doc:
        raise HTTPException(status_code=404, detail="Wallet not found")
    scores = doc.get("scores", {})
    return {
        "wallet": doc.get("wallet"),
        "score": scores.get("total", 0),
        "confidence": doc.get("confidence", 0),
        "reason_codes": doc.get("reason_codes", [])
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
