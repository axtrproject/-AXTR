 AXTR - Action-based X-chain Trust Research

On-chain Risk & Reputation Infrastructure for Solana Protocols.
AXTR is a modular scoring engine that evaluates wallet behavior on Solana using behavioral, timing, and cluster-based analysis.
It provides controlled API access via scoped 64-bit keys for both internal and B2B integrations.

What AXTR Solves:
Solana protocols face:
Sybil clusters
Airdrop farming
Mint bots
Coordinated wallet behavior
Unfair token distribution
AXTR detects risk patterns before distribution or mint execution.

Core Components:
Scoring Engine (behavioral + structural signals)
Key-based API access layer
Public web interface (wallet check)
B2B integration endpoints


Architecture
AXTR follows a modular infrastructure design:

   Clien                                
     ↓                                  
   API Gateway (Key Validation Layer)  
     ↓                                  
   Feature Extraction                   
     ↓                                 
   Scoring Engine                       
     ↓                                 
   Risk Score + Confidence + Flags      



Access Model

AXTR uses scoped 64-bit API keys:
Private B2B keys (project integrations)
Technical internal keys (web interface)
Controlled endpoint access
Rate-limited and permission-scoped
This allows secure integration without exposing the scoring logic.


Example API Request
curl -H "X-API-Token: <project_key>" \
"http://127.0.0.1:9001/score?wallet=97kZj95tGfSYE6uiHTkyM6YMgt9i7MmsmsZB9jb6eoC8&mode=mainnet"


Example API Responsе
{
  "wallet": "97kZj95tGfSYE6uiHTkyM6YMgt9i7MmsmsZB9jb6eoC8",
  "score": 33,
  "confidence": 0.33,
  "risk_level": "medium",
  "reason_codes": ["B01"]
}


Internal Feature Model (Simplified)

AXTR evaluates:
Timing entropy & burst behavior
Graph cluster structure
Program interaction similarity
Economic ROI stability
Compute pattern stability
Raw features are abstracted from public API responses.

Use Cases
NFT mint protection
Fair airdrop eligibility filtering
Launchpad anti-Sybil defense
DeFi farming abuse detection
Wallet risk scoring for Web3 platforms

Current Status
6,000+ wallets analyzed (devnet + mainnet)
Production scoring engine active
MongoDB optimized with indexing
Private API access model implemented
Open for pilot integrations

Why AXTR???
Behavior-first detection (not balance-based)
Cluster-aware analysis
Confidence-weighted scoring
Designed for protocol-level integration
