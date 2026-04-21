#!/usr/bin/env python3
"""
Kalshi Authenticated Weather Scraper
Uses API key authentication to access weather market data
"""

import base64
import datetime
import json
import os
from urllib.parse import urlparse

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class KalshiAuthenticatedScraper:
    def __init__(self, api_key_id=None, private_key_path=None, demo=False):
        """
        Initialize with API credentials

        Args:
            api_key_id: Your Kalshi API key ID (or set KALSHI_API_KEY env var)
            private_key_path: Path to your .key file (or set KALSHI_PRIVATE_KEY_PATH env var)
            demo: Use demo API (default: False, uses production)
        """
        self.api_key_id = api_key_id or os.getenv("KALSHI_API_KEY")
        private_key_path = private_key_path or os.getenv("KALSHI_PRIVATE_KEY_PATH")

        if not self.api_key_id or not private_key_path:
            raise ValueError(
                "Missing credentials! Set KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH "
                "environment variables or pass them as arguments"
            )

        self.base_url = (
            "https://demo-api.kalshi.co/trade-api/v2"
            if demo
            else "https://api.elections.kalshi.com/trade-api/v2"
        )

        # Load private key
        self.private_key = self._load_private_key(private_key_path)

        print(f"✓ Initialized with API key: {self.api_key_id[:8]}...")
        print(f"✓ Using {'DEMO' if demo else 'PRODUCTION'} API")

    def _load_private_key(self, key_path):
        """Load RSA private key from file"""
        try:
            with open(key_path, "rb") as f:
                return serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )
        except FileNotFoundError:
            raise FileNotFoundError(f"Private key not found at: {key_path}")

    def _create_signature(self, timestamp, method, path):
        """Create request signature using RSA-PSS"""
        # Strip query parameters before signing
        path_without_query = path.split("?")[0]
        message = f"{timestamp}{method}{path_without_query}".encode()

        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )

        return base64.b64encode(signature).decode("utf-8")

    def get(self, path, params=None):
        """Make authenticated GET request"""
        timestamp = str(int(datetime.datetime.now().timestamp() * 1000))

        # Full path for signing includes /trade-api/v2
        sign_path = urlparse(self.base_url + path).path
        signature = self._create_signature(timestamp, "GET", sign_path)

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

        url = self.base_url + path
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        return response.json()

    def get_weather_markets(self):
        """Fetch all weather/climate markets"""
        print("\n" + "=" * 60)
        print("FETCHING WEATHER MARKETS FROM KALSHI")
        print("=" * 60)

        # Series tickers (not event tickers!)
        weather_series = [
            "KXHIGHLAX",
            "KXHIGHNY",
            "KXHIGHCHI",
            "KXHIGHAUS",
            "KXHIGHMIA",
            "KXHIGHTSFO",
            "KXHIGHTSEA",
            "KXHIGHTLV",
            "KXHIGHPHIL",
            "KXHIGHTNOLA",
            "KXHIGHTDC",
            "KXHIGHDEN",
            "KXHIGHTBOS",
            "KXLOWTLAX",
            "KXLOWTNYC",
            "KXLOWTCHI",
            "KXLOWTAUS",
            "KXLOWTMIA",
            "KXLOWTPHIL",
            "KXLOWTDEN",
            "KXNYCSNOWM",
            "KXLAXSNOWM",
            "KXRAINSFOM",
            "KXHMONTH",
        ]

        all_markets = []
        series_found = 0

        for series_ticker in weather_series:
            try:
                print(f"\nFetching {series_ticker}...", end=" ")

                # Fetch markets by series_ticker parameter
                response = self.get(
                    "/markets",
                    params={"series_ticker": series_ticker, "status": "active", "limit": 100},
                )

                markets = response.get("markets", [])

                if markets:
                    series_found += 1
                    print(f"✓ {len(markets)} active markets")

                    for market in markets:
                        all_markets.append(
                            {
                                "platform": "Kalshi",
                                "series_ticker": series_ticker,
                                "event_ticker": market.get("event_ticker"),
                                "ticker": market.get("ticker"),
                                "title": market.get("title"),
                                "subtitle": market.get("subtitle"),
                                "yes_bid": float(market.get("yes_bid_dollars", 0)),
                                "no_bid": float(market.get("no_bid_dollars", 0)),
                                "yes_ask": float(market.get("yes_ask_dollars", 0)),
                                "no_ask": float(market.get("no_ask_dollars", 0)),
                                "last_price": float(market.get("last_price_dollars", 0)),
                                "volume_24h": float(market.get("volume_24h_fp", 0)),
                                "volume_total": float(market.get("volume_fp", 0)),
                                "open_interest": float(market.get("open_interest_fp", 0)),
                                "close_time": market.get("close_time"),
                                "url": f"https://kalshi.com/markets/{market.get('ticker')}",
                            }
                        )
                else:
                    print("✗ No active markets")

            except requests.exceptions.HTTPError as e:
                print(f"✗ Error: {e}")
            except Exception as e:
                print(f"✗ Error: {e}")

        print(f"\n{'=' * 60}")
        print("SUMMARY")
        print(f"{'=' * 60}")
        print(f"Series checked: {len(weather_series)}")
        print(f"Series with markets: {series_found}")
        print(f"Total active markets: {len(all_markets)}")

        return all_markets

    def print_summary(self, markets):
        """Print market summary"""
        if not markets:
            return

        print(f"\n{'=' * 60}")
        print("TOP MARKETS BY 24H VOLUME")
        print(f"{'=' * 60}")

        # Sort by volume
        top_markets = sorted(markets, key=lambda x: x["volume_24h"], reverse=True)[:10]

        for i, market in enumerate(top_markets, 1):
            print(f"\n{i}. {market['event_title']}")
            print(f"   {market['title'][:60]}")
            print(f"   Yes: ${market['yes_bid']:.2f} | No: ${market['no_bid']:.2f}")
            print(f"   24h Vol: ${market['volume_24h']:,.0f}")
            print(f"   URL: {market['url']}")


def main():
    """Main execution"""
    print("=" * 60)
    print("KALSHI AUTHENTICATED WEATHER MARKET SCRAPER")
    print("=" * 60)

    try:
        # Initialize scraper (reads from .env file)
        scraper = KalshiAuthenticatedScraper(demo=False)  # Set demo=True for testing

        # Fetch weather markets
        markets = scraper.get_weather_markets()

        # Print summary
        scraper.print_summary(markets)

        # Save to file
        output_file = "kalshi_weather_markets.json"
        with open(output_file, "w") as f:
            json.dump(markets, f, indent=2)

        print(f"\n{'=' * 60}")
        print(f"✓ Saved {len(markets)} markets to {output_file}")
        print(f"{'=' * 60}\n")

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        print("\nMake sure you've set up your .env file with:")
        print("  KALSHI_API_KEY=your-api-key-id")
        print("  KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi-key.key")


if __name__ == "__main__":
    main()
