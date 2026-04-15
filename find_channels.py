"""Find working Telegram crypto channels."""
import requests
import time
import re

channels = [
    # Whale & Trading
    'whale_alert_io', 'WhaleTrades', 'CryptoWhalesChannel',
    # News
    'crypto', 'blockchain', 'CoinTelegraph', 'TheBlock__',
    'CryptoBanter', 'Cointelegraph',
    # Alpha & Analysis
    'CryptoCapitalVenture', 'AltcoinBuzz', 'cryptodaku_',
    'LayerZero_Labs', 'ArkhamIntel',
    # DeFi
    'DeFiLlama', 'DeFiPulse', 'UniswapProtocol',
    # Airdrops
    'AirdropOfficial', 'aaboratory', 'CryptoAirdropFree',
    # Trading signals
    'binaboratory', 'crypto_signal_free', 'FatPigSignals',
    # Research
    'messaboratory', 'DelphiDigital',
]

print("Testing Telegram channels...")
working = []
for ch in channels:
    try:
        url = f"https://t.me/s/{ch}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        if res.status_code == 200:
            messages = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', res.text, re.DOTALL)
            clean = [m for m in messages if len(re.sub(r'<[^>]+>', '', m).strip()) > 10]
            if clean:
                preview = re.sub(r'<[^>]+>', '', clean[-1]).strip()[:80]
                print(f"  ✅ @{ch}: {len(clean)} messages — {preview}")
                working.append(ch)
            else:
                print(f"  ⚪ @{ch}: page loaded but 0 messages")
        else:
            print(f"  ❌ @{ch}: HTTP {res.status_code}")
    except Exception as e:
        print(f"  ❌ @{ch}: {str(e)[:50]}")
    time.sleep(1)

print(f"\n✓ Working channels ({len(working)}):")
print(f"  {working}")
