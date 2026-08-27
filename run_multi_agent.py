"""
AlphaScope — Multi-agent entry point (Stage 2 of the 3-agent refactor).

Runs 3 independent ChainAgent threads (Solana, Base, Ethereum) instead of
the old single run_agent_cycle(). Reuses the proven exit engine (price
monitor / check_exits), the single sim.db-writer pattern, and the exact
per-proposal gate/ban/execute pipeline from simulation.py — only the
buy-DECISION loop is now per-chain, using sizing.py's conviction-scaled
%-of-wallet model instead of the old cross-chain category-target allocator.

Assumes a co-running simulation.py session (the proven single-agent live
path, or run.py) is what keeps alphascope.db's market data fresh — this
script does not duplicate that refresh loop, it only reads what's already
there. If run with nothing else refreshing data, proposal loaders will just
find stale/no data and this process will safely sit idle, not error.

SAFETY (see the approved multi-agent plan, Stage 2):
  - Forced into PAPER MODE unless SIM_MULTI_AGENT_ALLOW_LIVE=true is set —
    independent of EXECUTOR_DRY_RUN, so a stray flag elsewhere can't route
    real money through this new, unsoaked code path by accident.
  - Writes to sim_multiagent.db, never sim.db — runs side by side with the
    proven single-agent live session with zero write contention or row
    confusion; both read the same shared alphascope.db.
  - The old run_agent_cycle()/run_simulation() path is completely untouched
    by this file — it remains the instant rollback for the whole refactor.

Usage:
    python3 run_multi_agent.py --hours 4 --cycle 5
"""

import argparse
import os
import threading
import time

import simulation as sim
from chain_agent import ChainAgent


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


def main():
    parser = argparse.ArgumentParser(
        description='AlphaScope multi-agent — 3 independent per-chain trading agents')
    parser.add_argument('--hours', type=float, default=4)
    parser.add_argument('--cycle', type=int, default=5)
    args = parser.parse_args()

    # Separate DB — see module docstring. Reassigning the module-level
    # constant here (before anything touches it) correctly propagates to
    # every simulation.py function that reads SIM_DB, since Python resolves
    # module globals at call time, not import time.
    sim.SIM_DB = 'sim_multiagent.db'

    allow_live = _env('SIM_MULTI_AGENT_ALLOW_LIVE', 'false').lower().strip() == 'true'
    if not allow_live:
        import executor
        executor._is_dry_run = lambda: True
        print("  🔒 Multi-agent forced to PAPER MODE "
              "(set SIM_MULTI_AGENT_ALLOW_LIVE=true to go live)")
    else:
        print("  🚀 Multi-agent LIVE MODE — SIM_MULTI_AGENT_ALLOW_LIVE=true")

    print(f"""
╔══════════════════════════════════════════════════╗
║  AlphaScope — Multi-Agent (3 independent chains)  ║
║  {'PAPER' if not allow_live else 'LIVE  '} · sim_multiagent.db{' '*17}║
╚══════════════════════════════════════════════════╝
""")

    sim.init_sim_tables()
    sim_id = f"MULTI_{time.strftime('%Y%m%d_%H%M')}"
    portfolio = sim.SimPortfolio(sim_id)

    sim._start_db_writer()
    time.sleep(0.5)

    monitor = sim.run_price_monitor(
        portfolio, sim.STOP_LOSS_PCT, sim.TAKE_PROFIT_PCT,
        duration_minutes=int(args.hours * 60) + 5, interval_seconds=10)

    agents = [ChainAgent(chain, portfolio) for chain in sim.CHAINS]
    agent_threads = []
    for agent in agents:
        t = threading.Thread(target=agent.run_forever, args=(args.cycle,),
                             daemon=True, name=agent.name)
        t.start()
        agent_threads.append([agent, t])

    print(f"  🤖 {len(agents)} chain agents running: {', '.join(sim.CHAINS)}\n")

    end_time = time.time() + args.hours * 3600
    try:
        while time.time() < end_time:
            time.sleep(30)
            # Watchdog — one chain's thread dying must never silently stop
            # that chain's trading for the rest of the run, same pattern as
            # the existing price_monitor watchdog in run_simulation().
            for slot in agent_threads:
                agent, t = slot
                if not t.is_alive():
                    print(f"  ⚠️  {agent.name} thread died — restarting")
                    try:
                        from executor import alert_error
                        alert_error(f"{agent.name} thread died — restarted automatically")
                    except Exception:
                        pass
                    new_t = threading.Thread(target=agent.run_forever, args=(args.cycle,),
                                             daemon=True, name=agent.name)
                    new_t.start()
                    slot[1] = new_t
            if not monitor.is_alive():
                print("  ⚠️  Price monitor died — restarting")
                remaining_min = int((end_time - time.time()) / 60) + 5
                monitor = sim.run_price_monitor(
                    portfolio, sim.STOP_LOSS_PCT, sim.TAKE_PROFIT_PCT,
                    duration_minutes=max(remaining_min, 5), interval_seconds=10)
    except KeyboardInterrupt:
        print("\n  Stopped by user.")

    print(f"\n{'='*60}\n  MULTI-AGENT SESSION COMPLETE -- {sim_id}\n{'='*60}")
    portfolio.print_status()
    portfolio.save()


if __name__ == '__main__':
    main()
