"""
AlphaScope simulation quick test — run this before committing to a 6h sim.
Usage:  python3 test_sim.py
Exits 0 if all checks pass, 1 if any fail.
"""

import sys
errors = []
warnings = []

# ── 1. Cost basis ─────────────────────────────────────────────────────────────
print("=" * 55)
print("  CHECK 1: Real portfolio cost basis")
print("=" * 55)
try:
    from simulation import SimPortfolio, REAL_PORTFOLIO
    p = SimPortfolio("TEST_COST")
    expected = sum(
        pos['amount'] * pos['entry_price']
        for positions in REAL_PORTFOLIO.values()
        for pos in positions
    )
    live_val  = p._real_value()
    cost      = p.starting_real
    pnl       = live_val - cost

    print(f"  Cost basis (fixed):  ${cost:>10,.2f}")
    print(f"  Live value (market): ${live_val:>10,.2f}")
    print(f"  Unrealised PnL:      ${pnl:>+10,.2f}")

    if abs(cost - expected) > 1:
        errors.append(f"Cost basis mismatch: got ${cost:.2f}, expected ${expected:.2f}")
        print(f"  ❌ FAIL — cost basis wrong!")
    else:
        print(f"  ✅ PASS — cost basis locked to entry prices")

    if live_val == cost:
        warnings.append("live_val == cost_basis — prices may not be resolving (check network)")
        print(f"  ⚠️  WARN — live value = cost basis, prices might be 0 (network issue?)")
    else:
        pct = pnl / cost * 100
        print(f"  ✅ Prices resolving — portfolio is {pct:+.1f}% vs cost basis")
except Exception as e:
    errors.append(f"Cost basis check crashed: {e}")
    print(f"  ❌ CRASH: {e}")

# ── 2. Price resolver ─────────────────────────────────────────────────────────
print()
print("=" * 55)
print("  CHECK 2: Price resolver (live API)")
print("=" * 55)
try:
    from simulation import resolve_price
    test_pairs = [
        ("SOL",  "solana",     "solana"),
        ("BTC",  "bitcoin",    "bitcoin"),
        ("LINK", "chainlink",  "ethereum"),
        ("ETH",  "ethereum",   "ethereum"),
        ("HYPE", "hyperliquid","arbitrum"),
    ]
    zero_count = 0
    for sym, cid, chain in test_pairs:
        px = resolve_price(sym, cid, chain, use_cache=False)
        status = "✅" if px > 0 else "❌"
        if px == 0:
            zero_count += 1
        print(f"  {status} {sym:<6} ${px:>12,.4f}")

    if zero_count == len(test_pairs):
        errors.append("ALL prices returned 0 — network or API issue")
        print(f"  ❌ FAIL — all prices are 0, sim will not trade")
    elif zero_count > 0:
        warnings.append(f"{zero_count}/{len(test_pairs)} prices returned 0")
        print(f"  ⚠️  WARN — {zero_count} prices are 0")
    else:
        print(f"  ✅ PASS — all prices resolved")
except Exception as e:
    errors.append(f"Price resolver crashed: {e}")
    print(f"  ❌ CRASH: {e}")

# ── 3. Fallback signals ───────────────────────────────────────────────────────
print()
print("=" * 55)
print("  CHECK 3: Fallback signal engine")
print("=" * 55)
try:
    from simulation import _fallback_signals
    sigs = _fallback_signals()
    buyable = [s for s in sigs if s.get('action') == 'BUY']
    print(f"  Total signals:   {len(sigs)}")
    print(f"  Buyable (BUY):   {len(buyable)}")
    for s in sigs[:6]:
        print(f"    {s['action']:<5} {s['symbol']:<8} ({s['chain']:<8}) "
              f"${s['trade_usd']:<4} {s['reasons'][:45]}")
    if len(buyable) == 0:
        errors.append("Fallback signals returned 0 BUY proposals — check network")
        print(f"  ❌ FAIL — no buyable signals (API down or network blocked?)")
    else:
        print(f"  ✅ PASS — fallback engine is live")
except Exception as e:
    errors.append(f"Fallback signals crashed: {e}")
    print(f"  ❌ CRASH: {e}")

# ── 4. Agent cycle dry run ────────────────────────────────────────────────────
print()
print("=" * 55)
print("  CHECK 4: Agent cycle dry run (no real buys)")
print("=" * 55)
try:
    from simulation import SimPortfolio, run_agent_cycle
    port = SimPortfolio("TEST_AGENT")
    actions = run_agent_cycle(port)
    sim_trades = [t for t in port.trades if t['action'] == 'BUY']
    print(f"  Actions taken:   {actions}")
    print(f"  Simulated buys:  {len(sim_trades)}")
    for t in sim_trades[:5]:
        print(f"    BUY {t['symbol']:<8} ({t['chain']:<8}) "
              f"${t['usd']:.0f} @ ${t['price']:.6f}")
    if len(sim_trades) == 0:
        warnings.append("Agent cycle made 0 trades — signals may be empty or all filtered")
        print(f"  ⚠️  WARN — 0 trades fired (signals filtered out or prices=0)")
    else:
        print(f"  ✅ PASS — agent cycle is trading")
except Exception as e:
    errors.append(f"Agent cycle crashed: {e}")
    print(f"  ❌ CRASH: {e}")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 55)
print("  SUMMARY")
print("=" * 55)
for w in warnings:
    print(f"  ⚠️  {w}")
for e in errors:
    print(f"  ❌ {e}")
if not errors and not warnings:
    print("  ✅ All checks passed — safe to run full simulation")
    print()
    print("  Run:  caffeinate -i python3 simulation.py --hours 6 --cycle 5")
elif not errors:
    print("  ✅ No hard errors — warnings above are non-blocking")
    print("  Run:  caffeinate -i python3 simulation.py --hours 6 --cycle 5")
else:
    print("  ❌ Fix errors above before running full simulation")
    sys.exit(1)
