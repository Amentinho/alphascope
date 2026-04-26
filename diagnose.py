"""Full diagnostic — runs on your Mac, not in container"""
import sqlite3, json

conn = sqlite3.connect('alphascope.db')

print("=" * 60)
print("1. DEX GEMS AVAILABLE RIGHT NOW")
print("=" * 60)
rows = conn.execute("""
    SELECT symbol, chain, liquidity_usd, cross_score, age_hours, contract_address
    FROM dex_gems 
    WHERE fetched_at >= datetime('now', '-2 hours')
    ORDER BY chain, cross_score DESC
""").fetchall()
chain_counts = {}
for r in rows:
    chain_counts[r[1]] = chain_counts.get(r[1], 0) + 1
    
print(f"Total: {len(rows)} gems")
for ch, n in sorted(chain_counts.items()):
    print(f"  {ch}: {n} gems")

print("\nTop 15 by score:")
top = conn.execute("""
    SELECT symbol, chain, liquidity_usd, cross_score, age_hours
    FROM dex_gems WHERE fetched_at >= datetime('now', '-2 hours')
    ORDER BY cross_score DESC, liquidity_usd DESC LIMIT 15
""").fetchall()
for r in top:
    print(f"  {r[0]:<12} {r[1]:<10} liq:${r[2]/1000:.0f}k score:{r[3]} age:{r[4]:.0f}h")

print("\n" + "=" * 60)
print("2. VALIDATION STATUS OF AVAILABLE GEMS")
print("=" * 60)
validated = conn.execute("""
    SELECT dg.symbol, dg.chain, dg.liquidity_usd, dg.cross_score,
           tv.verdict, tv.total_score
    FROM dex_gems dg
    LEFT JOIN token_validation tv 
      ON tv.contract_address = dg.contract_address AND tv.chain = dg.chain
    WHERE dg.fetched_at >= datetime('now', '-2 hours')
    ORDER BY dg.cross_score DESC LIMIT 20
""").fetchall()
for r in validated:
    verdict = r[4] or 'NOT_VALIDATED'
    print(f"  {r[0]:<12} {r[1]:<10} liq:${r[2]/1000:.0f}k score:{r[3]} → {verdict}")

print("\n" + "=" * 60)
print("3. WHY AGENT ISN'T BUYING ETH/BASE/BSC")
print("=" * 60)
# Check what passes all filters
eth_gems = conn.execute("""
    SELECT dg.symbol, dg.chain, dg.liquidity_usd, dg.cross_score,
           tv.verdict, tv.total_score, dg.contract_address
    FROM dex_gems dg
    LEFT JOIN token_validation tv 
      ON tv.contract_address = dg.contract_address AND tv.chain = dg.chain
    WHERE dg.fetched_at >= datetime('now', '-2 hours')
    AND dg.chain IN ('ethereum','base','bsc','arbitrum')
    ORDER BY dg.liquidity_usd DESC
""").fetchall()
print(f"ETH/BASE/BSC/ARB gems: {len(eth_gems)}")
for r in eth_gems:
    verdict = r[4] or 'NOT_VALIDATED'
    liq = r[2]
    liq_min = {'ethereum': 60000, 'base': 40000, 'bsc': 30000, 'arbitrum': 40000}.get(r[1], 40000)
    passes_liq = liq >= liq_min
    print(f"  {r[0]:<12} {r[1]:<10} liq:${liq/1000:.0f}k (min:${liq_min/1000:.0f}k {'✅' if passes_liq else '❌'}) verdict:{verdict}")

print("\n" + "=" * 60)
print("4. SOCIAL SIGNALS AVAILABLE")
print("=" * 60)
social = conn.execute("""
    SELECT symbol, chain, signal, sentiment_score, tweet_count,
           round((julianday('now') - julianday(cached_at)) * 1440) as age_min
    FROM token_social_cache
    WHERE cached_at >= datetime('now', '-30 minutes')
    ORDER BY age_min ASC LIMIT 20
""").fetchall()
print(f"Fresh social signals: {len(social)}")
for r in social:
    print(f"  {r[0]:<12} {r[1]:<10} {r[2]:<14} sent:{r[3]:+.2f} tweets:{r[4]} age:{r[5]:.0f}min")

print("\n" + "=" * 60)
print("5. AGENT CONFIG vs FILTERS")
print("=" * 60)
config = dict(conn.execute("SELECT key, value FROM agent_config").fetchall())
print(f"  min_signal_confidence: {config.get('min_signal_confidence', 65)}")
print(f"  max_gas_usd: {config.get('max_gas_usd', 20)}")
print(f"  max_position_usd: {config.get('max_position_usd', 500)}")

# Estimate gas as % of typical position for each chain
print("\n  Gas % of $50 position by chain:")
gas = {'ethereum': 12, 'base': 0.10, 'bsc': 0.20, 'solana': 0.001, 'arbitrum': 0.25}
for ch, g in gas.items():
    pct = g / 50 * 100
    ok = '✅' if pct < 20 else '❌ TOO HIGH'
    print(f"    {ch:<12}: ${g} gas = {pct:.1f}% of $50 {ok}")

conn.close()
