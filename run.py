"""
AlphaScope v1.0 — One command to rule them all.
Usage: python3 run.py
"""
import threading
import time
from datetime import datetime
from fetcher import init_db, fetch_all

def scheduler(interval=30):
    print(f"📡 Auto-fetcher: every {interval} minutes")
    try:
        fetch_all()
    except Exception as e:
        print(f"✗ Fetch failed: {e}")
    while True:
        time.sleep(interval * 60)
        try:
            print(f"\n⏰ Auto-fetch at {datetime.now().strftime('%H:%M')}")
            fetch_all()
        except Exception as e:
            print(f"✗ Fetch failed: {e}")

if __name__ == '__main__':
    print("""
╔══════════════════════════════════════════════╗
║  🔍 AlphaScope v1.0                         ║
║  Crypto Alpha Intelligence                   ║
║                                              ║
║  Dashboard: http://localhost:8050            ║
║  Auto-refresh: every 30 minutes             ║
║  Ctrl+C to stop                             ║
╚══════════════════════════════════════════════╝
    """)
    init_db()
    bg = threading.Thread(target=scheduler, args=(30,), daemon=True)
    bg.start()
    time.sleep(3)
    from dashboard import app
    app.run(debug=False, port=8050)
