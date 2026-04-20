#!/usr/bin/env python3
"""
Prediction Market Scraper V3 - Direct Series Lookup
Uses Kalshi's series endpoint to directly fetch weather/climate market series
"""

import requests
import json
from datetime import datetime

class KalshiWeatherScraper:
    def __init__(self):
        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        
    def get_series_list(self):
        """Get list of all market series"""
        url = f"{self.base_url}/series"
        params = {'limit': 100}
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get('series', [])
        except Exception as e:
            print(f"Error fetching series: {e}")
            return []
    
    def get_markets_for_series(self, series_ticker):
        """Get all markets for a specific series"""
        url = f"{self.base_url}/series/{series_ticker}/markets"
        params = {'limit': 100, 'status': 'open'}
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get('markets', [])
        except Exception as e:
            print(f"Error fetching markets for {series_ticker}: {e}")
            return []
    
    def find_weather_series(self):
        """Find weather/climate related series"""
        print("Fetching Kalshi market series...")
        all_series = self.get_series_list()
        
        print(f"Found {len(all_series)} total series\n")
        
        # Keywords for weather/climate series
        weather_keywords = [
            'temperature', 'temp', 'weather', 'climate', 'rain', 'snow',
            'precipitation', 'high', 'low', 'forecast', 'degree',
            'hurricane', 'storm', 'wildfire', 'flood', 'disaster'
        ]
        
        weather_series = []
        for series in all_series:
            title = series.get('title', '').lower()
            ticker = series.get('ticker', '').lower()
            
            if any(kw in title or kw in ticker for kw in weather_keywords):
                # Exclude if it's clearly sports
                if not any(sport in title for sport in ['nba', 'nfl', 'nhl', 'mlb']):
                    weather_series.append(series)
        
        return weather_series
    
    def scrape_weather_markets(self):
        """Main scraping function"""
        print("=" * 60)
        print("KALSHI WEATHER MARKET SCRAPER")
        print("=" * 60)
        
        # Find weather series
        weather_series = self.find_weather_series()
        
        print(f"\nFound {len(weather_series)} weather/climate series:")
        for series in weather_series[:10]:
            print(f"  - {series.get('title')} ({series.get('ticker')})")
        
        # Get markets for each series
        all_markets = []
        for series in weather_series[:5]:  # Limit to first 5 for now
            ticker = series.get('ticker')
            print(f"\nFetching markets for: {series.get('title')}")
            
            markets = self.get_markets_for_series(ticker)
            print(f"  Found {len(markets)} open markets")
            
            for market in markets:
                all_markets.append({
                    'platform': 'Kalshi',
                    'series': series.get('title'),
                    'ticker': market.get('ticker'),
                    'title': market.get('title'),
                    'yes_price': float(market.get('yes_bid_dollars', 0)),
                    'no_price': float(market.get('no_bid_dollars', 0)),
                    'volume': float(market.get('volume_fp', 0)),
                    'close_date': market.get('close_time'),
                    'url': f"https://kalshi.com/markets/{market.get('ticker')}"
                })
        
        return all_markets


def main():
    scraper = KalshiWeatherScraper()
    
    # Scrape markets
    markets = scraper.scrape_weather_markets()
    
    # Print summary
    print("\n" + "=" * 60)
    print(f"TOTAL WEATHER MARKETS FOUND: {len(markets)}")
    print("=" * 60)
    
    if markets:
        print("\nSample markets:")
        for market in markets[:5]:
            print(f"\n{market['title']}")
            print(f"  Series: {market['series']}")
            print(f"  Yes: ${market['yes_price']:.2f}")
            print(f"  URL: {market['url']}")
    
    # Save to file
    with open('kalshi_weather_markets.json', 'w') as f:
        json.dump(markets, f, indent=2)
    
    print(f"\nSaved {len(markets)} markets to kalshi_weather_markets.json")


if __name__ == "__main__":
    main()
