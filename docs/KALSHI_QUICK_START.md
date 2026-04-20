# Kalshi Weather Market Scraper - Quick Start Guide

## One-Time Setup (Do This Once)

### 1. Get Your Kalshi API Credentials
1. Go to https://kalshi.com and log in
2. Navigate to **Account & security** → **API Keys**
3. Click **Create Key**
4. Save both:
   - **API Key ID** (copy the UUID string shown on screen)
   - **Private Key** (downloads as a .key file)

### 2. Install Required Python Libraries
Open Terminal and run:
```bash
pip3 install requests cryptography python-dotenv --break-system-packages
```

### 3. Set Up Your Project Folder
```bash
cd ~/Desktop/RMN/Database/risk-market-news
```

### 4. Move Your Private Key File
Move the downloaded .key file to your project folder:
```bash
mv ~/Downloads/kalshi-key*.key ~/Desktop/RMN/Database/risk-market-news/kalshi-private-key.key
```

### 5. Create Your .env File
```bash
nano .env
```

Paste these two lines (replace with YOUR actual values):
```
KALSHI_API_KEY=your-api-key-id-from-step-1
KALSHI_PRIVATE_KEY_PATH=/Users/chriswestfall/Desktop/RMN/Database/risk-market-news/kalshi-private-key.key
```

**Important:**
- No quotes around values
- No spaces around the = sign
- Use your actual API Key ID from Kalshi
- Check the path matches where you saved the .key file

Save and exit:
- Press `Ctrl+X`
- Press `Y`
- Press `Enter`

### 6. Download the Scraper
Download `kalshi_authenticated_scraper.py` from the outputs and save it to:
```
/Users/chriswestfall/Desktop/RMN/Database/risk-market-news/
```

---

## Daily Use (Run Anytime)

### Run the Scraper
```bash
cd ~/Desktop/RMN/Database/risk-market-news
python3 kalshi_authenticated_scraper.py
```

### View Your Data
The scraper saves data to `kalshi_weather_markets.json`:
```bash
cat kalshi_weather_markets.json | head -50
```

Or open it in any text editor to see the full data.

---

## What Gets Scraped

**24 weather market series:**
- High temperatures: NYC, LA, Chicago, Austin, Miami, SF, Seattle, Vegas, Philly, New Orleans, DC, Denver, Boston
- Low temperatures: LA, NYC, Chicago, Austin, Miami, Philly, Denver
- Snow: NYC monthly, LA monthly
- Rain: San Francisco monthly
- Climate: Hottest month records

**258+ total active markets** (varies daily)

---

## Output Format

Each market includes:
- `title`: Market question (e.g., "Will NYC high temp be 54-55° on Mar 29?")
- `yes_bid` / `no_bid`: Current bid prices
- `volume_24h`: 24-hour trading volume
- `open_interest`: Total open positions
- `close_time`: When market expires
- `url`: Direct link to trade on Kalshi

---

## Troubleshooting

**Error: "Missing credentials"**
- Check your .env file exists: `cat .env`
- Make sure it has exactly 2 lines with no extra spaces

**Error: "Private key not found"**
- Check the file exists: `ls -la kalshi-private-key.key`
- Make sure the path in .env matches the actual file location

**Error: "400 Bad Request"**
- Your API key might be wrong - check you copied it correctly
- Make sure you're using production API keys (not demo)

**Error: "401 Unauthorized"**
- Your API key or signature is invalid
- Regenerate your API keys from Kalshi and update .env

---

## Tips

- **Run daily** to track price movements
- **Compare volumes** to find most-traded markets
- **Track probabilities** for weather event forecasting
- **Correlate with your SEC cat loss data** for insights

---

## Next Steps

Once you have the data, you can:
1. Build visualizations showing probability trends
2. Compare market forecasts vs actual NWS temperatures
3. Track which cities have highest trading volume
4. Integrate with your newsletter for weather-related stories
5. Correlate temperature extremes with catastrophe loss patterns

---

**Questions?** Check the full README.md or review the scraper code for details.
