from fastapi import FastAPI
from app.api.routes import router
from app.db.mongodb import db
from app.core.pipeline import pipeline
from app.core.cleaner import cleaner
from setup_db import setup_indices
import asyncio

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db.connect()
    await setup_indices()
    workers_task = asyncio.create_task(pipeline.start_workers())
    cleaner_task = asyncio.create_task(cleaner.run())
    yield
    # Shutdown
    workers_task.cancel()
    cleaner_task.cancel()
    await db.close()

app = FastAPI(title="Solana Trust Engine", lifespan=lifespan)

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    import os
    
    print("\n" + "="*30)
    print("  SOLANA TRUST ENGINE STARTUP")
    print("="*30)
    print("Выберите режим работы:")
    print("1. dev     (Solana Devnet)")
    print("2. test    (Solana Testnet)")
    print("3. mainnet (Solana Mainnet)")
    
    choice = input("\nВведите номер (1-3/dev/test/mainnet) [default: dev]: ").strip().lower()
    
    mode_map = {
        "1": "devnet",
        "dev": "devnet",
        "2": "testnet",
        "test": "testnet",
        "3": "mainnet",
        "mainnet": "mainnet"
    }
    
    selected_mode = mode_map.get(choice, "devnet")
    os.environ["APP_MODE"] = selected_mode
    
    print(f"\n>>> Запуск в режиме: {selected_mode.upper()}")
    print("="*30 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
