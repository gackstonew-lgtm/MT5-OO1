"""
Trading Session and Kill Zone Engine.
Manages Asian, London, and New York sessions with Dual Profiles:
1. 'ist_reference': Static UTC profile matching Indian Standard Time (IST = UTC+5:30) reference windows:
   - ASIA: 00:00 - 09:00 UTC (05:30 - 14:30 IST)
   - LONDON: 06:00 - 12:00 UTC (11:30 - 17:30 IST)
   - NEW YORK: 12:30 - 17:30 UTC (18:00 - 23:00 IST)
   - BEST WINDOW: 12:30 - 17:30 UTC (18:00 - 23:00 IST)
2. 'market_local_dst': Dynamic market hours using zoneinfo (Europe/London, America/New_York)
   with automatic Daylight Saving Time (DST) adaptation.

Includes:
- Overlap detection (London / NY Overlap)
- Handover cooldowns and late-session entry restrictions
- Weekend, Monday open skip, Friday cutoff, and Rollover/Swap blackout windows
- Strict fail-safe when broker UTC offset is unverified
"""

from __future__ import annotations
import json
import os
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import List, Optional, Tuple, Dict, Any

from src.core.contracts import SessionType, Candle, ActiveSessionInfo
from src.core.config import SessionConfig


@dataclass
class SessionState:
    current_session: SessionType
    is_in_killzone: bool
    active_killzone_name: Optional[str]
    session_high: float
    session_low: float
    session_open: float


class TradingSessionEngine:
    def __init__(
        self,
        config: Optional[SessionConfig] = None,
        config_path: str = "config/sessions.json"
    ):
        self.config = config or SessionConfig()
        self.config_path = config_path
        self._raw_cfg = self._load_json_config()
        self.active_profile = self._raw_cfg.get("active_profile", "ist_reference")
        self.require_verified_offset = self._raw_cfg.get("require_verified_broker_offset", True)
        self._session_candles: List[Candle] = []

    def _load_json_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def reload_config(self) -> None:
        self._raw_cfg = self._load_json_config()
        self.active_profile = self._raw_cfg.get("active_profile", "ist_reference")
        self.require_verified_offset = self._raw_cfg.get("require_verified_broker_offset", True)

    def set_active_profile(self, profile_name: str) -> bool:
        if profile_name in ["ist_reference", "market_local_dst"]:
            self.active_profile = profile_name
            self._raw_cfg["active_profile"] = profile_name
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(self._raw_cfg, f, indent=2)
            except Exception:
                pass
            return True
        return False

    # ========================================================================
    # 1. CORE SESSION EVALUATION & OVERLAP DETECTION
    # ========================================================================

    def get_session_info(
        self,
        current_dt: Optional[datetime] = None,
        broker_offset_verified: bool = True
    ) -> ActiveSessionInfo:
        """
        Comprehensive session evaluation returning ActiveSessionInfo.
        Evaluates profile intervals, overlaps, transition blackouts, and broker offset fail-safe.
        """
        dt = current_dt or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

        weekday = dt.weekday()  # Monday=0, Sunday=6
        cur_time = dt.time()

        # Check Fail-Safe
        if self.require_verified_offset and not broker_offset_verified:
            return ActiveSessionInfo(
                primary_session=SessionType.OFF_HOURS,
                active_sessions=(SessionType.OFF_HOURS,),
                is_best_window=False,
                time_in_session_minutes=0.0,
                time_to_close_minutes=0.0,
                is_handover_cooldown=False,
                is_rollover_blackout=False,
                is_weekend=False,
                is_monday_open_skip=False,
                is_friday_cutoff=False,
                is_cash_open_blackout=False,
                active_profile=self.active_profile,
                broker_offset_verified=False,
                can_trade=False,
                rejection_reason="Broker UTC offset unknown / unverified - fail-safe active"
            )

        # Transitions config
        transitions = self._raw_cfg.get("transitions", {})
        rollover_cfg = transitions.get("rollover_blackout", {"enabled": True, "start_utc": "21:55", "end_utc": "22:15"})
        mon_skip_min = transitions.get("monday_open_skip_minutes", 30)
        fri_cutoff_str = transitions.get("friday_cutoff_utc", "20:00")
        handover_min = transitions.get("handover_cooldown_minutes", 15)
        late_session_min = transitions.get("late_session_no_entry_minutes", 20)

        # 1. Weekend Check (Friday 20:00 UTC through Sunday 22:00 UTC)
        fri_h, fri_m = [int(x) for x in fri_cutoff_str.split(":")]
        is_weekend = False
        is_friday_cutoff = False
        if weekday == 4 and cur_time >= time(fri_h, fri_m):
            is_weekend = True
            is_friday_cutoff = True
        elif weekday == 5:  # Saturday
            is_weekend = True
        elif weekday == 6 and cur_time < time(22, 0):  # Sunday before market open
            is_weekend = True

        if is_weekend:
            return ActiveSessionInfo(
                primary_session=SessionType.WEEKEND,
                active_sessions=(SessionType.WEEKEND,),
                is_best_window=False,
                time_in_session_minutes=0.0,
                time_to_close_minutes=0.0,
                is_handover_cooldown=False,
                is_rollover_blackout=False,
                is_weekend=True,
                is_monday_open_skip=False,
                is_friday_cutoff=is_friday_cutoff,
                is_cash_open_blackout=False,
                active_profile=self.active_profile,
                broker_offset_verified=True,
                can_trade=False,
                rejection_reason="Market closed: Weekend hours"
            )

        # 2. Monday Open Skip
        is_monday_open = (weekday == 0 and cur_time < time(0, mon_skip_min))
        if is_monday_open:
            return ActiveSessionInfo(
                primary_session=SessionType.OFF_HOURS,
                active_sessions=(SessionType.OFF_HOURS,),
                is_best_window=False,
                time_in_session_minutes=cur_time.minute,
                time_to_close_minutes=mon_skip_min - cur_time.minute,
                is_handover_cooldown=True,
                is_rollover_blackout=False,
                is_weekend=False,
                is_monday_open_skip=True,
                is_friday_cutoff=False,
                is_cash_open_blackout=False,
                active_profile=self.active_profile,
                broker_offset_verified=True,
                can_trade=False,
                rejection_reason=f"Monday market open volatility buffer ({mon_skip_min}m skip active)"
            )

        # 3. Daily Rollover / Swap Window (~21:55 - 22:15 UTC)
        is_rollover = False
        if rollover_cfg.get("enabled", True):
            r_sh, r_sm = [int(x) for x in rollover_cfg.get("start_utc", "21:55").split(":")]
            r_eh, r_em = [int(x) for x in rollover_cfg.get("end_utc", "22:15").split(":")]
            if time(r_sh, r_sm) <= cur_time <= time(r_eh, r_em):
                is_rollover = True

        if is_rollover:
            return ActiveSessionInfo(
                primary_session=SessionType.ROLLOVER_BLACKOUT,
                active_sessions=(SessionType.ROLLOVER_BLACKOUT,),
                is_best_window=False,
                time_in_session_minutes=0.0,
                time_to_close_minutes=0.0,
                is_handover_cooldown=False,
                is_rollover_blackout=True,
                is_weekend=False,
                is_monday_open_skip=False,
                is_friday_cutoff=False,
                is_cash_open_blackout=False,
                active_profile=self.active_profile,
                broker_offset_verified=True,
                can_trade=False,
                rejection_reason="Daily swap rollover blackout active (wide spreads / illiquidity)"
            )

        # 4. Profile Session Intervals
        active_list: List[SessionType] = []
        is_best_window = False
        time_in_session = 0.0
        time_to_close = 0.0
        is_handover = False

        if self.active_profile == "market_local_dst":
            # Dynamic DST-aware evaluation via zoneinfo
            active_list, is_best_window, time_in_session, time_to_close, is_handover = self._eval_local_dst_profile(dt, handover_min)
        else:
            # Default 'ist_reference' profile
            active_list, is_best_window, time_in_session, time_to_close, is_handover = self._eval_ist_reference_profile(dt, handover_min)

        # Determine Primary Session
        if not active_list:
            primary = SessionType.OFF_HOURS
            can_trade = False
            rejection = "Outside active trading sessions (Off-Hours)"
        else:
            if SessionType.LONDON_NY_OVERLAP in active_list:
                primary = SessionType.LONDON_NY_OVERLAP
            elif is_best_window:
                primary = SessionType.BEST_WINDOW
            elif SessionType.LONDON in active_list:
                primary = SessionType.LONDON
            elif SessionType.NEW_YORK in active_list:
                primary = SessionType.NEW_YORK
            elif SessionType.ASIAN in active_list:
                primary = SessionType.ASIAN
            else:
                primary = active_list[0]

            can_trade = True
            rejection = None

        # Check late-session entry restriction
        if can_trade and time_to_close > 0 and time_to_close <= late_session_min:
            rejection = f"Late session cutoff ({time_to_close:.1f}m to close <= {late_session_min}m limit)"
            # Trade entry blocked, but existing management permitted

        # Check handover cooldown
        if is_handover:
            rejection = f"Session handover cooldown active ({handover_min}m transition buffer)"

        return ActiveSessionInfo(
            primary_session=primary,
            active_sessions=tuple(active_list),
            is_best_window=is_best_window,
            time_in_session_minutes=round(time_in_session, 1),
            time_to_close_minutes=round(time_to_close, 1),
            is_handover_cooldown=is_handover,
            is_rollover_blackout=False,
            is_weekend=False,
            is_monday_open_skip=False,
            is_friday_cutoff=False,
            is_cash_open_blackout=False,
            active_profile=self.active_profile,
            broker_offset_verified=True,
            can_trade=(can_trade and not is_handover and (time_to_close > late_session_min or time_to_close == 0.0)),
            rejection_reason=rejection
        )

    def _eval_ist_reference_profile(
        self,
        dt_utc: datetime,
        handover_min: int
    ) -> Tuple[List[SessionType], bool, float, float, bool]:
        """
        Evaluates session membership under 'ist_reference':
        - ASIA: 00:00 - 09:00 UTC (05:30 - 14:30 IST)
        - LONDON: 06:00 - 12:00 UTC (11:30 - 17:30 IST)
        - NEW YORK: 12:30 - 17:30 UTC (18:00 - 23:00 IST)
        - BEST WINDOW: 12:30 - 17:30 UTC (18:00 - 23:00 IST)
        """
        ct = dt_utc.time()
        cur_min = ct.hour * 60 + ct.minute
        active: List[SessionType] = []
        is_best = False
        time_in = 0.0
        time_to_close = 0.0
        is_handover = False

        # ASIA: 00:00 (0m) to 09:00 (540m)
        if 0 <= cur_min < 540:
            active.append(SessionType.ASIAN)
            time_in = float(cur_min)
            time_to_close = float(540 - cur_min)
            if cur_min < handover_min:
                is_handover = True

        # LONDON: 06:00 (360m) to 12:00 (720m)
        if 360 <= cur_min < 720:
            active.append(SessionType.LONDON)
            time_in = float(cur_min - 360)
            time_to_close = float(720 - cur_min)
            if cur_min - 360 < handover_min:
                is_handover = True

        # NEW YORK & BEST WINDOW: 12:30 (750m) to 17:30 (1050m)
        if 750 <= cur_min < 1050:
            active.append(SessionType.NEW_YORK)
            active.append(SessionType.BEST_WINDOW)
            is_best = True
            time_in = float(cur_min - 750)
            time_to_close = float(1050 - cur_min)
            if cur_min - 750 < handover_min:
                is_handover = True

        # OVERLAP: If both London and NY overlap
        if SessionType.LONDON in active and SessionType.NEW_YORK in active:
            active.append(SessionType.LONDON_NY_OVERLAP)

        return active, is_best, time_in, time_to_close, is_handover

    @staticmethod
    def _get_london_offset_hours(dt_utc: datetime) -> int:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo("Europe/London")
            loc = dt_utc.astimezone(tz)
            offset = loc.utcoffset()
            if offset is not None:
                return int(offset.total_seconds() // 3600)
        except Exception:
            pass
        # Fallback to deterministic UK BST rule:
        # BST begins last Sunday in March at 01:00 UTC, ends last Sunday in October at 01:00 UTC
        year = dt_utc.year
        last_sun_mar = 31 - ((datetime(year, 3, 31).weekday() + 1) % 7)
        dst_start = datetime(year, 3, last_sun_mar, 1, 0, tzinfo=timezone.utc)
        last_sun_oct = 31 - ((datetime(year, 10, 31).weekday() + 1) % 7)
        dst_end = datetime(year, 10, last_sun_oct, 1, 0, tzinfo=timezone.utc)
        return 1 if dst_start <= dt_utc < dst_end else 0

    @staticmethod
    def _get_new_york_offset_hours(dt_utc: datetime) -> int:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo("America/New_York")
            loc = dt_utc.astimezone(tz)
            offset = loc.utcoffset()
            if offset is not None:
                return int(offset.total_seconds() // 3600)
        except Exception:
            pass
        # Fallback to deterministic US EDT rule:
        # Second Sunday in March at 07:00 UTC to First Sunday in November at 06:00 UTC
        year = dt_utc.year
        first_sun_mar = 1 + (6 - datetime(year, 3, 1).weekday()) % 7
        second_sun_mar = first_sun_mar + 7
        dst_start = datetime(year, 3, second_sun_mar, 7, 0, tzinfo=timezone.utc)
        first_sun_nov = 1 + (6 - datetime(year, 11, 1).weekday()) % 7
        dst_end = datetime(year, 11, first_sun_nov, 6, 0, tzinfo=timezone.utc)
        return -4 if dst_start <= dt_utc < dst_end else -5

    def _eval_local_dst_profile(
        self,
        dt_utc: datetime,
        handover_min: int
    ) -> Tuple[List[SessionType], bool, float, float, bool]:
        """
        Evaluates dynamic local market hours:
        - London: 08:00 - 16:30 local (BST/GMT aware)
        - New York: 08:00 - 17:00 local (EDT/EST aware)
        - Asia: 00:00 - 09:00 UTC
        - Overlap / Best Window: When both London and NY local sessions are open.
        """
        active: List[SessionType] = []
        is_best = False
        time_in = 0.0
        time_to_close = 0.0
        is_handover = False

        # 1. London local
        lon_offset = self._get_london_offset_hours(dt_utc)
        lon_dt = dt_utc + timedelta(hours=lon_offset)
        lon_min = lon_dt.hour * 60 + lon_dt.minute
        # 08:00 (480m) to 16:30 (990m)
        is_lon = (480 <= lon_min < 990)
        if is_lon:
            active.append(SessionType.LONDON)
            time_in = float(lon_min - 480)
            time_to_close = float(990 - lon_min)
            if lon_min - 480 < handover_min:
                is_handover = True

        # 2. New York local
        ny_offset = self._get_new_york_offset_hours(dt_utc)
        ny_dt = dt_utc + timedelta(hours=ny_offset)
        ny_min = ny_dt.hour * 60 + ny_dt.minute
        # 08:00 (480m) to 17:00 (1020m)
        is_ny = (480 <= ny_min < 1020)
        if is_ny:
            active.append(SessionType.NEW_YORK)
            if not is_lon:
                time_in = float(ny_min - 480)
                time_to_close = float(1020 - ny_min)
            if ny_min - 480 < handover_min:
                is_handover = True

        # 3. Asia UTC (00:00 to 09:00 UTC)
        utc_min = dt_utc.hour * 60 + dt_utc.minute
        if 0 <= utc_min < 540:
            active.append(SessionType.ASIAN)
            if not is_lon and not is_ny:
                time_in = float(utc_min)
                time_to_close = float(540 - utc_min)
                if utc_min < handover_min:
                    is_handover = True

        # 4. Overlap & Best Window
        if is_lon and is_ny:
            active.append(SessionType.LONDON_NY_OVERLAP)
            active.append(SessionType.BEST_WINDOW)
            is_best = True
            time_in = float(ny_min - 480) # Time into overlap
            time_to_close = float(990 - lon_min) # Time until London closes

        return active, is_best, time_in, time_to_close, is_handover

    # ========================================================================
    # 2. BACKWARD COMPATIBLE CONVENIENCE METHODS
    # ========================================================================

    def get_current_session(self, current_dt: Optional[datetime] = None) -> SessionType:
        """
        Determines current active primary session.
        Maintains 100% backward compatibility with existing callers.
        """
        info = self.get_session_info(current_dt, broker_offset_verified=True)
        return info.primary_session

    def evaluate_killzone(self, current_dt: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        """
        Checks if current time falls within institutional Kill Zones.
        Maintains 100% backward compatibility.
        """
        dt = current_dt or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ct = dt.time()

        # London Kill Zone (07:00 - 10:30 UTC)
        if time(7, 0) <= ct <= time(10, 30):
            return True, "LONDON_KILL_ZONE"
        # New York AM Kill Zone (13:00 - 16:00 UTC)
        elif time(13, 0) <= ct <= time(16, 0):
            return True, "NEW_YORK_KILL_ZONE"

        # Best window is only evaluated when session upgrade layer is enabled
        if self._raw_cfg.get("enabled", False):
            info = self.get_session_info(dt, broker_offset_verified=True)
            if info.is_best_window:
                return True, "BEST_WINDOW_ZONE"

        return False, None

    def calculate_session_levels(self, candles: List[Candle]) -> Tuple[float, float, float]:
        """
        Calculates High, Low, Open for the session candles.
        """
        if not candles:
            return 0.0, 0.0, 0.0
        session_high = max(c.high for c in candles)
        session_low = min(c.low for c in candles)
        session_open = candles[0].open
        return session_high, session_low, session_open
