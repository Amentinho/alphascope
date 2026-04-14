"""
AlphaScope — Main Launcher
Runs the data fetcher on a schedule and launches the dashboard.
One command: python3 run.py
"""

import threading
import time
import subprocess
from datetime import datetime
from fetcher import init_db, fetch_all

# ============================================================
# SCHEDULED FETCHER (runs in background thread)
# ============================================================
def run_scheduler(interval_minutes=30):
    """Fetch data immediately, then every N minutes."""
    print(f"📡 Scheduler started — fetching every {interval_minutes} minutes")
    
    # First fetch immediately
    try:
        fetch_all()
    except Exception as e:
        print(f"✗ Initial fetch failed: {e}")
    
    # Then schedule recurring fetches
    while True:
        time.sleep(interval_minutes * 60)
        try:
            print(f"\n⏰ Scheduled fetch at {datetime.now().strftime('%H:%M')}")
            fetch_all()
        except Exception as e:
            print(f"✗ Scheduled fetch failed: {e}")

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("""
╔══════════════════════════════════════════╗
║  🔍 AlphaScope v0.3                     ║
║  Crypto Sentiment Intelligence          ║
║                                          ║
║  Dashboard: http://localhost:8050        ║
║  Auto-refresh: every 30 minutes         ║
║  Press Ctrl+C to stop                   ║
╚══════════════════════════════════════════╝
    """)
    
    # Initialize database
    init_db()
    
    # Start fetcher in background thread
    scheduler = threading.Thread(target=run_scheduler, args=(30,), daemon=True)
    scheduler.start()
    
    # Wait for first fetch to complete
    time.sleep(2)
    
    # Start dashboard (this blocks)
    from dashboard import app
    print("\n🖥️  Starting dashboard...")
    app.run(debug=False, port=8050)
