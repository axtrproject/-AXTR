import argparse
import asyncio
import secrets
import time
from typing import Optional
from app.db.mongodb import db, get_verified_db

async def generate_api_token(label: Optional[str]):
    await db.connect()
    v_db = await get_verified_db()
    token = None
    for _ in range(5):
        candidate = secrets.token_hex(32)
        exists = await v_db.api_tokens.find_one({"token": candidate})
        if not exists:
            token = candidate
            break
    if token is None:
        await db.close()
        raise RuntimeError("Failed to generate unique token")
    doc = {
        "token": token,
        "created_at": int(time.time()),
        "label": label,
        "active": True
    }
    await v_db.api_tokens.insert_one(doc)
    await db.close()
    print(token)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default=None)
    args = parser.parse_args()
    asyncio.run(generate_api_token(args.label))

if __name__ == "__main__":
    main()
