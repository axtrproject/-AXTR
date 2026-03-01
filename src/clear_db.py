import asyncio
from app.db.mongodb import db, get_raw_db, get_verified_db
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def clear_all_data():
    print("\n" + "="*50)
    print("🧹 ОЧИСТКА ВСЕХ БАЗ ДАННЫХ ОТ ТЕСТОВЫХ ДАННЫХ")
    print("="*50)
    
    await db.connect()
    r_db = await get_raw_db()
    v_db = await get_verified_db()
    
    # Список коллекций для очистки
    raw_collections = ["raw_transactions", "raw_instructions", "raw_accounts"]
    verified_collections = ["verified_wallets"]
    
    for coll in raw_collections:
        count = await r_db[coll].count_documents({})
        await r_db[coll].delete_many({})
        print(f"[*] Rawbd.{coll}: удалено {count} записей")
        
    for coll in verified_collections:
        count = await v_db[coll].count_documents({})
        await v_db[coll].delete_many({})
        print(f"[*] Clearbd.{coll}: удалено {count} записей")
        
    await db.close()
    print("\n✅ Базы данных полностью очищены.")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(clear_all_data())
