from pydantic_settings import BaseSettings
from app.core.models import Mode

class Settings(BaseSettings):
    MONGODB_URL: str = "mongodb://localhost:27017"
    DB_RAW_NAME: str = "Rawbd"
    DB_VERIFIED_NAME: str = "Clearbd"
    SECRET_KEY: str = "default_secret_key"
    API_DEBUG: bool = False
    SOLANA_RPC_URL_MAINNET: str = "https://api.mainnet-beta.solana.com"
    SOLANA_RPC_URL_TESTNET: str = "https://api.testnet.solana.com"
    SOLANA_RPC_URL_DEVNET: str = "https://api.devnet.solana.com"

    HELIUS_API_URL_MAINNET: str = "https://api.helius.xyz/v0"
    HELIUS_API_URL_DEVNET: str = "https://api-devnet.helius.xyz/v0"
    HELIUS_API_KEY: str = "5e854bd6-0079-467a-8701-46e20058eb45"
    
    # TTL settings (in days)
    TTL_TESTNET: int = 14
    TTL_MAINNET: int = 60
    TTL_DEVNET: int = 7
    
    # Weights for Testnet / Devnet
    WEIGHT_TIMING_TESTNET: int = 30
    WEIGHT_GRAPH_TESTNET: int = 40
    WEIGHT_BEHAVIOR_TESTNET: int = 30
    WEIGHT_ECONOMIC_TESTNET: int = 0
    
    # Weights for Mainnet
    WEIGHT_TIMING_MAINNET: int = 20
    WEIGHT_GRAPH_MAINNET: int = 30
    WEIGHT_BEHAVIOR_MAINNET: int = 20
    WEIGHT_ECONOMIC_MAINNET: int = 30

    # Known CEX Addresses
    CEX_ADDRESSES: list[str] = [
        "AC57B9ZkgqG9N6WpSthGfBNoS4uTpx5tD5K8k8N3p6V", # Binance
        "H886S8pCqH8Nrk9C58742XG5XG5XG5XG",           # Coinbase
        "5VCwS7BRoVw78W6Cq66A7BwzV2X9p4Z5G",          # Kraken
        "6dnT97YdYV6hS2uC7Y6F4M5L9F4G5H6J",          # OKX
        "9uS7BRoVw78W6Cq66A7BwzV2X9p4Z5G5H",          # Bybit
        "8uS7BRoVw78W6Cq66A7BwzV2X9p4Z5G5H",          # Huobi
        "7uS7BRoVw78W6Cq66A7BwzV2X9p4Z5G5H"           # KuCoin
    ]

    class Config:
        env_file = ".env"

settings = Settings()
