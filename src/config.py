from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class AppConfig:
    appendPlus4: bool
    appendUnitLabel: bool
    countryCode: str
    citySuggestion: bool
    useCensusToFillEmptyZIPs: bool
    advancedMode: bool
    noSkip4: bool

_CFG: Optional[AppConfig] = None

def set_config(cfg: AppConfig) -> None:
    global _CFG
    _CFG = cfg  # set once at start (or in child initializer)

def get_config() -> AppConfig:
    if _CFG is None:
        raise RuntimeError("Config not initialized yet")
    return _CFG
