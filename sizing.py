"""
AlphaScope — Autonomous position sizing.

Position size = f(this chain's own wallet balance, this proposal's own
conviction/alpha score). No hardcoded dollar caps anywhere in this file —
everything scales with the wallet, and stronger signals get disproportionately
more than marginal ones.

This replaces simulation.py's old category-target allocator (_category_targets/
_category_exposure/_risk_adjusted_score/_rank_and_size_proposals), which sized
trades against a fixed 70/20/10 target split across the COMBINED 3-chain
portfolio — a fundamentally different model than "how much of THIS chain's own
budget does THIS specific opportunity deserve."
"""


def conviction(score: float, min_score: float, max_score: float = 100.0,
               exponent: float = 1.5) -> float:
    """
    Normalize a proposal's own score (alpha_score/rotation_score/whatever
    scoring already produced it — see gates in simulation.py) to 0..1, then
    apply convexity so a high-conviction signal gets disproportionately more
    allocation than one that just barely cleared the gate threshold.

    exponent > 1 means: a score halfway between min and max gets LESS than
    half the pct range (convex), rewarding genuine confidence over marginal
    passes. exponent = 1 would be linear.
    """
    if max_score <= min_score:
        return 0.0
    c = (score - min_score) / (max_score - min_score)
    c = max(0.0, min(1.0, c))
    return c ** exponent


def position_size_usd(chain_cash: float, score: float, min_score: float,
                       min_pct: float, max_pct: float,
                       max_position_pct_of_chain: float) -> float:
    """
    chain_cash: this chain's currently available trading cash
                (portfolio.cash[chain] — already the live-wallet-derived
                trading slice, not the raw wallet balance).
    score: this proposal's own conviction score.
    min_pct/max_pct: the sizing range as a fraction of chain_cash — a
                weak-but-qualifying signal gets min_pct, a max-conviction
                signal approaches max_pct.
    max_position_pct_of_chain: hard diversification ceiling — no single
                position may exceed this fraction of chain_cash, regardless
                of how strong the signal is. Still a percentage, not a
                dollar figure, so it scales with the wallet automatically.

    Returns a dollar amount. Zero dollar constants anywhere in this
    function — the only inputs that matter are the wallet's own size and
    the signal's own strength.
    """
    if chain_cash <= 0:
        return 0.0
    c = conviction(score, min_score)
    pct = min_pct + c * (max_pct - min_pct)
    usd = chain_cash * pct
    ceiling = chain_cash * max_position_pct_of_chain
    return round(min(usd, ceiling), 4)
