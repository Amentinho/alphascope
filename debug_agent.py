"""
AlphaScope — Agent proposal debug
Traces exactly why each proposal gets filtered in run_agent_cycle.
Run: python3 debug_agent.py
"""
import json
from simulation import SimPortfolio, resolve_price, CHAINS, STARTING_BALANCE_USD, MIN_SIGNAL_CONF

# ── Load proposals from wallet_agent ─────────────────────────────────────────
print("=" * 60)
print("  RAW PROPOSALS from wallet_agent.evaluate_signals()")
print("=" * 60)
proposals = []
try:
    from wallet_agent import evaluate_signals
    proposals = evaluate_signals() or []
    print(f"  Total returned: {len(proposals)}")
    for i, p in enumerate(proposals):
        print(f"\n  [{i+1}] {p.get('action','?')} {p.get('symbol','?')} chain={p.get('chain','?')}")
        print(f"       trade_usd={p.get('trade_usd',0):.0f}  alpha={p.get('alpha_score',0)}")
        print(f"       category={p.get('category','?')}  coin_id={str(p.get('coin_id',''))[:40]}")
        print(f"       reasons={str(p.get('reasons',''))[:80]}")
except Exception as e:
    print(f"  wallet_agent error: {e}")

# ── Simulate the filter logic in run_agent_cycle ──────────────────────────────
print()
print("=" * 60)
print("  FILTER TRACE — why each proposal passes or fails")
print("=" * 60)

port = SimPortfolio("DEBUG")

stop_lossed = {f"{t['symbol']}_{t['chain']}" for t in port.trades
               if t['action'] == 'SELL' and t.get('reason') == 'stop_loss'}
try:
    with open('sim_ban_list.json') as f:
        stop_lossed |= set(json.load(f))
except Exception:
    pass

chain_counts = {}
for key, pos in port.holdings.items():
    if not pos.get('is_real'):
        ch = pos['chain']
        chain_counts[ch] = chain_counts.get(ch, 0) + 1

print(f"  Cash per chain: { {ch: f'${v:.0f}' for ch, v in port.cash.items()} }")
print(f"  Stop-lossed:    {stop_lossed or 'none'}")
print(f"  Chain counts:   {chain_counts or 'empty'}")

passed = 0
for p in proposals:
    sym   = p.get('symbol', '')
    chain = p.get('chain', 'solana')
    action = p.get('action', '')
    trade_usd = min(p.get('trade_usd', 40), 75)
    key = f"{sym}_{chain}"

    reasons_blocked = []

    if p.get('action') == 'SKIP':
        reasons_blocked.append("action=SKIP")
    if not sym or action not in ('BUY', 'ACCUMULATE'):
        reasons_blocked.append(f"action not BUY/ACCUMULATE (got '{action}')")
    if key in port.holdings:
        reasons_blocked.append("already holding")
    if key in stop_lossed:
        reasons_blocked.append("stop-lossed / banned")
    chain_limit = 4 if chain == 'solana' else 3
    if chain_counts.get(chain, 0) >= chain_limit:
        reasons_blocked.append(f"chain_limit hit ({chain}: {chain_counts.get(chain,0)}/{chain_limit})")
    if chain == 'ethereum':
        reasons_blocked.append("ETH mainnet blocked (gas)")
    if not port.can_buy(chain, trade_usd):
        reasons_blocked.append(f"insufficient cash (need ${trade_usd}, have ${port.cash.get(chain,0):.0f})")

    # Price check
    price = resolve_price(sym, coin_id=p.get('coin_id', ''), chain=chain, use_cache=False)
    if not reasons_blocked:  # only fetch price if other checks pass
        if not price or price <= 0:
            reasons_blocked.append(f"price=0 (resolve_price returned {price})")
        elif price < 1e-9:
            reasons_blocked.append(f"price too low ({price:.2e})")

    label = "✅ PASS" if not reasons_blocked else "❌ BLOCKED"
    print(f"\n  {label} {sym} ({chain}) ${trade_usd:.0f}")
    if reasons_blocked:
        for r in reasons_blocked:
            print(f"    → {r}")
    else:
        print(f"    → price=${price:.6f} — would BUY")
        passed += 1

print()
print("=" * 60)
print(f"  RESULT: {passed}/{len(proposals)} proposals would execute")
if passed == 0:
    print("  Fix needed — see blocked reasons above")
else:
    print("  Agent is ready to trade")
print("=" * 60)
