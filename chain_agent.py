"""
AlphaScope — Independent per-chain trading agent.

One ChainAgent per chain (Solana, Base, Ethereum), each running its own
cycle loop against its own slice of the shared portfolio. No shared
cross-chain allocator deciding percentages — each agent decides its own
buys using its own chain's wallet balance and each proposal's own conviction
score (see sizing.py), and its own independent circuit breaker.

Deliberately shared, not duplicated, across all 3 agents:
  - The SimPortfolio object itself (cash is already a dict keyed by chain;
    holdings/trades are one flat structure with a threading.Lock guarding
    the compound mutations — see simulation.py's SimPortfolio docstrings).
  - The exit engine (check_exits / run_price_monitor) — fast rug detection
    benefits from one unified loop watching every position, not 3 separate
    slower ones.
  - fetcher.py's data refresh and token_intelligence's scoring pass — one
    centralized refresh all 3 agents read from.
  - The single sim.db writer thread.
  - social_monitor.py's Twitter credit budget (now lock-protected).
  - The proven per-proposal gate/ban/execute pipeline in
    simulation._process_buy_proposals() — reused as-is, not rewritten.

A minimal common Agent lifecycle (run_forever/cycle-exception-isolation/
thread-watchdog) is intentionally kept generic so future non-trading agents
(airdrop hunting, yield farming, staking — one per chain, same pattern) can
reuse it without inheriting anything trading-specific.
"""

import os
import time
import threading


def _env(key, default=''):
    val = os.environ.get(key, '')
    if val:
        return val
    try:
        with open('.env') as f:
            for line in f:
                line = line.strip()
                if line.startswith(f'{key}='):
                    return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return default


class Agent:
    """Minimal shared lifecycle: run_forever + per-cycle exception isolation.
    Subclass and implement run_cycle(). Not trading-specific — later
    AirdropAgent/YieldAgent/StakingAgent can extend this the same way."""

    def __init__(self, name):
        self.name = name
        self._stop = False

    def run_cycle(self):
        raise NotImplementedError

    def run_forever(self, cycle_min=5):
        print(f"  🤖 [{self.name}] agent started (every {cycle_min}min)")
        while not self._stop:
            try:
                actions = self.run_cycle()
                if actions:
                    print(f"  [{self.name}] {actions} action(s) this cycle")
            except Exception as e:
                # One agent's bug must never take down the other agents or
                # the whole process — this is the per-agent analog of the
                # crash-resilience fix applied to the old single-cycle loop
                # in run_simulation(). Log, alert, keep going.
                print(f"  ⚠️  [{self.name}] cycle error: {e} — continuing to next cycle")
                try:
                    from executor import alert_error
                    alert_error(f"[{self.name}] agent cycle failed: {str(e)[:180]} — agent continues")
                except Exception:
                    pass
            time.sleep(cycle_min * 60)

    def stop(self):
        self._stop = True


# ── Sizing config — percentages, not dollar figures ────────────────────────
SIM_MIN_ALLOC_SCORE = float(_env('SIM_MIN_ALLOC_SCORE', '58'))
SIZING_MIN_PCT = float(_env('SIM_SIZING_MIN_PCT', '0.05'))
SIZING_MAX_PCT = float(_env('SIM_SIZING_MAX_PCT', '0.35'))
SIZING_MAX_POSITION_PCT = float(_env('SIM_SIZING_MAX_POSITION_PCT', '0.35'))
DAILY_LOSS_PCT = float(_env('SIM_DAILY_LOSS_PCT', '0.30'))

# alpha_score is NOT on a shared scale across proposal sources — DEX_GEM's
# formula (min(100, cross_score*12+20)) hits its ceiling easily on ordinary
# gems, while ESTABLISHED's rotation_score formula tops out at 95 and rarely
# gets close. Normalizing both through the same conviction() curve without
# correction would systematically over-size unvetted gems relative to
# established coins — the opposite of the original system's own risk
# weighting (SIM_TARGET_ESTABLISHED_PCT=0.60 vs GEMS=0.25 vs LISTINGS=0.15).
# This preserves that same relative weighting as a ceiling multiplier on an
# otherwise fully conviction-scaled, percentage-based model.
CATEGORY_MAX_PCT_WEIGHT = {
    'ESTABLISHED': 1.0,
    'DEX_GEM': 0.42,   # ~25/60 of established's ceiling
    'GEM': 0.42,
    'LISTING': 0.25,   # ~15/60 of established's ceiling
}


class ChainAgent(Agent):
    def __init__(self, chain, portfolio, stop_loss=None, take_profit=None):
        super().__init__(f"chain:{chain}")
        self.chain = chain
        self.portfolio = portfolio
        self.stop_loss = stop_loss
        self.take_profit = take_profit

    def _circuit_breaker_tripped(self):
        """
        Percentage-of-current-on-chain-budget circuit breaker — not a fixed
        dollar figure. The loss limit is recomputed every cycle against
        THIS chain's current trading cash, so it automatically grows or
        shrinks as the wallet does, same philosophy as position sizing.
        Independent per chain: one chain tripping never affects the other two.
        """
        chain_cash_now = self.portfolio.cash.get(self.chain, 0)
        loss_limit = chain_cash_now * DAILY_LOSS_PCT
        starting = self.portfolio.starting_trading_by_chain.get(self.chain, chain_cash_now)
        trading_pnl = self.portfolio.trading_value_for(self.chain) - starting
        if trading_pnl < -loss_limit:
            print(f"    🛑 [{self.name}] loss limit hit (${trading_pnl:.2f}, "
                  f"limit ${loss_limit:.2f}) — no new buys this cycle")
            return True
        return False

    def _gather_proposals(self):
        """Pull every proposal source, filtered to this chain only. Each
        source already tags proposals with their chain — this is pure
        filtering, no new data plumbing (see the multi-agent plan's
        exploration notes)."""
        import simulation as sim

        proposals = []

        try:
            dex = sim._load_dex_proposals(self.portfolio)
            proposals += [p for p in dex if p.get('chain') == self.chain]
        except Exception as e:
            print(f"    [{self.name}] dex proposals error: {e}")

        try:
            established = sim._load_established_proposals(self.portfolio, chain=self.chain)
            proposals += established
        except Exception as e:
            print(f"    [{self.name}] established proposals error: {e}")

        if self.chain == 'solana':
            # _load_listing_proposals hardcodes every result to solana —
            # not a bug, a deliberate design (lowest gas, fastest
            # execution) documented in simulation.py. Base/ETH agents
            # simply never see listing proposals, same as they'd never
            # see any other proposal type they don't produce.
            try:
                listings = sim._load_listing_proposals(self.portfolio)
                proposals += listings
            except Exception as e:
                print(f"    [{self.name}] listing proposals error: {e}")

        if self.chain in ('solana', 'base'):
            # wallet_agent.evaluate_signals() only ever covers solana/base
            # (ETH mainnet excluded there — see its own SUPPORTED_CHAINS).
            try:
                from wallet_agent import evaluate_signals
                wa = evaluate_signals() or []
                seen = {p.get('symbol') for p in proposals}
                for p in wa:
                    if p.get('chain') != self.chain:
                        continue
                    if p.get('action') in ('SKIP', None):
                        continue
                    sym = p.get('symbol', '')
                    if not sym or sym in seen:
                        continue
                    seen.add(sym)
                    proposals.append(p)
            except Exception as e:
                print(f"    [{self.name}] wallet_agent error: {e}")

        return proposals

    def _size_proposals(self, proposals):
        """Overwrite each proposal's trade_usd using the conviction-scaled
        %-of-chain-wallet model, replacing whatever the old proposal-
        generation functions set (their $ values are otherwise ignored for
        ChainAgent's path — sizing.py owns this decision now)."""
        import sizing
        chain_cash = self.portfolio.cash.get(self.chain, 0)
        for p in proposals:
            score = float(p.get('alpha_score', p.get('rotation_score', 0)) or 0)
            weight = CATEGORY_MAX_PCT_WEIGHT.get(p.get('category', ''), 0.42)
            p['trade_usd'] = sizing.position_size_usd(
                chain_cash, score, SIM_MIN_ALLOC_SCORE,
                SIZING_MIN_PCT, SIZING_MAX_PCT * weight,
                SIZING_MAX_POSITION_PCT * weight)
            p['_allocation_score'] = score
        # Highest conviction first — _process_buy_proposals respects
        # SIM_MAX_NEW_BUYS_PER_CYCLE, so ordering decides who gets the slots.
        proposals.sort(key=lambda p: p.get('alpha_score', 0), reverse=True)
        return proposals

    def run_cycle(self):
        import simulation as sim

        if self._circuit_breaker_tripped():
            return 0

        proposals = self._gather_proposals()
        actionable = [p for p in proposals if p.get('action') not in ('SKIP', None)]
        if not actionable:
            return 0

        print(f"    [{self.name}] proposals: " + " | ".join(
            f"{p['action']} {p['symbol']}" for p in actionable[:10]))
        proposals = self._size_proposals(actionable)

        return sim._process_buy_proposals(self.portfolio, proposals)
