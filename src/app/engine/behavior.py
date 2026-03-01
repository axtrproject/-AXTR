import math
import statistics
from typing import List, Dict, Set
from app.core.models import RawTransaction, Mode, RawInstruction
from app.engine.base import BaseFeatureEngine
from app.db.mongodb import get_raw_db

class BehaviorEngine(BaseFeatureEngine):
    async def extract(self, wallet: str, transactions: List[RawTransaction], mode: Mode) -> Dict:
        if not transactions:
            return {
                "sequence_complexity": 0, 
                "jaccard_similarity": 0, 
                "compute_stability": 1.0,
                "cu_per_instruction": 0,
                "cu_variance": 0
            }
            
        raw_db = await get_raw_db()
        mode_val = mode.value if hasattr(mode, 'value') else mode

        # 1. Fetch all instructions for this wallet to build sequences
        cursor = raw_db.raw_instructions.find({"wallet": wallet, "mode": mode_val}).sort("tx_signature", 1)
        instructions = await cursor.to_list(length=1000)
        
        # Group instructions by transaction signature
        tx_map: Dict[str, List[RawInstruction]] = {}
        for inst_data in instructions:
            sig = inst_data["tx_signature"]
            if sig not in tx_map:
                tx_map[sig] = []
            tx_map[sig].append(RawInstruction(**inst_data))

        # 2. Sequence Analysis (Jaccard Similarity between transactions)
        # We compare the set of (program_id, instruction_type) per transaction
        tx_sequences: List[Set[str]] = []
        for sig in tx_map:
            seq = {f"{inst.program_id}:{inst.instruction_type}" for inst in tx_map[sig]}
            tx_sequences.append(seq)

        avg_jaccard = 0
        if len(tx_sequences) > 1:
            similarities = []
            for i in range(len(tx_sequences)):
                for j in range(i + 1, len(tx_sequences)):
                    s1, s2 = tx_sequences[i], tx_sequences[j]
                    if not s1 and not s2:
                        intersection = 0
                        union = 0
                    else:
                        intersection = len(s1.intersection(s2))
                        union = len(s1.union(s2))
                    
                    similarities.append(intersection / union if union > 0 else 1.0)
            avg_jaccard = statistics.mean(similarities)
        elif len(tx_sequences) == 1:
            avg_jaccard = 1.0

        # 3. Normalized Compute Unit Analysis
        # CU / instruction_count and CU variance
        cu_stats = []
        for tx in transactions:
            inst_count = len(tx_map.get(tx.signature, []))
            if inst_count > 0:
                cu_stats.append(tx.compute_units / inst_count)
            else:
                cu_stats.append(tx.compute_units)

        cu_per_instruction = statistics.mean(cu_stats) if cu_stats else 0
        cu_variance = statistics.variance(cu_stats) if len(cu_stats) > 1 else 0
        
        # Stability metric: 1.0 is perfectly stable (low variance)
        compute_stability = 1.0 / (1.0 + math.sqrt(cu_variance)) if cu_stats else 1.0

        unique_programs_list = list(set(inst_data["program_id"] for inst_data in instructions))

        # 4. Noise Detection (Adversarial Thinking)
        # Bots sometimes add "noise" - tiny transactions to random programs to look human
        noise_score = 0
        small_tx_ratio = sum(1 for tx in transactions if 0 < abs(tx.amount_change) < 0.001 * 1e9) / len(transactions)
        
        # If too many tiny transactions AND high program diversity, it might be noise simulation
        is_noise_simulation = small_tx_ratio > 0.5 and len(unique_programs_list) > 10
        if is_noise_simulation:
            noise_score = 10

        return {
            "sequence_length": len(transactions),
            "avg_jaccard": avg_jaccard,
            "compute_stability": compute_stability,
            "cu_per_instruction": cu_per_instruction,
            "cu_variance": cu_variance,
            "unique_programs": len(unique_programs_list),
            "unique_programs_list": unique_programs_list,
            "is_noise_simulation": is_noise_simulation
        }

    def score(self, features: Dict, mode: Mode) -> int:
        score = 0
        
        # 1. High compute stability (+15)
        # If variance is extremely low, it's likely a bot
        if features.get("compute_stability", 0) > 0.98 and features.get("sequence_length", 0) > 3:
            score += 15
            
        # 2. High Jaccard Similarity (+15)
        # Identical or near-identical instruction sets across many transactions
        if features.get("avg_jaccard", 0) > 0.9 and features.get("sequence_length", 0) > 3:
            score += 15
            
        # 3. Single Program Focus (+10)
        if features.get("unique_programs", 0) == 1 and features.get("sequence_length", 0) > 5:
            score += 10

        # 4. Noise Simulation Detection (+10)
        if features.get("is_noise_simulation", False):
            score += 10
            
        # Cap score based on mode (TZ requirement)
        if mode == Mode.MAINNET:
            return min(score, 20)
        return min(score, 30)
