#!/usr/bin/env python3
"""
Prediction Market Tracker & Visualizer
Demo with sample data showing what the system can track
"""

import json
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

# Sample prediction market data (what the scraper would return)
SAMPLE_PREDICTION_MARKETS = {
    "timestamp": "2026-01-19T15:45:00",
    "total_markets": 12,
    "kalshi": [
        {
            "platform": "Kalshi",
            "ticker": "CLIMATE-TEMP-2026",
            "title": "Will 2026 be the hottest year on record?",
            "yes_price": 0.68,
            "no_price": 0.32,
            "volume": 125000,
            "open_interest": 45000,
            "close_date": "2026-12-31",
            "category": "Climate",
            "url": "https://kalshi.com/markets/CLIMATE-TEMP-2026"
        },
        {
            "platform": "Kalshi",
            "ticker": "HURR-ATL-2026",
            "title": "Will a Category 5 hurricane hit the US in 2026?",
            "yes_price": 0.23,
            "no_price": 0.77,
            "volume": 89000,
            "open_interest": 32000,
            "close_date": "2026-11-30",
            "category": "Weather",
            "url": "https://kalshi.com/markets/HURR-ATL-2026"
        },
        {
            "platform": "Kalshi",
            "ticker": "WILDFIRE-CA-Q1",
            "title": "Will California wildfire losses exceed $5B in Q1 2026?",
            "yes_price": 0.82,
            "no_price": 0.18,
            "volume": 156000,
            "open_interest": 67000,
            "close_date": "2026-03-31",
            "category": "Disaster",
            "url": "https://kalshi.com/markets/WILDFIRE-CA-Q1"
        },
        {
            "platform": "Kalshi",
            "ticker": "INS-CAP-RNR",
            "title": "Will RenaissanceRe raise capital in 2026?",
            "yes_price": 0.45,
            "no_price": 0.55,
            "volume": 42000,
            "open_interest": 18000,
            "close_date": "2026-12-31",
            "category": "Insurance",
            "url": "https://kalshi.com/markets/INS-CAP-RNR"
        }
    ],
    "predictit": [
        {
            "platform": "PredictIt",
            "market_id": 8745,
            "market_name": "Will FEMA declare a major disaster in California in January 2026?",
            "contract_name": "Yes",
            "yes_price": 0.89,
            "best_buy_yes": 0.90,
            "best_buy_no": 0.11,
            "url": "https://www.predictit.org/markets/detail/8745",
            "end_date": "2026-01-31"
        },
        {
            "platform": "PredictIt",
            "market_id": 8623,
            "market_name": "Will global insured losses exceed $100B in 2026?",
            "contract_name": "Yes",
            "yes_price": 0.64,
            "best_buy_yes": 0.65,
            "best_buy_no": 0.36,
            "url": "https://www.predictit.org/markets/detail/8623",
            "end_date": "2026-12-31"
        }
    ],
    "polymarket": [
        {
            "platform": "Polymarket",
            "question": "Will a major earthquake (7.0+) hit California in 2026?",
            "description": "Resolves YES if USGS records a 7.0+ magnitude earthquake in California",
            "outcomes": ["Yes", "No"],
            "volume": 2450000,
            "liquidity": 890000,
            "end_date": "2026-12-31",
            "url": "https://polymarket.com/event/ca-earthquake-2026"
        },
        {
            "platform": "Polymarket",
            "question": "Will hurricane season 2026 be above average?",
            "description": "Resolves YES if NOAA declares 2026 above-average hurricane season",
            "outcomes": ["Yes", "No"],
            "volume": 1850000,
            "liquidity": 645000,
            "end_date": "2026-11-30",
            "url": "https://polymarket.com/event/hurricane-2026"
        },
        {
            "platform": "Polymarket",
            "question": "Will Berkshire Hathaway acquire a reinsurer in 2026?",
            "description": "Resolves YES if Berkshire announces acquisition of major reinsurer",
            "outcomes": ["Yes", "No"],
            "volume": 3200000,
            "liquidity": 1200000,
            "end_date": "2026-12-31",
            "url": "https://polymarket.com/event/brk-reinsurance-2026"
        }
    ]
}


class PredictionMarketAnalyzer:
    def __init__(self, data):
        self.data = data
        self.brand_colors = {
            'kalshi': '#00D4AA',
            'predictit': '#EE6123',
            'polymarket': '#7C3AED',
            'primary': '#3b82f6'
        }
    
    def create_probability_comparison(self, output_path='/home/claude/prediction_probabilities.png'):
        """Compare implied probabilities across platforms"""
        fig, ax = plt.subplots(figsize=(14, 8), facecolor='white')
        
        # Extract key markets
        markets_data = []
        
        # Kalshi markets
        for m in self.data['kalshi']:
            markets_data.append({
                'title': m['title'][:50] + '...' if len(m['title']) > 50 else m['title'],
                'probability': m['yes_price'] * 100,
                'platform': 'Kalshi',
                'volume': m['volume']
            })
        
        # PredictIt markets
        for m in self.data['predictit']:
            markets_data.append({
                'title': m['market_name'][:50] + '...' if len(m['market_name']) > 50 else m['market_name'],
                'probability': m['yes_price'] * 100,
                'platform': 'PredictIt',
                'volume': 0  # PredictIt doesn't always show volume
            })
        
        df = pd.DataFrame(markets_data)
        df = df.sort_values('probability', ascending=True)
        
        # Create horizontal bar chart
        y_pos = range(len(df))
        colors = [self.brand_colors.get(p.lower(), self.brand_colors['primary']) 
                 for p in df['platform']]
        
        bars = ax.barh(y_pos, df['probability'], color=colors, alpha=0.8)
        
        ax.set_yticks(y_pos)
        ax.set_yticklabels(df['title'], fontsize=9)
        ax.set_xlabel('Implied Probability (%)', fontsize=12, weight='bold')
        ax.set_title('Prediction Market Probabilities\nClimate & Insurance Events 2026',
                    fontsize=16, weight='bold', pad=20)
        
        # Add value labels
        for i, (prob, platform) in enumerate(zip(df['probability'], df['platform'])):
            ax.text(prob + 1, i, f'{prob:.0f}%', va='center', fontsize=10, weight='bold')
        
        # Add legend
        legend_elements = [
            plt.Rectangle((0,0),1,1, fc=self.brand_colors['kalshi'], alpha=0.8, label='Kalshi'),
            plt.Rectangle((0,0),1,1, fc=self.brand_colors['predictit'], alpha=0.8, label='PredictIt'),
            plt.Rectangle((0,0),1,1, fc=self.brand_colors['polymarket'], alpha=0.8, label='Polymarket')
        ]
        ax.legend(handles=legend_elements, loc='lower right', fontsize=10)
        
        ax.grid(axis='x', alpha=0.3)
        ax.set_xlim(0, 100)
        
        # Branding
        fig.text(0.99, 0.01, 'Risk Market News | Source: Kalshi, PredictIt, Polymarket',
                ha='right', fontsize=9, style='italic', color='gray')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Probability comparison chart saved: {output_path}")
        return output_path
    
    def create_volume_analysis(self, output_path='/home/claude/market_volume.png'):
        """Analyze trading volume across markets"""
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6), facecolor='white')
        
        # Chart 1: Volume by platform
        platform_volumes = {
            'Kalshi': sum(m.get('volume', 0) for m in self.data['kalshi']),
            'Polymarket': sum(m.get('volume', 0) for m in self.data['polymarket'])
        }
        
        colors = [self.brand_colors['kalshi'], self.brand_colors['polymarket']]
        ax1.pie(platform_volumes.values(), labels=platform_volumes.keys(),
               autopct='%1.1f%%', colors=colors, startangle=90,
               textprops={'fontsize': 12, 'weight': 'bold'})
        ax1.set_title('Trading Volume by Platform\n(Climate/Insurance Markets)',
                     fontsize=14, weight='bold')
        
        # Chart 2: Top markets by volume
        kalshi_df = pd.DataFrame(self.data['kalshi'])
        kalshi_df = kalshi_df.nsmallest(4, 'volume')  # Get lowest for better chart
        
        bars = ax2.barh(range(len(kalshi_df)), kalshi_df['volume'] / 1000,
                       color=self.brand_colors['kalshi'], alpha=0.8)
        ax2.set_yticks(range(len(kalshi_df)))
        ax2.set_yticklabels([t[:40] + '...' if len(t) > 40 else t 
                            for t in kalshi_df['title']], fontsize=9)
        ax2.set_xlabel('Trading Volume (thousands)', fontsize=11, weight='bold')
        ax2.set_title('Market Trading Volume\nKalshi Climate/Insurance Markets',
                     fontsize=14, weight='bold')
        ax2.grid(axis='x', alpha=0.3)
        
        # Add value labels
        for i, vol in enumerate(kalshi_df['volume']):
            ax2.text(vol / 1000 + 2, i, f'${vol/1000:.0f}K',
                    va='center', fontsize=10, weight='bold')
        
        # Branding
        fig.text(0.99, 0.01, 'Risk Market News | Source: Prediction Markets',
                ha='right', fontsize=9, style='italic', color='gray')
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Volume analysis chart saved: {output_path}")
        return output_path
    
    def create_event_risk_dashboard(self, output_path='/home/claude/event_risk_dashboard.png'):
        """Create a risk dashboard combining cat loss data with prediction markets"""
        fig = plt.figure(figsize=(16, 10), facecolor='white')
        gs = fig.add_gridspec(3, 2, hspace=0.3, wspace=0.3)
        
        # Top left: High probability events
        ax1 = fig.add_subplot(gs[0, 0])
        high_risk = pd.DataFrame(self.data['kalshi'])
        high_risk = high_risk.nlargest(5, 'yes_price')
        
        ax1.barh(range(len(high_risk)), high_risk['yes_price'] * 100,
                color='#ef4444', alpha=0.8)
        ax1.set_yticks(range(len(high_risk)))
        ax1.set_yticklabels([t[:35] for t in high_risk['title']], fontsize=9)
        ax1.set_xlabel('Probability (%)', fontsize=10, weight='bold')
        ax1.set_title('🔴 High Risk Events (>50%)', fontsize=12, weight='bold')
        ax1.grid(axis='x', alpha=0.3)
        
        # Top right: Market liquidity
        ax2 = fig.add_subplot(gs[0, 1])
        poly_df = pd.DataFrame(self.data['polymarket'])
        
        ax2.scatter(poly_df['liquidity'] / 1e6, poly_df['volume'] / 1e6,
                   s=200, alpha=0.6, c=self.brand_colors['polymarket'])
        ax2.set_xlabel('Liquidity ($M)', fontsize=10, weight='bold')
        ax2.set_ylabel('Volume ($M)', fontsize=10, weight='bold')
        ax2.set_title('💰 Market Depth\n(Polymarket)', fontsize=12, weight='bold')
        ax2.grid(True, alpha=0.3)
        
        # Middle: Timeline of events
        ax3 = fig.add_subplot(gs[1, :])
        
        # Create timeline data
        events = []
        for m in self.data['kalshi']:
            events.append({
                'date': datetime.strptime(m['close_date'], '%Y-%m-%d'),
                'event': m['title'][:30],
                'prob': m['yes_price']
            })
        
        events_df = pd.DataFrame(events).sort_values('date')
        
        colors_map = ['#ef4444' if p > 0.5 else '#f59e0b' if p > 0.3 else '#10b981' 
                     for p in events_df['prob']]
        
        ax3.scatter(events_df['date'], events_df['prob'] * 100,
                   s=300, c=colors_map, alpha=0.7, edgecolors='black', linewidth=2)
        
        for idx, row in events_df.iterrows():
            ax3.annotate(row['event'], xy=(row['date'], row['prob'] * 100),
                        xytext=(0, 10), textcoords='offset points',
                        fontsize=8, ha='center',
                        bbox=dict(boxstyle='round,pad=0.3', fc='white', alpha=0.8))
        
        ax3.set_ylabel('Probability (%)', fontsize=11, weight='bold')
        ax3.set_title('📅 Event Timeline & Risk Assessment', fontsize=13, weight='bold')
        ax3.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        
        # Bottom left: Category breakdown
        ax4 = fig.add_subplot(gs[2, 0])
        categories = {}
        for m in self.data['kalshi']:
            cat = m.get('category', 'Other')
            categories[cat] = categories.get(cat, 0) + 1
        
        ax4.pie(categories.values(), labels=categories.keys(),
               autopct='%1.0f%%', startangle=90,
               colors=['#3b82f6', '#ef4444', '#10b981', '#f59e0b'])
        ax4.set_title('📊 Markets by Category', fontsize=12, weight='bold')
        
        # Bottom right: Platform comparison
        ax5 = fig.add_subplot(gs[2, 1])
        platform_counts = {
            'Kalshi': len(self.data['kalshi']),
            'PredictIt': len(self.data['predictit']),
            'Polymarket': len(self.data['polymarket'])
        }
        
        bars = ax5.bar(platform_counts.keys(), platform_counts.values(),
                      color=[self.brand_colors['kalshi'], 
                            self.brand_colors['predictit'],
                            self.brand_colors['polymarket']],
                      alpha=0.8)
        
        ax5.set_ylabel('Number of Markets', fontsize=10, weight='bold')
        ax5.set_title('🏪 Markets per Platform', fontsize=12, weight='bold')
        ax5.grid(axis='y', alpha=0.3)
        
        for bar, count in zip(bars, platform_counts.values()):
            height = bar.get_height()
            ax5.text(bar.get_x() + bar.get_width()/2., height,
                    f'{int(count)}', ha='center', va='bottom', fontsize=11, weight='bold')
        
        # Overall title and branding
        fig.suptitle('🌪️ Catastrophe Risk Intelligence Dashboard\nPrediction Markets + Insurance Data',
                    fontsize=18, weight='bold', y=0.98)
        
        fig.text(0.99, 0.01, 'Risk Market News | Real-time prediction market data',
                ha='right', fontsize=9, style='italic', color='gray')
        
        plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
        print(f"Event risk dashboard saved: {output_path}")
        return output_path
    
    def generate_newsletter_summary(self):
        """Generate text summary for newsletter"""
        summary = []
        summary.append("📊 PREDICTION MARKET INTELLIGENCE\n")
        summary.append("=" * 60)
        
        # High probability events
        summary.append("\n🔴 HIGH PROBABILITY EVENTS (>50%):")
        for m in self.data['kalshi']:
            if m['yes_price'] > 0.5:
                summary.append(f"  • {m['title']}: {m['yes_price']*100:.0f}%")
        
        # Market movers
        summary.append("\n💰 HIGHEST VOLUME MARKETS:")
        sorted_markets = sorted(self.data['kalshi'], key=lambda x: x['volume'], reverse=True)
        for m in sorted_markets[:3]:
            summary.append(f"  • {m['title']}")
            summary.append(f"    Volume: ${m['volume']:,} | Probability: {m['yes_price']*100:.0f}%")
        
        # Platform summary
        summary.append(f"\n📈 PLATFORM SUMMARY:")
        summary.append(f"  • Kalshi: {len(self.data['kalshi'])} climate/insurance markets")
        summary.append(f"  • PredictIt: {len(self.data['predictit'])} disaster markets")
        summary.append(f"  • Polymarket: {len(self.data['polymarket'])} catastrophe markets")
        
        summary.append("\n" + "=" * 60)
        
        return "\n".join(summary)


def main():
    """Generate all visualizations"""
    print("=" * 60)
    print("PREDICTION MARKET ANALYSIS & VISUALIZATION")
    print("=" * 60)
    
    analyzer = PredictionMarketAnalyzer(SAMPLE_PREDICTION_MARKETS)
    
    # Generate charts
    charts = []
    charts.append(analyzer.create_probability_comparison())
    charts.append(analyzer.create_volume_analysis())
    charts.append(analyzer.create_event_risk_dashboard())
    
    # Generate newsletter summary
    print("\n" + analyzer.generate_newsletter_summary())
    
    print(f"\n{'='*60}")
    print(f"Generated {len(charts)} visualization charts!")
    print("=" * 60)
    
    return charts


if __name__ == "__main__":
    main()
