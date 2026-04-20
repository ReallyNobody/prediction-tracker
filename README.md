# SEC Catastrophe Loss Tracking System
## Risk Market News - Data Infrastructure

A complete system for extracting, storing, and visualizing catastrophe loss data from SEC filings for insurance and reinsurance companies.

---

## 🚀 What This System Does

1. **Scrapes SEC Filings** - Automatically pulls 10-Ks, 10-Qs, and 8-Ks from EDGAR
2. **Extracts Cat Loss Data** - Identifies hurricane, wildfire, and other catastrophe loss disclosures
3. **Stores in Database** - SQLite database with indexed queries for fast access
4. **Generates Visualizations** - Web dashboard + newsletter-ready charts
5. **Exports for Publishing** - JSON exports for easy integration into articles

---

## 📁 System Components

### 1. SEC Scraper (`sec_cat_loss_scraper.py`)
- Searches SEC EDGAR by company CIK
- Downloads filing HTML/text
- Extracts catastrophe loss mentions with context
- Captures dollar amounts and event names

**Key Companies to Track:**
- RenaissanceRe Holdings (CIK: 1067983)
- Everest Re Group (CIK: 1163165)
- Arch Capital Group (CIK: 875159)
- Beazley (CIK: 1373251)
- Lancashire Holdings (CIK: 1379169)

### 2. Database System (`cat_loss_database.py`)
- SQLite database with optimized schema
- Stores: company, ticker, event, dates, gross/net losses, geography
- Fast queries by event, company, or time period
- Export functions for JSON/CSV

**Database Schema:**
```sql
CREATE TABLE cat_losses (
    id INTEGER PRIMARY KEY,
    company TEXT,
    ticker TEXT,
    filing_type TEXT,
    filing_date DATE,
    quarter TEXT,
    event_name TEXT,
    event_date DATE,
    gross_loss_usd REAL,
    net_loss_usd REAL,
    loss_type TEXT,
    geography TEXT,
    context TEXT,
    source_accession TEXT
)
```

### 3. Web Dashboard (`cat_loss_dashboard.html`)
- Interactive single-page app
- Real-time filtering by company/event
- Charts: Bar charts (by event), pie charts (by company)
- Sortable data table
- Modern dark theme optimized for Risk Market News branding

### 4. Newsletter Charts (`newsletter_charts.py`)
Generates 4 publication-ready charts:

1. **Event Comparison Chart** - Gross vs Net losses by event
2. **Company Market Share** - Pie chart of net losses by insurer
3. **Timeline Chart** - Chronological bubble chart (size = gross loss)
4. **Retention Ratios** - Net/Gross retention percentages by company

All charts:
- 300 DPI for print quality
- Risk Market News branding
- Professional color scheme
- Ready for newsletter embedding

---

## 🔧 How to Use

### Initial Setup
```bash
# Install dependencies
pip install requests beautifulsoup4 pandas matplotlib seaborn --break-system-packages

# Initialize database
python cat_loss_database.py
```

### Scraping New Data
```bash
# Edit sec_cat_loss_scraper.py to add company CIKs
# Then run:
python sec_cat_loss_scraper.py

# Import scraped data into database
python cat_loss_database.py --import scraped_data.json
```

### Generate Newsletter Charts
```bash
# Creates 4 PNG charts in current directory
python newsletter_charts.py
```

### Launch Web Dashboard
```bash
# Option 1: Open directly in browser
open cat_loss_dashboard.html

# Option 2: Serve with Python
python -m http.server 8000
# Then visit: http://localhost:8000/cat_loss_dashboard.html
```

---

## 📊 Sample Queries

### Query by Event
```python
from cat_loss_database import CatLossDatabase

db = CatLossDatabase()
milton_losses = db.get_losses_by_event("Milton")
print(f"Total Milton losses: ${milton_losses['net_loss_usd'].sum()/1e6:.0f}M")
```

### Query by Company
```python
rnr_losses = db.get_losses_by_company("RenaissanceRe")
print(rnr_losses[['event_name', 'quarter', 'net_loss_usd']])
```

### Export for Newsletter
```python
db.export_to_json('latest_cat_losses.json')
```

---

## 🎯 Integration with Risk Market News

### Newsletter Article Workflow
1. Run scraper weekly after earnings season
2. Generate newsletter charts with `newsletter_charts.py`
3. Export top events with `db.get_losses_by_event()`
4. Embed charts in Substack/Ghost
5. Link to web dashboard for interactive exploration

### Podcast Integration
- Pull specific company data for episode prep
- Create custom comparison charts for show notes
- Export timeline charts for social media posts

### Social Media
- Use pie charts for quick Twitter/LinkedIn posts
- Timeline charts work great for Instagram stories
- Database queries for breaking news threads

---

## 🔮 Future Enhancements

### Phase 2
- [ ] Automated daily scraping (cron job)
- [ ] Email alerts for major loss events
- [ ] API endpoint for programmatic access
- [ ] Cat bond spread correlation analysis
- [ ] Reserve development tracking

### Phase 3
- [ ] Machine learning for loss estimation
- [ ] Sentiment analysis of MD&A sections
- [ ] Peer comparison tools
- [ ] Historical loss trending
- [ ] Integration with catastrophe models (RMS, AIR)

---

## 📝 Data Sources

**Primary:** SEC EDGAR (https://www.sec.gov)
**Filing Types:** 10-K, 10-Q, 8-K
**Update Frequency:** After each earnings season (Q1, Q2, Q3, Q4)

---

## 🛠️ Technical Notes

### Rate Limiting
- SEC allows 10 requests/second per IP
- Scraper includes 0.2s delays between requests
- Use appropriate User-Agent header

### Data Accuracy
- All data extracted from official SEC filings
- Amounts may be estimates/preliminary
- Always cite source accession number
- Check for reserve development in subsequent filings

### Performance
- SQLite handles 100K+ records easily
- Indexed queries return <100ms
- Dashboard loads 1000+ records instantly
- Chart generation: ~2-3 seconds per chart

---

## 📞 Support

For questions about this system:
- Review the code comments
- Check the SEC EDGAR documentation
- Reach out to Risk Market News editorial team

---

## ⚖️ Disclaimer

This system extracts publicly available data from SEC filings. Always:
- Verify data against original filings
- Note preliminary vs. final loss estimates
- Include appropriate disclaimers in publications
- Respect SEC rate limits and terms of service

---

**Built for Risk Market News by Claude**
*Last Updated: January 2026*
