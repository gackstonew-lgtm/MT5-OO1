"""
Unit tests for Champion / Challenger Statistical Gating and Promotion.
"""

import unittest
from datetime import datetime, timezone
from src.learning.champion_challenger import ChampionChallengerEngine, StrategyCandidate
from src.learning.performance_tracker import TradeRecord


class TestChampionChallenger(unittest.TestCase):
    def setUp(self):
        self.champ = StrategyCandidate(
            id="CHAMP", name="Champion V1", version="1.0", parameters={}, is_champion=True
        )
        self.chal = StrategyCandidate(
            id="CHAL", name="Challenger V2", version="2.0", parameters={}, is_champion=False
        )
        self.engine = ChampionChallengerEngine(
            champion=self.champ,
            challenger=self.chal,
            min_sample_size=10,
            min_profit_factor_diff=0.2
        )
        self.now = datetime.now(timezone.utc)

    def test_sample_size_gating(self):
        # Challenger only has 5 trades (< 10 threshold) -> must NOT qualify
        for i in range(5):
            self.engine.record_challenger_trade(
                TradeRecord(ticket=i, symbol="EURUSD", direction="BUY", setup_type="OTE",
                            session="LONDON", regime="NORMAL", entry_price=1.08, exit_price=1.09,
                            volume=0.1, profit=100.0, r_multiple=2.0, entry_time=self.now, exit_time=self.now)
            )

        qualified, report, _ = self.engine.evaluate_challenger_promotion()
        self.assertFalse(qualified)
        self.assertIn("below mandatory statistical threshold", report)

    def test_promotion_requires_explicit_authorization(self):
        # Record 15 profitable trades for challenger
        for i in range(15):
            self.engine.record_challenger_trade(
                TradeRecord(ticket=i, symbol="EURUSD", direction="BUY", setup_type="OTE",
                            session="LONDON", regime="NORMAL", entry_price=1.08, exit_price=1.09,
                            volume=0.1, profit=100.0, r_multiple=2.0, entry_time=self.now, exit_time=self.now)
            )

        # Qualifies, but promotion requires token
        self.assertFalse(self.engine.promote_challenger_to_champion("INVALID_CODE"))
        self.assertTrue(self.engine.promote_challenger_to_champion("AUTHORIZE_PROMOTION"))
        self.assertEqual(self.engine.champion.name, "Challenger V2")


if __name__ == "__main__":
    unittest.main()
