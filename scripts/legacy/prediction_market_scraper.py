#!/usr/bin/env python3
"""
Prediction Market Scraper
Pulls prices from Kalshi, PredictIt, and Polymarket for insurance/cat-related markets
"""

import requests
import json
from datetime import datetime
import time

class PredictionMarketScraper:
    def __init__(self):
        self.kalshi_base = "https://api.elections.kalshi.com/trade-api/v2"
        self.predictit_base = "https://www.predictit.org/api/marketdata/all"
        self.polymarket_base = "https://clob.polymarket.com"
        
    def get_kalshi_markets(self, search_term=None):
        """
        Fetch markets from Kalshi
        search_term: keyword to filter markets (e.g., 'insurance', 'hurricane', 'climate')
        """
        print(f"\n{'='*60}")
        print("FETCHING KALSHI MARKETS")
        print(f"{'='*60}")
        
        try:
            # Get all markets
            url = f"{self.kalshi_base}/markets"
            params = {
                'limit': 200,
                'status': 'open'
            }
            
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            markets = data.get('markets', [])
            print(f"Found {len(markets)} total open markets")
            
            # Filter by search term if provided
            if search_term:
                keywords = ['climate', 'weather', 'hurricane', 'flood', 'disaster', 
                           'temperature', 'insurance', 'wildfire', 'storm', 'catastrophe',
                           'earthquake', 'tornado']
                
                filtered = []
                for market in markets:
                    title = market.get('title', '').lower()
                    ticker = market.get('ticker', '').lower()
                    
                    if any(keyword in title or keyword in ticker for keyword in keywords):
                        filtered.append(market)
                
                markets = filtered
                print(f"Filtered to {len(markets)} climate/disaster-related markets")
            
            # Format results
            results = []
            for market in markets:
                result = {
                    'platform': 'Kalshi',
                    'ticker': market.get('ticker'),
                    'title': market.get('title'),
                    'yes_price': market.get('yes_bid', 0) / 100 if market.get('yes_bid') else None,  # Convert cents to dollars
                    'no_price': market.get('no_bid', 0) / 100 if market.get('no_bid') else None,
                    'volume': market.get('volume'),
                    'open_interest': market.get('open_interest'),
                    'close_date': market.get('close_time'),
                    'category': market.get('category'),
                    'url': f"https://kalshi.com/markets/{market.get('ticker')}"
                }
                results.append(result)
            
            return results
            
        except Exception as e:
            print(f"Error fetching Kalshi markets: {e}")
            return []
    
    def get_predictit_markets(self):
        """Fetch all markets from PredictIt"""
        print(f"\n{'='*60}")
        print("FETCHING PREDICTIT MARKETS")
        print(f"{'='*60}")
        
        try:
            response = requests.get(self.predictit_base)
            response.raise_for_status()
            data = response.json()
            
            markets = data.get('markets', [])
            print(f"Found {len(markets)} total markets")
            
            # Filter for relevant markets
            keywords = ['climate', 'weather', 'hurricane', 'disaster', 'temperature',
                       'insurance', 'wildfire', 'storm', 'flood', 'natural']
            
            results = []
            for market in markets:
                name = market.get('name', '').lower()
                short_name = market.get('shortName', '').lower()
                
                # Check if market is relevant
                if any(keyword in name or keyword in short_name for keyword in keywords):
                    # Get contract details
                    for contract in market.get('contracts', []):
                        result = {
                            'platform': 'PredictIt',
                            'market_id': market.get('id'),
                            'market_name': market.get('name'),
                            'contract_name': contract.get('name'),
                            'yes_price': contract.get('lastTradePrice'),
                            'best_buy_yes': contract.get('bestBuyYesCost'),
                            'best_buy_no': contract.get('bestBuyNoCost'),
                            'url': market.get('url'),
                            'end_date': market.get('dateEnd')
                        }
                        results.append(result)
            
            print(f"Filtered to {len(results)} climate/disaster-related contracts")
            return results
            
        except Exception as e:
            print(f"Error fetching PredictIt markets: {e}")
            return []
    
    def get_polymarket_markets(self, search_term='climate'):
        """
        Fetch markets from Polymarket
        Note: This uses their simplified API - full CLOB API requires more setup
        """
        print(f"\n{'='*60}")
        print("FETCHING POLYMARKET MARKETS")
        print(f"{'='*60}")
        
        try:
            # Using Gamma API (simpler public endpoint)
            url = "https://gamma-api.polymarket.com/markets"
            params = {
                'closed': False,
                'limit': 100
            }
            
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            print(f"Found {len(data)} total open markets")
            
            # Filter for relevant markets
            keywords = ['climate', 'weather', 'hurricane', 'disaster', 'temperature',
                       'wildfire', 'storm', 'flood', 'natural disaster', 'earthquake',
                       'insurance', 'catastrophe']
            
            results = []
            for market in data:
                question = market.get('question', '').lower()
                description = market.get('description', '').lower()
                
                if any(keyword in question or keyword in description for keyword in keywords):
                    result = {
                        'platform': 'Polymarket',
                        'question': market.get('question'),
                        'description': market.get('description'),
                        'outcomes': market.get('outcomes'),
                        'volume': market.get('volume'),
                        'liquidity': market.get('liquidity'),
                        'end_date': market.get('endDate'),
                        'url': f"https://polymarket.com/event/{market.get('slug', '')}"
                    }
                    results.append(result)
            
            print(f"Filtered to {len(results)} climate/disaster-related markets")
            return results
            
        except Exception as e:
            print(f"Error fetching Polymarket markets: {e}")
            return []
    
    def get_all_markets(self):
        """Fetch from all platforms"""
        all_results = {
            'kalshi': self.get_kalshi_markets(search_term='climate'),
            'predictit': self.get_predictit_markets(),
            'polymarket': self.get_polymarket_markets(),
            'timestamp': datetime.now().isoformat(),
            'total_markets': 0
        }
        
        all_results['total_markets'] = (
            len(all_results['kalshi']) + 
            len(all_results['predictit']) + 
            len(all_results['polymarket'])
        )
        
        return all_results
    
    def save_to_json(self, data, filename='/home/claude/prediction_markets.json'):
        """Save results to JSON"""
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\n{'='*60}")
        print(f"Saved results to: {filename}")
        print(f"{'='*60}")
        return filename
    
    def print_summary(self, data):
        """Print a nice summary of findings"""
        print(f"\n{'='*60}")
        print("PREDICTION MARKET SUMMARY")
        print(f"{'='*60}")
        print(f"Total markets found: {data['total_markets']}")
        print(f"  - Kalshi: {len(data['kalshi'])}")
        print(f"  - PredictIt: {len(data['predictit'])}")
        print(f"  - Polymarket: {len(data['polymarket'])}")
        
        # Show some examples
        print(f"\n{'='*60}")
        print("SAMPLE KALSHI MARKETS")
        print(f"{'='*60}")
        for market in data['kalshi'][:5]:
            print(f"\n{market['title']}")
            print(f"  Ticker: {market['ticker']}")
            if market['yes_price']:
                print(f"  Yes Price: ${market['yes_price']:.2f} ({market['yes_price']*100:.0f}%)")
            print(f"  Volume: {market['volume']:,}" if market['volume'] else "  Volume: N/A")
            print(f"  URL: {market['url']}")
        
        if data['predictit']:
            print(f"\n{'='*60}")
            print("SAMPLE PREDICTIT MARKETS")
            print(f"{'='*60}")
            for market in data['predictit'][:3]:
                print(f"\n{market['market_name']}")
                print(f"  Contract: {market['contract_name']}")
                if market['yes_price']:
                    print(f"  Last Price: ${market['yes_price']:.2f}")
                print(f"  URL: {market['url']}")
        
        if data['polymarket']:
            print(f"\n{'='*60}")
            print("SAMPLE POLYMARKET MARKETS")
            print(f"{'='*60}")
            for market in data['polymarket'][:3]:
                print(f"\n{market['question']}")
                print(f"  Volume: ${market['volume']:,.0f}" if market['volume'] else "  Volume: N/A")
                print(f"  URL: {market['url']}")


class InsuranceStockPredictions:
    """
    Track prediction markets for insurance company stocks
    (This would integrate with stock market prediction markets)
    """
    
    def __init__(self):
        self.insurance_tickers = ['RNR', 'EG', 'ACGL', 'AIG', 'TRV', 'CB', 'ALL', 'PGR']
    
    def search_company_predictions(self, ticker):
        """Search for prediction markets about a specific insurance company"""
        # This would search across platforms for markets about stock prices,
        # earnings, acquisitions, etc. for insurance companies
        pass


def main():
    """Main execution"""
    print("=" * 60)
    print("PREDICTION MARKET SCRAPER FOR RISK MARKET NEWS")
    print("=" * 60)
    
    scraper = PredictionMarketScraper()
    
    # Fetch all markets
    results = scraper.get_all_markets()
    
    # Print summary
    scraper.print_summary(results)
    
    # Save to file
    output_file = scraper.save_to_json(results)
    
    print("\n" + "=" * 60)
    print("NEXT STEPS")
    print("=" * 60)
    print("1. Review the JSON output for relevant markets")
    print("2. Set up automated scraping (cron job)")
    print("3. Correlate with your cat loss data")
    print("4. Create price tracking charts")
    print("5. Build alerts for major price movements")
    
    return results


if __name__ == "__main__":
    main()
