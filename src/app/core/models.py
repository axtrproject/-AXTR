from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from enum import Enum
import time

class Mode(str, Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"
    DEVNET = "devnet"

class Evidence(BaseModel):
    clusters: List[str] = Field(default_factory=list)
    shared_funders: List[str] = Field(default_factory=list)
    programs: List[str] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)
    last_updated: int = Field(default_factory=lambda: int(time.time()))

class ScoringFeatures(BaseModel):
    timing: Dict = Field(default_factory=dict)
    graph: Dict = Field(default_factory=dict)
    behavior: Dict = Field(default_factory=dict)
    economic: Dict = Field(default_factory=dict)
    alerts: List[str] = Field(default_factory=list) # Critical alerts found
    evidence: Evidence = Field(default_factory=Evidence)

class Scores(BaseModel):
    total: int = 0
    timing: int = 0
    graph: int = 0
    behavior: int = 0
    economic: int = 0

class HistoryItem(BaseModel):
    ts: int
    total: int
    timing: int = 0
    graph: int = 0
    behavior: int = 0
    economic: int = 0

class UserVerified(BaseModel):
    id: Optional[int] = Field(None, alias="_id")
    wallet: str = Field(..., pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
    first_seen: int
    last_seen: int
    mode: Mode
    features: ScoringFeatures
    scores: Scores
    confidence: float
    labels: List[str] = Field(default_factory=list)
    profile: str = "Unknown" # e.g., "MEV Bot", "HFT Trader", "Organic User", "Sybil Farmer"
    history: List[HistoryItem] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list) # Structured codes like "G01", "B02"

class RawTransaction(BaseModel):
    signature: str
    block_time: int
    slot: int
    fee: int
    status: str
    processed: bool = False
    mode: Mode
    fee_payer: Optional[str] = None
    amount_change: int = 0 # Net SOL change for the wallet
    compute_units: int = 0 # Added for behavior analysis

class RawInstruction(BaseModel):
    wallet: str # Wallet that performed the instruction
    tx_signature: str
    program_id: str
    instruction_type: str = "unknown"
    data: str
    accounts: List[str] = Field(default_factory=list)
    processed: bool = False
    mode: Mode

class RawAccount(BaseModel):
    address: str
    owner: str
    lamports: int
    executable: bool
    processed: bool = False
    mode: Mode
