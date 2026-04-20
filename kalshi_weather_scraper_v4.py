#!/usr/bin/env python3
"""
Kalshi Weather Scraper V4 - Direct Event Fetching
Targets known weather event series directly
"""

import requests
import json
from datetime import datetime

class KalshiWeatherScraperV4:
    def __init__(self):
        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        
        # Known weather event series from the website
        self.weather_events = [
            # High temperatures
            'KXHIGHLAX',    # LA
            'KXHIGHNY',     # NYC
            'KXHIGHCHI',    # Chicago
            'KXHIGHAUS',    # Austin
            'KXHIGHMIA',    # Miami
            'KXHIGHTSFO',   # San Francisco
            'KXHIGHTSEA',   # Seattle
            'KXHIGHTLV',    # Las Vegas
            'KXHIGHPHIL',   # Philadelphia
            'KXHIGHTNOLA',  # New Orleans
            'KXHIGHTDC',    # DC
            'KXHIGHDEN',    # Denver
            'KXHIGHTBOS',   # Boston
            
            # Low temperatures
            'KXLOWTLAX',    # LA
            'KXLOWTNYC',    # NYC
            'KXLOWTCHI',    # Chicago
            'KXLOWTAUS',    # Austin
            'KXLOWTMIA',    # Miami
            'KXLOWTPHIL',   # Philadelphia
            'KXLOWTDEN',    # Denver
            
            # Snow/rain
            'KXNYCSNOWM',   # NYC snow monthly
            'KXLAXSNOWM',   # LA snow
            'KXRAINSFOM',   # SF rain
            
            # Climate change
            'KXHMONTH',     # Hottest month
        ]
    
    def get_event_markets(self, event_ticker):
        """Get markets for a specific event"""
        url = f"{self.base_url}/events/{event_ticker}"
        
        try:
            response = requests.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            
            event_data = response.json()
            event = event_data.get('event', {})
            markets = event_data.get('markets', [])
            
            return {
                'event': event,
                'markets': markets
            }
        except Exception as e:
            return None
    
    def scrape_all_weather_markets(self):
        """Scrape all known weather markets"""
        print("=" * 60)
        print("KALSHI WEATHER MARKET SCRAPER V4")
        print("=" * 60)
        
        all_markets = []
        events_found = 0
        
        for event_ticker in self.weather_events:
            print(f"\nFetching: {event_ticker}...", end=" ")
            
            data = self.get_event_markets(event_ticker)
            
            if data and data['markets']:
                event = data['event']
                markets = data['markets']
                events_found += 1
                
                print(f"✓ {len(markets)} markets")
                
                for market in markets:
                    if market.get('status') == 'active':
                        all_markets.append({
                            'platform': 'Kalshi',
                            'event_ticker': event_ticker,
                            'event_title': event.get('title', ''),
                            'ticker': market.get('ticker'),
                            'title': market.get('title'),
                            'yes_price': float(market.get('yes_bid_dollars', 0)),
                            'no_price': float(market.get('no_bid_dollars', 0)),
                            'yes_ask': float(market.get('yes_ask_dollars', 0)),
                            'no_ask': float(market.get('no_ask_dollars', 0)),
                            'volume_24h': float(market.get('volume_24h_fp', 0)),
                            'volume_total': float(market.get('volume_fp', 0)),
                            'open_interest': float(market.get('open_interest_fp', 0)),
                            'close_date': market.get('close_time'),
                            'url': f"https://kalshi.com/markets/{market.get('ticker')}"
                        })
            else:
                print("✗ No active markets")
        
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Events checked: {len(self.weather_events)}")
        print(f"Events with active markets: {events_found}")
        print(f"Total active markets: {len(all_markets)}")
        
        return all_markets
    
    def print_summary(self, markets):
        """Print summary of markets"""
        if not markets:
            print("\nNo active markets found")
            return
        
        print(f"\n{'='*60}")
        print("SAMPLE MARKETS")
        print(f"{'='*60}")
        
        # Group by event
        by_event = {}
        for m in markets:
            event = m['event_ticker']
            if event not in by_event:
                by_event[event] = []
            by_event[event].append(m)
        
        # Show top 5 events by volume
        event_volumes = {e: sum(m['volume_24h'] for m in ms) for e, ms in by_event.items()}
        top_events = sorted(event_volumes.items(), key=lambda x: x[1], reverse=True)[:5]
        
        for event_ticker, vol in top_events:
            event_markets = by_event[event_ticker]
            print(f"\n{event_markets[0]['event_title']}")
            print(f"  Event: {event_ticker}")
            print(f"  24h Volume: ${vol:,.0f}")
            print(f"  Active markets: {len(event_markets)}")
            
            # Show top market
            if event_markets:
                top = max(event_markets, key=lambda x: x['yes_price'])
                print(f"  Top prediction: {top['title'][:50]}")
                print(f"    Yes: ${top['yes_price']:.2f} | No: ${top['no_price']:.2f}")


def main():
    scraper = KalshiWeatherScraperV4()
    
    # Scrape all markets
    markets = scraper.scrape_all_weather_markets()
    
    # Print summary
    scraper.print_summary(markets)
    
    # Save to JSON
    output_file = 'kalshi_weather_markets_v4.json'
    with open(output_file, 'w') as f:
        json.dump(markets, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"Saved {len(markets)} markets to {output_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
