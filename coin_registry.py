"""
AlphaScope — Dynamic Coin Registry
Auto-learns new coin tickers from social data.
"""

import json
import os
import re
from datetime import datetime

REGISTRY_FILE = 'coin_registry.json'

FALSE_POSITIVES = {
    'THE', 'FOR', 'AND', 'NOT', 'HAS', 'ARE', 'WAS', 'BUT', 'ALL',
    'CAN', 'HAD', 'HER', 'ONE', 'OUR', 'OUT', 'DAY', 'GET', 'HOW',
    'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE', 'WAY', 'WHO', 'DID',
    'GOT', 'HIS', 'LET', 'SAY', 'SHE', 'TOO', 'USE', 'CEO', 'SEC',
    'ETF', 'USD', 'EUR', 'NFT', 'API', 'ATH', 'DCA', 'FUD', 'ICO',
    'IDO', 'TVL', 'APY', 'APR', 'DEX', 'CEX', 'DAO', 'TGE', 'KYC',
    'AMA', 'OTC', 'P2P', 'RPC', 'FAQ', 'PSA', 'IMO', 'TBH', 'FYI',
    'BIG', 'TOP', 'LOW', 'JUST', 'LIKE', 'THIS', 'THAT', 'WITH',
    'FROM', 'THEY', 'BEEN', 'HAVE', 'WILL', 'MORE', 'WHEN', 'WHAT',
    'YOUR', 'SOME', 'THAN', 'THEM', 'ONLY', 'VERY', 'BACK', 'ALSO',
    'OVER', 'GOOD', 'YEAR', 'LONG', 'MUCH', 'SAID', 'EACH', 'LOOK',
    'MOST', 'FIND', 'HERE', 'MANY', 'WELL', 'LAST', 'TAKE', 'COME',
    'MAKE', 'KNOW', 'FREE', 'REAL', 'FULL', 'BEST', 'SURE', 'STOP',
    'HOLD', 'HIGH', 'EVEN',
}


class CoinRegistry:
    def __init__(self):
        self.tickers = {
            'btc': 'BTC', 'bitcoin': 'BTC', 'eth': 'ETH', 'ethereum': 'ETH',
            'sol': 'SOL', 'solana': 'SOL', 'link': 'LINK', 'chainlink': 'LINK',
            'arb': 'ARB', 'arbitrum': 'ARB', 'sui': 'SUI', 'doge': 'DOGE',
            'avax': 'AVAX', 'dot': 'DOT', 'ada': 'ADA', 'xrp': 'XRP',
            'matic': 'MATIC', 'op': 'OP', 'apt': 'APT', 'sei': 'SEI',
            'tia': 'TIA', 'jup': 'JUP', 'ondo': 'ONDO', 'pendle': 'PENDLE',
            'render': 'RNDR', 'inj': 'INJ', 'pepe': 'PEPE', 'bonk': 'BONK',
            'wif': 'WIF', 'near': 'NEAR', 'atom': 'ATOM', 'algo': 'ALGO',
            'hype': 'HYPE', 'jto': 'JTO', 'pyth': 'PYTH', 'ray': 'RAY',
            'vet': 'VET', 'ftm': 'FTM', 'grt': 'GRT', 'aave': 'AAVE',
            'uni': 'UNI', 'mkr': 'MKR', 'fil': 'FIL', 'ar': 'AR',
            'tao': 'TAO', 'pengu': 'PENGU', 'mon': 'MON', 'hbar': 'HBAR',
        }
        self.coingecko_map = {
            'BTC': 'bitcoin', 'ETH': 'ethereum', 'SOL': 'solana', 'LINK': 'chainlink',
            'ARB': 'arbitrum', 'SUI': 'sui', 'DOGE': 'dogecoin', 'AVAX': 'avalanche-2',
            'DOT': 'polkadot', 'ADA': 'cardano', 'XRP': 'ripple', 'OP': 'optimism',
            'APT': 'aptos', 'SEI': 'sei-network', 'TIA': 'celestia',
            'ONDO': 'ondo-finance', 'PENDLE': 'pendle', 'RNDR': 'render-token',
            'INJ': 'injective-protocol', 'PEPE': 'pepe', 'NEAR': 'near',
            'ATOM': 'cosmos', 'ALGO': 'algorand', 'HYPE': 'hyperliquid',
            'AAVE': 'aave', 'UNI': 'uniswap', 'FIL': 'filecoin', 'AR': 'arweave',
            'TAO': 'bittensor', 'GRT': 'the-graph', 'HBAR': 'hedera-hashgraph',
        }
        self.base_tickers = dict(self.tickers)
        self.discovered = {}
        self.load()

    def load(self):
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE) as f:
                    data = json.load(f)
                    self.discovered = data.get('discovered', {})
                    learned = data.get('learned_tickers', {})
                    self.tickers.update(learned)
                    self.coingecko_map.update(data.get('learned_coingecko', {}))
                    if learned:
                        print(f"  \u2713 Loaded {len(learned)} learned tickers")
            except:
                pass

    def save(self):
        learned = {k: v for k, v in self.tickers.items() if k not in self.base_tickers}
        learned_cg = {k: v for k, v in self.coingecko_map.items()}
        data = {
            'discovered': self.discovered,
            'learned_tickers': learned,
            'learned_coingecko': learned_cg,
            'last_updated': datetime.now().isoformat(),
        }
        with open(REGISTRY_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def record_ticker(self, ticker, source='unknown'):
        ticker = ticker.upper()
        if ticker in FALSE_POSITIVES or len(ticker) < 2 or len(ticker) > 6:
            return
        if ticker.lower() in self.tickers or ticker in self.tickers.values():
            return
        now = datetime.now().isoformat()
        if ticker not in self.discovered:
            self.discovered[ticker] = {'count': 0, 'first_seen': now, 'sources': []}
        self.discovered[ticker]['count'] += 1
        self.discovered[ticker]['last_seen'] = now
        if source not in self.discovered[ticker]['sources']:
            self.discovered[ticker]['sources'].append(source)
        if self.discovered[ticker]['count'] >= 3 and len(self.discovered[ticker]['sources']) >= 2:
            self.tickers[ticker.lower()] = ticker
            print(f"    \U0001f195 New coin learned: {ticker} (seen {self.discovered[ticker]['count']}x from {self.discovered[ticker]['sources']})")
            self._try_resolve_coingecko(ticker)

    def _try_resolve_coingecko(self, ticker):
        try:
            import requests
            res = requests.get(f'https://api.coingecko.com/api/v3/search?query={ticker}', timeout=10)
            if res.status_code == 200:
                for coin in res.json().get('coins', [])[:3]:
                    if coin.get('symbol', '').upper() == ticker:
                        self.coingecko_map[ticker] = coin['id']
                        print(f"    \U0001f4ce Mapped {ticker} -> CoinGecko: {coin['id']}")
                        return
        except:
            pass

    def get_stats(self):
        pending = {k: v for k, v in self.discovered.items()
                   if k.lower() not in self.tickers and k not in self.tickers.values()}
        graduated = {k: v for k, v in self.tickers.items() if k not in self.base_tickers}
        return {
            'total_known': len(set(self.tickers.values())),
            'pending': len(pending),
            'graduated': len(graduated),
            'top_pending': sorted(pending.items(), key=lambda x: -x[1]['count'])[:10],
        }

registry = CoinRegistry()
