from fastapi import FastAPI, Header, HTTPException, Depends, Query
from typing import Optional
from app.db.mongodb import db, get_verified_db
from app.core.models import Mode

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
