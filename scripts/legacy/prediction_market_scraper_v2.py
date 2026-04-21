#!/usr/bin/env python3
"""
Prediction Market Scraper - UPDATED VERSION
Improved filtering for climate, insurance, and catastrophe markets
Excludes sports teams and other false positives
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
        
        # VERY specific keywords for insurance/catastrophe markets
        # Avoid broad terms that might appear in sports
        self.include_keywords = [
            # Loss/damage terms (must include "loss" or "damage")
            'wildfire loss', 'hurricane loss', 'insured loss', 'catastrophe loss',
            'cat loss', 'property loss', 'insurance loss', 'economic loss from',
            'wildfire damage', 'hurricane damage', 'flood damage', 'earthquake damage',
            
            # Temperature markets (very common on Kalshi)
            'temperature will', 'degrees fahrenheit', 'degrees celsius',
            'high temperature', 'low temperature', 'average temperature',
            'temp above', 'temp below', 'warmest day', 'coldest day',
            'record high', 'record low', 'temperature forecast',
            'weather forecast temperature', 'daily high temp', 'daily low temp',
            
            # Climate/weather metrics (specific records)
            'hottest year on record', 'warmest year', 'temperature record',
            'climate record', 'wettest year', 'driest year',
            'global temperature', 'annual temperature',
            
            # Precipitation/weather
            'inches of rain', 'inches of snow', 'snowfall total',
            'rainfall total', 'precipitation amount',
            
            # Insurance industry specific
            'reinsurance capital', 'cat bond', 'catastrophe bond',
            'insurance merger', 'reinsurer acquisition', 'underwriting result',
            'combined ratio', 'loss ratio',
            
            # Regulatory/government
            'fema disaster declaration', 'fema major disaster', 'federal disaster',
            'natural disaster declaration', 'state of emergency climate',
            
            # Specific companies (insurance/reinsurance only)
            'renaissancere', 'everest re', 'everest group', 'arch capital', 
            'swiss re', 'munich re', 'hannover re', 'scor se',
            'berkshire hathaway reinsurance', 'aspen insurance',
            
            # Weather derivatives (very specific)
            'weather derivative market', 'temperature derivative', 
            'hdd index', 'cdd index', 'heating degree day', 'cooling degree day',
            
            # Specific measurable catastrophe events
            'named storm count', 'major hurricane landfall', 
            'category 5 hurricane', 'category 4 hurricane',
            'acres burned wildfire', 'billion-dollar disaster',
            'costliest natural disaster'
        ]
        
        # Exclude sports and other false positives
        self.exclude_keywords = [
            # Sports general
            'nhl', 'nba', 'nfl', 'mlb', 'mls', 'epl', 'nascar', 'ufc', 'mma',
            'stanley cup', 'playoffs', 'championship', 'super bowl',
            'world series', 'finals', 'ncaa', 'college basketball',
            
            # Sports teams
            'carolina hurricanes', 'miami heat', 'avalanche hockey',
            'thunder basketball', 'lightning hockey', 'jazz basketball',
            'heat basketball', 'storm basketball', 'wild hockey',
            
            # Sports betting terms
            'points scored', 'runs scored', 'goals scored',
            'wins by over', 'spread', 'moneyline', 'parlay',
            'point spread', 'over/under', 'prop bet',
            
            # Player names/stats (catches parlays)
            'assists', 'rebounds', 'touchdowns', 'home runs',
            'strikeouts', 'yards', 'field goals',
            
            # Other non-insurance
            'stock price will', 'share price', 'earnings per share',
            'election', 'senate', 'congress', 'president will',
            'video game', 'esports', 'gaming', 'movie', 'tv show',
            'album', 'song', 'artist'
        ]
    
    def is_relevant_market(self, text):
        """
        Improved relevance check
        Returns True only if text matches include keywords AND doesn't match exclude keywords
        """
        text_lower = text.lower()
        
        # First check: must contain at least one include keyword
        has_include = any(keyword in text_lower for keyword in self.include_keywords)
        
        if not has_include:
            return False
        
        # Second check: must NOT contain any exclude keywords
        has_exclude = any(keyword in text_lower for keyword in self.exclude_keywords)
        
        return not has_exclude
    
    def get_kalshi_markets(self):
        """
        Fetch markets from Kalshi - filtering by ticker patterns
        Weather markets have specific ticker prefixes like KXHIGH (high temp), KXLOW (low temp)
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
            
            # Filter by ticker patterns for weather/climate markets
            # Based on Kalshi's naming conventions:
            weather_prefixes = [
                'KXHIGH',      # High temperature markets
                'KXLOW',       # Low temperature markets
                'KXTEMP',      # Temperature markets
                'KXRAIN',      # Rainfall markets
                'KXSNOW',      # Snowfall markets
                'KXPRECIP',    # Precipitation markets
                'KXHURR',      # Hurricane markets
                'KXSTORM',     # Storm markets
                'KXFIRE',      # Wildfire markets
                'KXCLIMATE',   # Climate markets
                'KXWEATHER',   # General weather
                'KXHDD',       # Heating degree days
                'KXCDD',       # Cooling degree days
            ]
            
            # Also check event_ticker for these patterns
            filtered = []
            for market in markets:
                ticker = market.get('ticker', '').upper()
                event_ticker = market.get('event_ticker', '').upper()
                title = market.get('title', '').lower()
                
                # Check if ticker starts with weather prefix
                matches_ticker = any(ticker.startswith(prefix) for prefix in weather_prefixes)
                matches_event = any(event_ticker.startswith(prefix) for prefix in weather_prefixes)
                
                # Exclude sports parlays (they have KXMV prefix and sports keywords)
                is_sports_parlay = (
                    ticker.startswith('KXMV') or 
                    event_ticker.startswith('KXMV') or
                    self.has_sports_keywords(title)
                )
                
                if (matches_ticker or matches_event) and not is_sports_parlay:
                    filtered.append(market)
            
            markets = filtered
            print(f"Filtered to {len(markets)} weather/climate markets")
            
            # Format results
            results = []
            for market in markets:
                result = {
                    'platform': 'Kalshi',
                    'ticker': market.get('ticker'),
                    'title': market.get('title'),
                    'subtitle': market.get('yes_sub_title'),  # This often has better description
                    'yes_price': float(market.get('yes_bid_dollars', 0)),
                    'no_price': float(market.get('no_bid_dollars', 0)),
                    'volume': float(market.get('volume_fp', 0)),
                    'open_interest': float(market.get('open_interest_fp', 0)),
                    'close_date': market.get('close_time'),
                    'category': 'weather',  # We infer this from ticker
                    'url': f"https://kalshi.com/markets/{market.get('ticker')}"
                }
                results.append(result)
            
            return results
            
        except Exception as e:
            print(f"Error fetching Kalshi markets: {e}")
            return []
    
    def get_predictit_markets(self):
        """Fetch markets from PredictIt with improved filtering"""
        print(f"\n{'='*60}")
        print("FETCHING PREDICTIT MARKETS")
        print(f"{'='*60}")
        
        try:
            response = requests.get(self.predictit_base)
            response.raise_for_status()
            data = response.json()
            
            markets = data.get('markets', [])
            print(f"Found {len(markets)} total markets")
            
            results = []
            for market in markets:
                name = market.get('name', '')
                short_name = market.get('shortName', '')
                
                # Check if market is relevant
                combined_text = f"{name} {short_name}"
                
                if self.is_relevant_market(combined_text):
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
            
            print(f"Filtered to {len(results)} climate/disaster/insurance contracts")
            return results
            
        except Exception as e:
            print(f"Error fetching PredictIt markets: {e}")
            return []
    
    def get_polymarket_markets(self):
        """
        Fetch markets from Polymarket with improved filtering
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
            
            results = []
            for market in data:
                question = market.get('question', '')
                description = market.get('description', '')
                
                # Check if market is relevant
                combined_text = f"{question} {description}"
                
                if self.is_relevant_market(combined_text):
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
            
            print(f"Filtered to {len(results)} climate/disaster/insurance markets")
            return results
            
        except Exception as e:
            print(f"Error fetching Polymarket markets: {e}")
            return []
    
    def get_all_markets(self):
        """Fetch from all platforms"""
        all_results = {
            'kalshi': self.get_kalshi_markets(),
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
    
    def save_to_json(self, data, filename='prediction_markets.json'):
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
        
        # Show Kalshi markets
        if data['kalshi']:
            print(f"\n{'='*60}")
            print("KALSHI MARKETS")
            print(f"{'='*60}")
            for market in data['kalshi'][:10]:  # Show up to 10
                print(f"\n{market['title']}")
                if market.get('subtitle'):
                    print(f"  {market['subtitle']}")
                print(f"  Ticker: {market['ticker']}")
                if market['yes_price'] is not None:
                    print(f"  Yes Price: ${market['yes_price']:.2f} ({market['yes_price']*100:.0f}%)")
                vol = market.get('volume')
                if vol and isinstance(vol, (int, float)):
                    print(f"  Volume: {vol:,}")
                else:
                    print(f"  Volume: N/A")
                print(f"  URL: {market['url']}")
        
        # Show PredictIt markets
        if data['predictit']:
            print(f"\n{'='*60}")
            print("PREDICTIT MARKETS")
            print(f"{'='*60}")
            for market in data['predictit'][:10]:
                print(f"\n{market['market_name']}")
                print(f"  Contract: {market['contract_name']}")
                if market['yes_price']:
                    print(f"  Last Price: ${market['yes_price']:.2f}")
                print(f"  URL: {market['url']}")
        
        # Show Polymarket markets
        if data['polymarket']:
            print(f"\n{'='*60}")
            print("POLYMARKET MARKETS")
            print(f"{'='*60}")
            for market in data['polymarket'][:10]:
                print(f"\n{market['question']}")
                vol = market.get('volume')
                if vol and isinstance(vol, (int, float)):
                    print(f"  Volume: ${vol:,.0f}")
                else:
                    print(f"  Volume: N/A")
                print(f"  URL: {market['url']}")


def main():
    """Main execution"""
    print("=" * 60)
    print("PREDICTION MARKET SCRAPER FOR RISK MARKET NEWS")
    print("Updated version with improved filtering")
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
    print("2. Adjust keywords if needed (edit include_keywords/exclude_keywords)")
    print("3. Set up automated scraping (cron job)")
    print("4. Correlate with your cat loss data")
    print("5. Create price tracking charts")
    
    return results


if __name__ == "__main__":
    main()
