"""
Per-instrument profiles and broker-symbol alias resolution.

Centralises instrument-specific behaviour (Gold, crypto, FX majors) so the
risk engine, market-data resolver and scalping engine agree on:
- aliases for the same underlying instrument (XAUUSD / GOLD / XAUUSD.m ...)
- minimum confluence score and spread ceilings
- ATR-based stop-loss calibration and preferred reward-to-risk
- maximum simultaneous positions on the instrument

The registry is intentionally dependency-free so it can be imported from any
layer without creating cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import json
import threading

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "instruments.json"

# Suffixes brokers commonly append/prepend to the canonical instrument name.
_COMMON_SUFFIXES = (
    ".m", ".std", ".pro", ".ecn", "_ecn", ".raw", ".a", ".r", ".c", ".i",
    "m", "+", "#", ".x", ".cash",
)


def _normalise_root(symbol: str) -> str:
    """Strips broker suffixes and separators to compare instruments."""
    s = str(symbol).upper().strip()
    for sep in ("/", "-", "_"):
        if sep in s and sep != "_":
            s = s.replace(sep, "")
    # Remove a trailing broker suffix (longest first).
    for sfx in sorted(_COMMON_SUFFIXES, key=len, reverse=True):
        if s.endswith(sfx.upper()) and len(s) > len(sfx):
            s = s[: -len(sfx)]
            break
    return s


@dataclass
class InstrumentProfile:
    canonical: str
    aliases: List[str] = field(default_factory=list)
    asset_class: str = "FX"
    pip_size: float = 0.0001
    point: float = 0.00001
    min_confidence: float = 45.0
    max_spread_points: float = 2000.0
    max_spread_pct_of_sl: float = 20.0
    atr_sl_min_multiplier: float = 0.8
    atr_sl_max_multiplier: float = 3.0
    target_rr: float = 1.5
    max_positions_per_symbol: int = 0
    preferred_timeframes: List[str] = field(default_factory=list)
    notes: str = ""

    def matches(self, symbol: str) -> bool:
        sym = str(symbol).upper()
        if sym == self.canonical.upper():
            return True
        if sym in {a.upper() for a in self.aliases}:
            return True
        return _normalise_root(sym) == _normalise_root(self.canonical)


class InstrumentRegistry:
    def __init__(self, profiles: Optional[Dict[str, InstrumentProfile]] = None):
        self._profiles: Dict[str, InstrumentProfile] = profiles or {}
        self._lock = threading.Lock()
        self._alias_index: Dict[str, str] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._alias_index = {}
        for canonical, profile in self._profiles.items():
            keys = [canonical, profile.canonical, *profile.aliases]
            for key in keys:
                if key:
                    self._alias_index[str(key).upper()] = canonical
                    self._alias_index[_normalise_root(str(key))] = canonical

    @classmethod
    def from_file(cls, path: Optional[Path] = None) -> "InstrumentRegistry":
        cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not cfg_path.exists():
            return cls(default_profiles())
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            items = raw.get("instruments", raw)
            profiles: Dict[str, InstrumentProfile] = {}
            for canonical, data in items.items():
                data = dict(data or {})
                data.setdefault("canonical", canonical)
                profiles[canonical] = InstrumentProfile(**{
                    k: v for k, v in data.items() if k in InstrumentProfile.__dataclass_fields__
                })
            if not profiles:
                return cls(default_profiles())
            return cls(profiles)
        except Exception:
            return cls(default_profiles())

    def get(self, symbol: str) -> InstrumentProfile:
        sym = str(symbol).upper()
        with self._lock:
            canonical = self._alias_index.get(sym) or self._alias_index.get(_normalise_root(sym))
        if canonical and canonical in self._profiles:
            return self._profiles[canonical]
        # Fall back to a direct prefix match (e.g. XAUUSD.micro).
        root = _normalise_root(sym)
        for canonical, profile in self._profiles.items():
            if root.startswith(profile.canonical.upper()):
                return profile
        return InstrumentProfile(canonical=sym)

    def aliases_for(self, symbol: str) -> List[str]:
        profile = self.get(symbol)
        out: List[str] = [str(symbol)]
        for candidate in [profile.canonical, *profile.aliases]:
            if candidate and candidate not in out:
                out.append(candidate)
        # Common broker suffix variants for the canonical root.
        for suffix in (".m", "m", ".pro", ".ecn", ".std", ".raw", ".a", ""):
            candidate = f"{profile.canonical}{suffix}"
            if candidate not in out:
                out.append(candidate)
        return out

    def all_profiles(self) -> Dict[str, InstrumentProfile]:
        return dict(self._profiles)


def default_profiles() -> Dict[str, InstrumentProfile]:
    return {
        "XAUUSD": InstrumentProfile(
            canonical="XAUUSD",
            aliases=["XAUUSD.m", "XAUUSDm", "XAUUSD.micro", "GOLD", "GOLD.m", "GOLDm", "XAU/USD"],
            asset_class="METALS",
            pip_size=0.1,
            point=0.01,
            min_confidence=45.0,
            max_spread_points=2000.0,
            max_spread_pct_of_sl=35.0,
            atr_sl_min_multiplier=1.2,
            atr_sl_max_multiplier=4.0,
            target_rr=1.8,
            max_positions_per_symbol=1,
            preferred_timeframes=["M1", "M5", "M15"],
            notes="Gold requires wider ATR-calibrated stops to survive micro-noise.",
        ),
        "BTCUSD": InstrumentProfile(
            canonical="BTCUSD",
            aliases=["BTCUSD.m", "BTCUSDm", "BTCUSD.micro", "BTC/USD"],
            asset_class="CRYPTO",
            pip_size=1.0,
            point=0.01,
            min_confidence=50.0,
            max_spread_points=2000.0,
            max_spread_pct_of_sl=25.0,
            atr_sl_min_multiplier=1.0,
            atr_sl_max_multiplier=3.5,
            target_rr=1.8,
            max_positions_per_symbol=1,
            preferred_timeframes=["M1", "M5", "M15"],
            notes="Crypto spread and slippage are wide; require stronger confluence.",
        ),
        "EURUSD": InstrumentProfile(
            canonical="EURUSD",
            aliases=["EURUSD.m", "EURUSDm", "EUR/USD"],
            asset_class="FX_MAJOR",
            pip_size=0.0001,
            point=0.00001,
            min_confidence=45.0,
            max_spread_points=300.0,
            max_spread_pct_of_sl=20.0,
            atr_sl_min_multiplier=0.8,
            atr_sl_max_multiplier=3.0,
            target_rr=1.5,
            max_positions_per_symbol=1,
            preferred_timeframes=["M1", "M5", "M15"],
        ),
        "GBPUSD": InstrumentProfile(
            canonical="GBPUSD",
            aliases=["GBPUSD.m", "GBPUSDm", "GBP/USD"],
            asset_class="FX_MAJOR",
            pip_size=0.0001,
            point=0.00001,
            min_confidence=45.0,
            max_spread_points=400.0,
            max_spread_pct_of_sl=20.0,
            atr_sl_min_multiplier=0.8,
            atr_sl_max_multiplier=3.2,
            target_rr=1.5,
            max_positions_per_symbol=1,
            preferred_timeframes=["M1", "M5", "M15"],
        ),
        "USDJPY": InstrumentProfile(
            canonical="USDJPY",
            aliases=["USDJPY.m", "USDJPYm", "USD/JPY"],
            asset_class="FX_MAJOR",
            pip_size=0.01,
            point=0.001,
            min_confidence=45.0,
            max_spread_points=300.0,
            max_spread_pct_of_sl=20.0,
            atr_sl_min_multiplier=0.8,
            atr_sl_max_multiplier=3.0,
            target_rr=1.5,
            max_positions_per_symbol=1,
            preferred_timeframes=["M1", "M5", "M15"],
        ),
    }


_REGISTRY: Optional[InstrumentRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> InstrumentRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = InstrumentRegistry.from_file()
        return _REGISTRY


def get_instrument_profile(symbol: str) -> InstrumentProfile:
    return get_registry().get(symbol)


def instrument_aliases(symbol: str) -> List[str]:
    return get_registry().aliases_for(symbol)


def is_gold(symbol: str) -> bool:
    profile = get_instrument_profile(symbol)
    return profile.asset_class == "METALS" and "XAU" in profile.canonical.upper()


def reset_registry() -> None:
    """Testing helper: forces the next call to reload config/instruments.json."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None
