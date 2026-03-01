import time
from typing import List, Dict, Optional
from app.core.models import RawTransaction, Mode, Scores, ScoringFeatures, UserVerified, Evidence
from app.engine.timing import TimingEngine
from app.engine.graph import GraphEngine
from app.engine.behavior import BehaviorEngine
from app.engine.economic import EconomicEngine

class ScoringEngine:
    def __init__(self):
        self.timing_engine = TimingEngine()
        self.graph_engine = GraphEngine()
        self.behavior_engine = BehaviorEngine()
        self.economic_engine = EconomicEngine()

    async def analyze(self, wallet: str, transactions: List[RawTransaction], mode: Mode, previous_user: Optional[UserVerified] = None) -> Dict:
        # Extract features from all engines
        timing_features = await self.timing_engine.extract(wallet, transactions, mode)
        graph_features = await self.graph_engine.extract(wallet, transactions, mode)
        behavior_features = await self.behavior_engine.extract(wallet, transactions, mode)
        economic_features = await self.economic_engine.extract(wallet, transactions, mode)

        # Calculate individual current scores
        curr_timing = self.timing_engine.score(timing_features, mode)
        curr_graph = self.graph_engine.score(graph_features, mode)
        curr_behavior = self.behavior_engine.score(behavior_features, mode)
        curr_economic = self.economic_engine.score(economic_features, mode)

        # Incremental Scoring with Memory (Decay)
        if previous_user:
            old_scores = previous_user.scores
            last_ts = previous_user.last_seen
            now = int(time.time())
            
            # Decay factor: 0.8 for memory, 0.2 for new signal (as per user instruction)
            # Time decay: if more than 30 days, memory fades faster
            days_passed = (now - last_ts) / 86400
            decay_multiplier = 0.8 * (0.95 ** min(days_passed, 30)) # Slow decay
            
            timing_score = int(old_scores.timing * decay_multiplier + curr_timing * (1 - decay_multiplier))
            graph_score = int(old_scores.graph * decay_multiplier + curr_graph * (1 - decay_multiplier))
            behavior_score = int(old_scores.behavior * decay_multiplier + curr_behavior * (1 - decay_multiplier))
            economic_score = int(old_scores.economic * decay_multiplier + curr_economic * (1 - decay_multiplier))
            
            # Evidence Accumulation
            evidence = previous_user.features.evidence
            evidence.last_updated = now
        else:
            timing_score = curr_timing
            graph_score = curr_graph
            behavior_score = curr_behavior
            economic_score = curr_economic
            evidence = Evidence(last_updated=int(time.time()))

        # Update Evidence Facts
        if graph_features.get("root_funder"):
            funder = graph_features["root_funder"]
            if funder != wallet and funder not in evidence.shared_funders:
                evidence.shared_funders.append(funder)
        
        # If no root_funder but direct_funder is different
        direct_funder = graph_features.get("shared_funder")
        if direct_funder and direct_funder != wallet and direct_funder not in evidence.shared_funders:
            evidence.shared_funders.append(direct_funder)
        
        # Accumulate unique programs
        if behavior_features.get("unique_programs_list"):
            for prog in behavior_features["unique_programs_list"]:
                if prog not in evidence.programs:
                    evidence.programs.append(prog)
        
        # Update Flags
        if behavior_features.get("compute_stability", 0) > 0.98:
            if "stable_compute" not in evidence.flags: evidence.flags.append("stable_compute")
        if graph_features.get("graph_depth", 1) >= 3:
            if "deep_obfuscation" not in evidence.flags: evidence.flags.append("deep_obfuscation")

        # Multipliers based on Evidence (Recognition & Reinforcement)
        multiplier = 1.0
        # REINFORCEMENT: If pattern repeats, boost multiplier
        pattern_repeat_count = 0
        if len(evidence.shared_funders) > 1: 
            multiplier *= 1.2 
            pattern_repeat_count += 1
        if "stable_compute" in evidence.flags and curr_behavior > 10: 
            multiplier *= 1.4 # Increased boost for stable compute reinforcement
            pattern_repeat_count += 1
        if len(evidence.programs) > 10:
            multiplier *= 1.1 # Diversified botting programs
        
        # Exponential reinforcement for long-lived patterns
        if previous_user and pattern_repeat_count > 0:
            # If we saw patterns before, they harden
            multiplier *= (1.1 ** min(len(previous_user.history), 5))

        # Alert Logic & Reason Codes: identify critical threats
        alerts = []
        reason_codes = []
        
        if graph_features.get("cluster_ratio", 0) > 0.8 and graph_features.get("cluster_size", 0) > 20:
            alerts.append("CRITICAL: Massive Bot Farm Cluster")
            reason_codes.append("G01") # Cluster detection
        if economic_features.get("zero_loss_variance", 999999) < 0.001 and len(transactions) > 10:
            # Distinction: check if it's HFT (Pro human) or MEV (Bot)
            if economic_features.get("is_high_frequency") and economic_features.get("mev_usage", 0) > 0:
                alerts.append("HIGH: Algorithmic MEV Bot")
                reason_codes.append("E01") # Zero-loss MEV
            else:
                # Pro human - don't add alert or reason code for zero-loss if it's not HFT+MEV
                pass
                
        if behavior_features.get("compute_stability", 0) > 0.99:
            alerts.append("MEDIUM: Highly Stable Compute Pattern")
            reason_codes.append("B01") # Stable compute
        if economic_features.get("cluster_roi_dispersion", 1.0) < 0.05:
            alerts.append("CRITICAL: Identical ROI Cluster Detected")
            reason_codes.append("E02") # Economic synchronization
        if graph_features.get("graph_depth", 1) >= 3:
            alerts.append(f"HIGH: Deep Layer Obfuscation (Depth {graph_features.get('graph_depth')})")
            reason_codes.append("G02") # Deep tracing
        if len(evidence.shared_funders) > 5:
            reason_codes.append("G03") # Many-to-one funding

        # Combine into scores model
        total_score = int((timing_score + graph_score + behavior_score + economic_score) * multiplier)
        scores = Scores(
            total=min(total_score, 100),
            timing=timing_score,
            graph=graph_score,
            behavior=behavior_score,
            economic=economic_score
        )

        # Profile Assignment (Context-Aware)
        profile = self.detect_profile(scores, behavior_features, economic_features, evidence)

        features = ScoringFeatures(
            timing=timing_features,
            graph=graph_features,
            behavior=behavior_features,
            economic=economic_features,
            alerts=alerts,
            evidence=evidence
        )

        # Calculate Non-linear Confidence
        confidence = self.calculate_confidence(scores, alerts, previous_user)

        return {
            "scores": scores,
            "features": features,
            "confidence": confidence,
            "profile": profile,
            "reason_codes": reason_codes
        }

    def detect_profile(self, scores: Scores, behavior: Dict, economic: Dict, evidence: Evidence) -> str:
        """
        Determines the use-case profile of the wallet.
        """
        if scores.total > 70:
            if economic.get("mev_usage", 0) > 0: return "MEV Bot"
            if behavior.get("compute_stability", 0) > 0.95: return "HFT Execution Bot"
            return "Sybil / Farm Bot"
        
        if scores.total > 30:
            if "stable_compute" in evidence.flags: return "Automated Farmer"
            return "Suspicious User"
            
        # Human categories
        if economic.get("total_volume", 0) > 1000 * 1e9: # > 1000 SOL
            return "Whale / Pro Trader"
        
        if len(evidence.programs) > 20:
            return "Active DApp User"
            
        return "Organic User"

    def calculate_confidence(self, scores: Scores, alerts: List[str], previous_user: Optional[UserVerified] = None) -> float:
        """
        Calculates non-linear confidence based on signal agreement, alerts and history.
        """
        # Base confidence from total score (0 to 1.0)
        base = min(scores.total / 100.0, 1.0)
        
        # Synergy Boost: If multiple engines have high scores
        high_signals = 0
        if scores.timing > 15: high_signals += 1
        if scores.graph > 20: high_signals += 1
        if scores.behavior > 15: high_signals += 1
        if scores.economic > 20: high_signals += 1
        
        boost = 0.0
        if high_signals >= 3:
            boost = 0.2 # Strong synergy
        elif high_signals == 2:
            boost = 0.1 # Moderate synergy
            
        # Alert Penalty/Boost
        alert_impact = len([a for a in alerts if "CRITICAL" in a]) * 0.15
        alert_impact += len([a for a in alerts if "HIGH" in a]) * 0.05
        
        # Memory Confidence Boost: If we have seen this user before, confidence grows
        memory_boost = 0.0
        if previous_user:
            memory_boost = 0.1 # We are more sure because we have history
        
        confidence = base + boost + alert_impact + memory_boost
        
        # Inconsistency Penalty: if one score is very high and others are 0
        if scores.total > 40:
            sub_scores = [scores.timing, scores.graph, scores.behavior, scores.economic]
            active_engines = len([s for s in sub_scores if s > 0])
            if active_engines == 1:
                confidence *= 0.7 # Penalty for single-source signal
                
        return min(max(confidence, 0.0), 1.0)

    def interpret(self, total_score: int) -> str:
        if 0 <= total_score <= 15:
            return "Human (Organic)"
        elif 16 <= total_score <= 35:
            return "Suspicious User"
        elif 36 <= total_score <= 60:
            return "Bot / Automated"
        elif 61 <= total_score <= 85:
            return "Sybil"
        else:
            return "Critical Sybil / Cluster"
