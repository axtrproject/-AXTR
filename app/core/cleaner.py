import asyncio
import time
from app.db.mongodb import get_raw_db
from app.core.config import settings
from app.core.models import Mode

class AsyncCleaner:
    def __init__(self, interval_seconds: int = 3600):
        self.interval = interval_seconds

    async def run(self):
        while True:
            try:
                await self.cleanup()
            except Exception as e:
                print(f"Cleanup error: {e}")
            await asyncio.sleep(self.interval)

    async def cleanup(self):
        raw_db = await get_raw_db()
        current_time = int(time.time())

        # Testnet cleanup
        testnet_expiry = current_time - (settings.TTL_TESTNET * 86400)
        await raw_db.raw_transactions.delete_many({
            "mode": Mode.TESTNET.value,
            "processed": True,
            "block_time": {"$lt": testnet_expiry}
        })

        # Mainnet cleanup
        mainnet_expiry = current_time - (settings.TTL_MAINNET * 86400)
        await raw_db.raw_transactions.delete_many({
            "mode": Mode.MAINNET.value,
            "processed": True,
            "block_time": {"$lt": mainnet_expiry}
        })

        # Devnet cleanup
        devnet_expiry = current_time - (settings.TTL_DEVNET * 86400)
        await raw_db.raw_transactions.delete_many({
            "mode": Mode.DEVNET.value,
            "processed": True,
            "block_time": {"$lt": devnet_expiry}
        })

        print("Cleanup completed successfully")

cleaner = AsyncCleaner()
