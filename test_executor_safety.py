import os
import unittest

os.environ.setdefault('EXECUTOR_DRY_RUN', 'true')

import executor


class ExecutorSafetyTests(unittest.TestCase):
    def test_min_out_applies_evm_slippage(self):
        old = executor.EVM_SLIPPAGE_BPS
        try:
            executor.EVM_SLIPPAGE_BPS = 300
            self.assertEqual(executor._min_out(10_000), 9_700)
        finally:
            executor.EVM_SLIPPAGE_BPS = old

    def test_gas_guard_blocks_oversized_gas_costs(self):
        old_price = executor._eth_price
        try:
            executor._eth_price = lambda: 2_000
            ok, err = executor._guard_evm_gas(
                trade_usd=2,
                gas_units=200_000,
                max_fee_per_gas=50_000_000_000,
                label='swap',
            )
            self.assertFalse(ok)
            self.assertIn('gas $20.00 exceeds limit', err)
        finally:
            executor._eth_price = old_price

    def test_sol_sell_route_check_fails_closed_on_network_error(self):
        old_get = executor.requests.get
        try:
            def raising_get(*args, **kwargs):
                raise TimeoutError('network down')

            executor.requests.get = raising_get
            ok, reason = executor._can_sell_sol_token('So11111111111111111111111111111111111111112')
            self.assertFalse(ok)
            self.assertIn('check_failed_blocking', reason)
        finally:
            executor.requests.get = old_get

    def test_pumpfun_graduation_unknown_is_not_allowed(self):
        old_get = executor.requests.get
        try:
            class Response:
                status_code = 503

                def json(self):
                    return {}

            executor.requests.get = lambda *args, **kwargs: Response()
            self.assertIsNone(executor._is_pumpfun_graduated('So11111111111111111111111111111111111111112'))
        finally:
            executor.requests.get = old_get


if __name__ == '__main__':
    unittest.main()
