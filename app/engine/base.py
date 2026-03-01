from abc import ABC, abstractmethod
from typing import List, Dict
from app.core.models import RawTransaction, Mode

class BaseFeatureEngine(ABC):
    @abstractmethod
    async def extract(self, wallet: str, transactions: List[RawTransaction], mode: Mode) -> Dict:
        pass

    @abstractmethod
    def score(self, features: Dict, mode: Mode) -> int:
        pass
