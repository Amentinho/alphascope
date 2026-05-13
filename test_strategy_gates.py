import os
import tempfile
import unittest

os.environ.setdefault('EXECUTOR_DRY_RUN', 'true')

import simulation


class StrategyGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.old_db = simulation.SIM_DB
        self.old_seen = simulation.MIN_WATCHLIST_SEEN
        self.old_age = simulation.MIN_WATCHLIST_AGE_MIN
        simulation.SIM_DB = self.tmp.name
        simulation.MIN_WATCHLIST_SEEN = 2
        simulation.MIN_WATCHLIST_AGE_MIN = 60

    def tearDown(self):
        simulation.SIM_DB = self.old_db
        simulation.MIN_WATCHLIST_SEEN = self.old_seen
        simulation.MIN_WATCHLIST_AGE_MIN = self.old_age
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_watchlist_defers_first_sighting_then_allows_second(self):
        proposal = {
            'symbol': 'TEST',
            'chain': 'solana',
            'category': 'DEX_GEM',
            'coin_id': 'So11111111111111111111111111111111111111112',
            'alpha_score': 80,
        }
        ok1, reason1 = simulation._candidate_watchlist_gate(proposal, 1.00, {})
        ok2, reason2 = simulation._candidate_watchlist_gate(proposal, 1.01, {})

        self.assertFalse(ok1)
        self.assertIn('first sighting', reason1)
        self.assertTrue(ok2)
        self.assertIn('watchlist ready', reason2)

    def test_watchlist_rejects_candidate_that_declines(self):
        proposal = {
            'symbol': 'DROP',
            'chain': 'solana',
            'category': 'DEX_GEM',
            'coin_id': 'So11111111111111111111111111111111111111112',
            'alpha_score': 80,
        }
        simulation._candidate_watchlist_gate(proposal, 1.00, {})
        ok, reason = simulation._candidate_watchlist_gate(proposal, 0.95, {})

        self.assertFalse(ok)
        self.assertIn('price declined', reason)

    def test_social_mentions_with_declining_curve_are_blocked(self):
        old_market = simulation._market_snapshot
        old_social = simulation._social_snapshot
        try:
            simulation._market_snapshot = lambda *a, **k: {
                'price': 1.0, 'liquidity': 100_000, 'volume_24h': 50_000,
                'm5': -1.0, 'h1': -1.0, 'h6': -2.0, 'h24': -3.0,
                'buy_sell_ratio': 1.2, 'source': 'test',
            }
            simulation._social_snapshot = lambda *a, **k: {
                'signal': 'BUY', 'tweets': 10, 'sentiment': 0.5,
            }
            ok, reason, _ = simulation._speculative_market_gate({
                'symbol': 'MENTIONED',
                'chain': 'solana',
                'category': 'DEX_GEM',
                'coin_id': 'So11111111111111111111111111111111111111112',
            }, 1.0)
            self.assertFalse(ok)
            self.assertIn('mentions present but price curve is declining', reason)
        finally:
            simulation._market_snapshot = old_market
            simulation._social_snapshot = old_social

    def test_allocator_prefers_stronger_established_and_sizes_gems_smaller(self):
        class DummyPortfolio:
            cash = {'ethereum': 10, 'solana': 10, 'base': 10}
            holdings = {}

            def _trading_value(self):
                return 30

        proposals = [
            {
                'symbol': 'ONDO', 'chain': 'ethereum', 'category': 'ESTABLISHED',
                'rotation_score': 82, 'alpha_score': 82, 'trade_usd': 2,
            },
            {
                'symbol': 'MOON', 'chain': 'solana', 'category': 'DEX_GEM',
                'alpha_score': 82, 'liquidity_usd': 25_000,
                'price_change_24h': 5, 'trade_usd': 2,
            },
        ]
        ranked = simulation._rank_and_size_proposals(DummyPortfolio(), proposals)

        self.assertEqual(ranked[0]['symbol'], 'ONDO')
        gem = next(p for p in ranked if p['symbol'] == 'MOON')
        self.assertLessEqual(gem['trade_usd'], simulation.SIM_GEM_MAX_USD)
        self.assertGreater(ranked[0]['_allocation_score'], gem['_allocation_score'])


if __name__ == '__main__':
    unittest.main()
