import asyncio
import sys
import json
import logging
import os
import time
from typing import List, Optional
from app.db.mongodb import db, get_verified_db
from app.core.models import UserVerified, Mode
from app.core.pipeline import pipeline

# Настройка логгера для CLI
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def load_programs():
    if os.path.exists("programs.json"):
        with open("programs.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def select_programs():
    programs = load_programs()
    if not programs:
        print("\n⚠️ Файл programs.json не найден или пуст.")
        return None

    print("\n--- СПИСОК ДОСТУПНЫХ ПРОГРАММ ---")
    for key, info in programs.items():
        print(f"{key}. {info['name']} ({info['id']})")
    
    print("\nВыберите программы для анализа:")
    print("Укажите номера через запятую или 'all' для всех.")
    choice = input("Ваш выбор: ").strip().lower()
    
    if choice == 'all':
        return [info['id'] for info in programs.values()]
    
    try:
        selected_keys = [k.strip() for k in choice.split(',')]
        selected_ids = [programs[k]['id'] for k in selected_keys if k in programs]
        return selected_ids if selected_ids else None
    except Exception:
        print("❌ Ошибка ввода. Будут проанализированы все транзакции.")
        return None

async def run_mass_analysis(mode_str: str, count: int, program_ids: Optional[List[str]] = None):
    await db.connect()
    
    mode_map = {
        "devnet": Mode.DEVNET,
        "testnet": Mode.TESTNET,
        "mainnet": Mode.MAINNET
    }
    mode = mode_map.get(mode_str, Mode.DEVNET)
    
    print(f"\n🚀 Запуск массового анализа {count} кошельков в сети {mode_str}...")
    if program_ids:
        print(f"🔍 Извлечение кошельков из смарт-контрактов: {len(program_ids)} программ...")
    else:
        print("❌ Ошибка: Необходимо выбрать хотя бы одну программу для извлечения кошельков.")
        return

    # Извлекаем реальные кошельки напрямую из блокчейна через выбранные Program IDs
    try:
        from app.core.ingestion import ingestion_service
        found_wallets = await ingestion_service.get_active_wallets_for_programs(program_ids, mode, target_count=count)
        
        if not found_wallets:
            print("❌ Не удалось найти активные кошельки для выбранных программ.")
            return
            
        print(f"[*] Успешно извлечено {len(found_wallets)} уникальных кошельков для анализа.")
        test_wallets = found_wallets
    except Exception as e:
        logger.error(f"Ошибка при извлечении кошельков из смарт-контрактов: {e}")
        return
    
    # --- ФАЗА 1: СБОР ДАННЫХ (INGESTION) ---
    print(f"\n📥 Фаза 1: Сбор транзакций для {len(test_wallets)} кошельков...")
    from app.core.ingestion import ingestion_service
    
    # Чтобы не превышать лимиты RPC, будем собирать данные пачками
    ingest_batch_size = 5
    for i in range(0, len(test_wallets), ingest_batch_size):
        batch = test_wallets[i:i+ingest_batch_size]
        print(f"[*] Загрузка данных для пачки кошельков {i+1}-{min(i+ingest_batch_size, len(test_wallets))}...")
        tasks = [ingestion_service.ingest_wallet(w, mode) for w in batch]
        await asyncio.gather(*tasks)
        # Небольшая пауза между пачками
        await asyncio.sleep(1)

    print("\n✅ Сбор данных завершен. Все транзакции сохранены в Rawbd.")

    # --- ФАЗА 2: АНАЛИЗ (ANALYSIS) ---
    print(f"\n🧠 Фаза 2: Анализ данных ( Scoring Engine )...")
    import time
    run_id = int(time.time())
    
    await pipeline.start_workers(num_workers=10)
    
    for wallet in test_wallets:
        # Указываем skip_ingest=True, так как данные уже в базе
        await pipeline.add_to_queue(wallet, mode, program_ids, skip_ingest=True)
    
    print(f"[*] {len(test_wallets)} кошельков добавлено в очередь анализа.")
    
    # Ждем завершения
    await pipeline.queue.join()
    
    # После завершения выводим статистику
    await show_statistics(mode, test_wallets)
    
    await db.close()

async def show_statistics(mode: Mode, current_wallets: List[str] = None):
    verified_db = await get_verified_db()
    
    mode_val = mode.value if hasattr(mode, 'value') else mode
    
    # Если переданы кошельки текущего запуска, фильтруем по ним
    query = {"mode": mode_val}
    if current_wallets:
        query["wallet"] = {"$in": current_wallets}

    total_count = await verified_db.verified_wallets.count_documents(query)
    
    if current_wallets and total_count < len(current_wallets):
        missing_count = len(current_wallets) - total_count
        print(f"\n⚠️ ВНИМАНИЕ: {missing_count} кошельков не были проанализированы (недостаточно данных или ошибки).")
        
        # Поиск тех, кто не попал в базу
        found_in_db = await verified_db.verified_wallets.distinct("wallet", query)
        missing_wallets = set(current_wallets) - set(found_in_db)
        if missing_wallets:
            print(f"[*] Пропущенные адреса (первые 5): {list(missing_wallets)[:5]}")
    
    if total_count == 0:
        print("\n" + "="*45)
        print(f"📊 РЕЗУЛЬТАТЫ АНАЛИЗА ({mode.upper()})")
        print("="*45)
        print("Нет данных для отображения статистики.")
        print("="*45)
        return

    bots_query = query.copy()
    bots_query["scores.total"] = {"$gt": 60}
    bots_count = await verified_db.verified_wallets.count_documents(bots_query)
    humans_count = total_count - bots_count
    
    bot_percent = (bots_count / total_count) * 100
    human_percent = (humans_count / total_count) * 100
    
    # Расчет агрегированных данных
    pipeline_stats = [
        {"$match": query},
        {"$group": {
            "_id": None,
            "avg_score": {"$avg": "$scores.total"},
            "max_score": {"$max": "$scores.total"},
            "min_score": {"$min": "$scores.total"},
            "avg_confidence": {"$avg": "$confidence"}
        }}
    ]
    stats_res = await verified_db.verified_wallets.aggregate(pipeline_stats).to_list(1)
    stats = stats_res[0] if stats_res else {}

    print("\n" + "="*45)
    print(f"📊 РЕЗУЛЬТАТЫ АНАЛИЗА ({mode.upper()})")
    print("="*45)
    print(f"Всего проанализировано: {total_count}")
    print(f"🤖 Боты (Score > 60):  {bots_count} ({bot_percent:.1f}%)")
    print(f"👤 Люди (Score <= 60): {humans_count} ({human_percent:.1f}%)")
    print("-" * 25)
    print(f"Средний Score:       {stats.get('avg_score', 0):.2f}")
    print(f"Средний Confidence:  {stats.get('avg_confidence', 0):.2f}")
    print(f"Max Score:           {stats.get('max_score', 0)}")
    print(f"Min Score:           {stats.get('min_score', 0)}")
    print("="*45)

REASON_DESCRIPTIONS = {
    "G01": "Обнаружен в крупном кластере (ферма ботов)",
    "G02": "Глубокое запутывание связей (Layer 3+)",
    "G03": "Множественное спонсирование (один ко многим)",
    "B01": "Стабильный Compute Unit (паттерн ПО)",
    "E01": "Алгоритмический Zero-Loss (MEV/Arbitrage)",
    "E02": "Синхронизация ROI внутри кластера"
}

def get_score_color(score: int) -> str:
    if score > 70: return "\033[91m" # Red
    if score > 30: return "\033[93m" # Yellow
    return "\033[92m" # Green

def get_score_bar(score: int, length: int = 20) -> str:
    filled = int(score / 100 * length)
    bar = "█" * filled + "░" * (length - filled)
    color = get_score_color(score)
    return f"{color}[{bar}] {score}/100\033[0m"

async def query_wallet(address: str, mode: str, program_ids: Optional[List[str]] = None):
    m = Mode.MAINNET if mode == "mainnet" else (Mode.TESTNET if mode == "testnet" else Mode.DEVNET)
    
    print(f"\n🔍 Анализ кошелька: {address}")
    print(f"🌐 Сеть: {m.upper()}")
    
    await db.connect()
    await pipeline.process_wallet(address, m, program_ids)
    
    v_db = await get_verified_db()
    res = await v_db.verified_wallets.find_one({"wallet": address, "mode": m})
    
    if res:
        print("\n" + "="*50)
        print(f"📊 РЕЗУЛЬТАТЫ АНАЛИЗА: {address}")
        print("="*50)
        
        scores = res.get("scores", {})
        total_score = scores.get("total", 0)
        profile = res.get("profile", "Unknown")
        confidence = res.get("confidence", 0)
        
        # Основной скор - МАКСИМАЛЬНО ЗАМЕТНО
        print(f"\n🎯 ИТОГОВЫЙ РЕЙТИНГ РИСКА:")
        print(f"   {get_score_bar(total_score, 30)}")
        print(f"   Профиль: {profile}")
        print(f"   Уверенность системы: {confidence*100:.1f}%")

        # Детализация скора (Score Breakdown) - ПРИОРИТЕТ ВЫШЕ ЧЕМ У ОБЪЯСНЕНИЙ
        print("\n� ДЕТАЛИЗАЦИЯ БАЛЛОВ (SCORE BREAKDOWN):")
        print(f"  ├─ Timing Analysis:   {scores.get('timing', 0):>3} pts")
        print(f"  ├─ Graph/Network:     {scores.get('graph', 0):>3} pts")
        print(f"  ├─ Behavior/Pattern:  {scores.get('behavior', 0):>3} pts")
        print(f"  └─ Economic/ROI:      {scores.get('economic', 0):>3} pts")
        
        # Причины - теперь идут ПОСЛЕ цифр
        print("\n📝 ОБОСНОВАНИЕ (EXPLAINABILITY):")
        rcodes = res.get("reason_codes", [])
        if rcodes:
            for rc in rcodes:
                desc = REASON_DESCRIPTIONS.get(rc, "Неизвестный код")
                print(f"  • [{rc}] {desc}")
        else:
            print("  • Паттерны ботов не обнаружены")

        print("\n📈 ИСТОРИЯ ИЗМЕНЕНИЙ (SCORE EVOLUTION):")
        history = res.get("history", [])
        for h in history[-5:]:
            ts_str = time.strftime('%H:%M %d.%m', time.localtime(h['ts']))
            print(f"  [{ts_str}] Total: {h['total']} (T:{h.get('timing',0)} G:{h.get('graph',0)} B:{h.get('behavior',0)} E:{h.get('economic',0)})")

        print("\n⚠️ КРИТИЧЕСКИЕ ПРЕДУПРЕЖДЕНИЯ:")
        alerts = res.get("features", {}).get("alerts", [])
        if alerts:
            for a in alerts:
                print(f"  [!] {a}")
        else:
            print("  Нет")
        
        print("="*50)
    else:
        print("\n❌ Ошибка: данные не были сохранены в БД.")
    
    await db.close()

from app.engine.analytics import analytics_engine

async def show_global_analytics():
    print("\n" + "═"*60)
    print("📊 ГЛОБАЛЬНЫЙ АНАЛИТИЧЕСКИЙ ОТЧЕТ (TRUST ENGINE)")
    print("═"*60)
    
    await db.connect()
    data = await analytics_engine.get_global_report()
    
    # 1. Coverage
    c = data['coverage']
    print(f"\n1. COVERAGE (ОХВАТ СЕТИ)")
    print(f"  ├─ Уникальных кошельков: {c['total_wallets']}")
    print(f"  │  └─ Mainnet: {c['mainnet']} | Devnet: {c['devnet']} | Testnet: {c['testnet']}")
    print(f"  ├─ Транзакций обработано: {c['total_txs']}")
    print(f"  └─ Смарт-контрактов покрыто: {c['programs']}")

    # 2. Detection
    d = data['detection']
    print(f"\n2. DETECTION (КАЧЕСТВО ДЕТЕКЦИИ)")
    print(f"  ├─ High Risk (70-100): {d['high_risk_count']} ({d['high_risk_pct']:.1f}%)")
    print(f"  ├─ Low Risk (0-30):   {d['low_risk_count']} ({d['low_risk_pct']:.1f}%)")
    print(f"  └─ Распределение скора (Histogram):")
    for bucket in d.get('distribution', []):
        label = bucket['_id']
        if isinstance(label, (int, float)):
            low, high = int(label), int(label) + 20
            label_str = f"{low:2}-{high:<3}"
        else:
            label_str = f"{label:<6}"
            
        count = bucket['count']
        total = c['total_wallets']
        bar = "█" * int(count / max(1, total) * 20)
        print(f"     {label_str}: {bar} ({count})")

    # 3. False Positive Control
    fp = data['fp_control']
    print(f"\n3. FALSE POSITIVE CONTROL (ТОЧНОСТЬ)")
    print(f"  ├─ Pro Trader ≠ Bot кейсы: {fp['pro_traders']}")
    print(f"  ├─ Изменения профиля:      {fp['profile_changes']} кошельков")
    print(f"  └─ Расчетный % False Positive: {fp['fp_rate']}")

    # 4. Memory & Evolution
    m = data['memory']
    avg_gain = m.get('avg_score_gain', 0)
    print(f"\n4. MEMORY / EVOLUTION (ИСТОРИЯ)")
    print(f"  ├─ Кошельков с историей: {m['evolution_count']}")
    print(f"  ├─ История > 30 дней:    {m['long_history']}")
    print(f"  ├─ Затвердевших паттернов: {m['hardened_patterns']}")
    print(f"  └─ Среднее усиление скора: {avg_gain:+.1f}% за сессию")

    # 5. Graph Power
    g = data['graph']
    print(f"\n5. GRAPH POWER (СВЯЗИ И ФЕРМЫ)")
    print(f"  ├─ Обнаружено кластеров: {g['cluster_count']}")
    print(f"  ├─ Средний размер фермы: {g['avg_size']:.1f} кошельков")
    print(f"  ├─ Средний Score кластера: {g['avg_cluster_score']:.1f}")
    print(f"  └─ Глубина детекции:")
    print(f"     ├─ 1-hop (Прямые): {g['one_hop_count']}")
    print(f"     └─ 3-hop (Скрытые): {g['three_hop_count']}")

    # 6. Explainability
    e = data['explainability']
    print(f"\n6. EXPLAINABILITY (ОБОСНОВАННОСТЬ)")
    print(f"  ├─ Среднее кол-во причин на кошелек: {e['avg_codes_per_wallet']}")
    print(f"  ├─ Топ причин (Reason Codes):")
    for code in e['top_codes']:
        desc = REASON_DESCRIPTIONS.get(code['_id'], code['_id'])
        print(f"  │  • {code['_id']}: {code['count']} ({desc})")

    # 7. Performance
    p = data['performance']
    print(f"\n7. PERFORMANCE (ИНФРАСТРУКТУРА)")
    print(f"  ├─ Режим: Полная асинхронность (Async Pipeline)")
    print(f"  ├─ Обработано транзакций: {p['total_processed_tx']}")
    print(f"  ├─ Текущая скорость:      {p['throughput']}")
    print(f"  ├─ Средняя задержка:      {p['avg_latency']}")
    print(f"  └─ Утилизация RPC:        {p['rpc_efficiency']} (Batch Requests)")

    print("\n" + "═"*60)
    await db.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        async def main_menu():
            while True:
                print("\n" + "="*35)
                print("   SOLANA TRUST ENGINE SYSTEM")
                print("="*35)
                print("1. Анализ одного кошелька")
                print("2. Массовый анализ (Bulk Analysis)")
                print("3. Показать общую статистику")
                print("4. Быстрый просмотр кошелька из БД")
                print("5. ГЛОБАЛЬНЫЙ АНАЛИТИЧЕСКИЙ ОТЧЕТ")
                print("0. Выход")
                
                choice = input("\nВыберите действие (0-5): ").strip()
                
                if choice == "1":
                    address = input("Введите адрес кошелька: ").strip().replace('"', '').replace("'", "")
                    print("\nВыберите сеть (1: dev, 2: test, 3: mainnet):")
                    net = input("Ваш выбор [3]: ").strip() or "3"
                    mode_map = {"1": "devnet", "2": "testnet", "3": "mainnet"}
                    p_ids = select_programs()
                    await query_wallet(address, mode_map.get(net, "mainnet"), p_ids)
                    
                elif choice == "2":
                    print("\n--- НАСТРОЙКА МАССОВОГО АНАЛИЗА ---")
                    print("Выберите сеть (1: dev, 2: test, 3: mainnet):")
                    net_choice = input("Ваш выбор [1]: ").strip() or "1"
                    count = int(input("Сколько кошельков анализировать? [1000]: ").strip() or "1000")
                    p_ids = select_programs()
                    mode_map = {"1": "devnet", "2": "testnet", "3": "mainnet"}
                    await run_mass_analysis(mode_map.get(net_choice, "devnet"), count, p_ids)
                    
                elif choice == "3":
                    print("\nВыберите сеть (1: dev, 2: test, 3: mainnet):")
                    net_choice = input("Ваш выбор [3]: ").strip() or "3"
                    mode_map = {"1": "devnet", "2": "testnet", "3": "mainnet"}
                    await db.connect()
                    m = Mode.MAINNET if net_choice == "3" else (Mode.TESTNET if net_choice == "2" else Mode.DEVNET)
                    await show_statistics(m)
                    await db.close()
                    
                elif choice == "4":
                    address = input("Введите адрес кошелька: ").strip().replace('"', '').replace("'", "")
                    await db.connect()
                    v_db = await get_verified_db()
                    cursor = v_db.verified_wallets.find({"wallet": address})
                    results = await cursor.to_list(length=10)
                    if results:
                        print("\n" + "="*50)
                        print(f"📄 НАЙДЕНЫ ДАННЫЕ ДЛЯ {address}")
                        print("="*50)
                        for data in results:
                            mode_found = data.get("mode", "unknown").upper()
                            data.pop("_id", None)
                            print(f"\n[ СЕТЬ: {mode_found} ]")
                            print(json.dumps(data, indent=2, ensure_ascii=False))
                            print("-" * 30)
                        print("="*50)
                    else:
                        print(f"\n❌ Кошелек {address} не найден ни в одной из сетей.")
                    await db.close()

                elif choice == "5":
                    await show_global_analytics()

                elif choice == "0":
                    break

        try:
            asyncio.run(main_menu())
        except KeyboardInterrupt:
            pass
    else:
        # Сохраняем поддержку старого CLI вызова
        address = sys.argv[1].replace('"', '').replace("'", "")
        mode = sys.argv[2] if len(sys.argv) > 2 else "mainnet"
        asyncio.run(query_wallet(address, mode))
