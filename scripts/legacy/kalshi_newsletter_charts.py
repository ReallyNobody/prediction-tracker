#!/usr/bin/env python3
"""
Kalshi Newsletter Chart Generator
Automatically creates publication-ready charts from weather market data
"""

import json
from datetime import datetime

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# Set style
plt.style.use("seaborn-v0_8-darkgrid")
sns.set_palette("husl")


class KalshiNewsletterCharts:
    def __init__(self, json_file="kalshi_weather_markets.json"):
        """Load and prepare data"""
        print("Loading Kalshi weather market data...")

        with open(json_file) as f:
            data = json.load(f)

        self.df = pd.DataFrame(data)

        # Extract city names
        self.df["city"] = self.df["series_ticker"].str.replace(r"^KX(HIGH|LOW)T?", "", regex=True)
        self.df["city"] = self.df["city"].str.replace("SNOWM|RAINM", "", regex=True)

        # Calculate implied probability
        self.df["implied_prob"] = self.df["yes_bid"] * 100

        # Extract market type
        self.df["market_type"] = self.df["series_ticker"].apply(self._get_market_type)

        print(f"Loaded {len(self.df)} markets across {self.df['city'].nunique()} cities")

        # Brand colors
        self.colors = {
            "primary": "#3b82f6",
            "secondary": "#ef4444",
            "accent": "#10b981",
            "dark": "#1e293b",
            "light": "#f1f5f9",
        }

    def _get_market_type(self, ticker):
        """Determine market type from ticker"""
        if "HIGH" in ticker:
            return "High Temp"
        elif "LOW" in ticker:
            return "Low Temp"
        elif "SNOW" in ticker:
            return "Snow"
        elif "RAIN" in ticker:
            return "Rain"
        else:
            return "Other"

    def create_volume_chart(self, output_path="kalshi_top_volume.png"):
        """Chart 1: Top 10 Markets by Trading Volume"""
        print("\nGenerating volume chart...")

        fig, ax = plt.subplots(figsize=(14, 8), facecolor="white")

        # Get top 10 by volume
        top10 = self.df.nlargest(10, "volume_24h").copy()

        # Shorten titles
        top10["short_title"] = top10["title"].str.replace(r"\*\*", "", regex=True)
        top10["short_title"] = top10["short_title"].apply(
            lambda x: x[:60] + "..." if len(x) > 60 else x
        )

        # Create bars
        colors = [
            self.colors["primary"]
            if "high temp" in t.lower()
            else self.colors["secondary"]
            if "low temp" in t.lower()
            else self.colors["accent"]
            for t in top10["title"]
        ]

        bars = ax.barh(range(len(top10)), top10["volume_24h"], color=colors, alpha=0.8)

        # Formatting
        ax.set_yticks(range(len(top10)))
        ax.set_yticklabels(top10["short_title"], fontsize=10)
        ax.set_xlabel("24-Hour Trading Volume ($)", fontsize=13, weight="bold")
        ax.set_title(
            "Top Weather Prediction Markets by Volume\nKalshi Trading Data",
            fontsize=17,
            weight="bold",
            pad=20,
        )

        # Add value labels
        for i, (idx, row) in enumerate(top10.iterrows()):
            ax.text(
                row["volume_24h"] + 100,
                i,
                f"${row['volume_24h']:,.0f}",
                va="center",
                fontsize=10,
                weight="bold",
            )

        # Add legend
        legend_elements = [
            mpatches.Patch(color=self.colors["primary"], label="High Temperature", alpha=0.8),
            mpatches.Patch(color=self.colors["secondary"], label="Low Temperature", alpha=0.8),
            mpatches.Patch(color=self.colors["accent"], label="Snow/Rain", alpha=0.8),
        ]
        ax.legend(handles=legend_elements, loc="lower right", fontsize=10, framealpha=0.95)

        ax.grid(axis="x", alpha=0.3)
        ax.set_xlim(0, top10["volume_24h"].max() * 1.15)

        # Branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: Kalshi.com | " + datetime.now().strftime("%B %d, %Y"),
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"✓ Saved: {output_path}")
        plt.close()

    def create_city_comparison(self, output_path="kalshi_by_city.png"):
        """Chart 2: Average Implied Probability by City"""
        print("Generating city comparison chart...")

        fig, ax = plt.subplots(figsize=(14, 7), facecolor="white")

        # Calculate average probability by city
        city_stats = (
            self.df.groupby("city")
            .agg({"implied_prob": "mean", "volume_24h": "sum", "ticker": "count"})
            .sort_values("volume_24h", ascending=False)
            .head(12)
        )

        # Create bars
        x_pos = range(len(city_stats))
        bars = ax.bar(
            x_pos,
            city_stats["implied_prob"],
            color=self.colors["secondary"],
            alpha=0.8,
            edgecolor="black",
            linewidth=1.5,
        )

        # Color by probability
        for i, (idx, row) in enumerate(city_stats.iterrows()):
            if row["implied_prob"] > 60:
                bars[i].set_color(self.colors["secondary"])
            elif row["implied_prob"] > 40:
                bars[i].set_color(self.colors["primary"])
            else:
                bars[i].set_color(self.colors["accent"])

        # Formatting
        ax.set_xticks(x_pos)
        ax.set_xticklabels(city_stats.index, fontsize=11, weight="bold", rotation=45, ha="right")
        ax.set_ylabel("Average Implied Probability (%)", fontsize=13, weight="bold")
        ax.set_title(
            'Weather Market Sentiment by City\nAverage "Yes" Probability Across All Active Markets',
            fontsize=17,
            weight="bold",
            pad=20,
        )

        # Add value labels and market count
        for i, (idx, row) in enumerate(city_stats.iterrows()):
            ax.text(
                i,
                row["implied_prob"] + 2,
                f"{row['implied_prob']:.0f}%",
                ha="center",
                fontsize=11,
                weight="bold",
            )
            ax.text(
                i,
                -5,
                f"{int(row['ticker'])} mkts",
                ha="center",
                fontsize=8,
                style="italic",
                color="gray",
            )

        # Add horizontal reference line
        avg_prob = self.df["implied_prob"].mean()
        ax.axhline(y=avg_prob, color="gray", linestyle="--", linewidth=1, alpha=0.5)
        ax.text(
            len(city_stats) - 0.5,
            avg_prob + 2,
            f"Overall Avg: {avg_prob:.0f}%",
            fontsize=9,
            style="italic",
            color="gray",
        )

        ax.grid(axis="y", alpha=0.3)
        ax.set_ylim(-8, city_stats["implied_prob"].max() * 1.15)

        # Branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: Kalshi.com | " + datetime.now().strftime("%B %d, %Y"),
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"✓ Saved: {output_path}")
        plt.close()

    def create_market_overview(self, output_path="kalshi_market_overview.png"):
        """Chart 3: Multi-panel Market Overview Dashboard"""
        print("Generating market overview dashboard...")

        fig = plt.figure(figsize=(16, 10), facecolor="white")
        gs = fig.add_gridspec(2, 2, hspace=0.3, wspace=0.25)

        # Panel 1: Market count by type
        ax1 = fig.add_subplot(gs[0, 0])
        type_counts = self.df["market_type"].value_counts()
        colors_type = [
            self.colors["primary"],
            self.colors["secondary"],
            self.colors["accent"],
            "#f59e0b",
        ]
        ax1.pie(
            type_counts,
            labels=type_counts.index,
            autopct="%1.0f%%",
            colors=colors_type,
            startangle=90,
            textprops={"fontsize": 11, "weight": "bold"},
        )
        ax1.set_title("Markets by Type", fontsize=14, weight="bold", pad=15)

        # Panel 2: Volume distribution
        ax2 = fig.add_subplot(gs[0, 1])
        volume_bins = [0, 1000, 5000, 10000, 50000]
        self.df["volume_bin"] = pd.cut(
            self.df["volume_24h"], bins=volume_bins, labels=["<$1K", "$1K-$5K", "$5K-$10K", "$10K+"]
        )
        vol_dist = self.df["volume_bin"].value_counts().sort_index()
        ax2.bar(range(len(vol_dist)), vol_dist, color=self.colors["primary"], alpha=0.8)
        ax2.set_xticks(range(len(vol_dist)))
        ax2.set_xticklabels(vol_dist.index, fontsize=10)
        ax2.set_ylabel("Number of Markets", fontsize=11, weight="bold")
        ax2.set_title("Volume Distribution", fontsize=14, weight="bold", pad=15)
        ax2.grid(axis="y", alpha=0.3)

        # Panel 3: Probability distribution
        ax3 = fig.add_subplot(gs[1, 0])
        ax3.hist(
            self.df["implied_prob"],
            bins=20,
            color=self.colors["accent"],
            alpha=0.7,
            edgecolor="black",
        )
        ax3.axvline(x=50, color="red", linestyle="--", linewidth=2, label="50% (Coin Flip)")
        ax3.set_xlabel("Implied Probability (%)", fontsize=11, weight="bold")
        ax3.set_ylabel("Number of Markets", fontsize=11, weight="bold")
        ax3.set_title("Probability Distribution", fontsize=14, weight="bold", pad=15)
        ax3.legend(fontsize=9)
        ax3.grid(axis="y", alpha=0.3)

        # Panel 4: Summary stats
        ax4 = fig.add_subplot(gs[1, 1])
        ax4.axis("off")

        total_markets = len(self.df)
        total_volume = self.df["volume_24h"].sum()
        total_oi = self.df["open_interest"].sum()
        avg_prob = self.df["implied_prob"].mean()
        cities = self.df["city"].nunique()

        stats_text = f"""
        📊 MARKET SUMMARY
        
        Total Active Markets: {total_markets:,}
        Total 24h Volume: ${total_volume:,.0f}
        Total Open Interest: {total_oi:,.0f}
        
        Average Probability: {avg_prob:.1f}%
        Cities Tracked: {cities}
        
        Top Market:
        {self.df.nlargest(1, "volume_24h")["title"].values[0][:60]}
        Volume: ${self.df["volume_24h"].max():,.0f}
        """

        ax4.text(
            0.1,
            0.5,
            stats_text,
            fontsize=12,
            family="monospace",
            verticalalignment="center",
            bbox=dict(
                boxstyle="round", facecolor="#f0f9ff", edgecolor=self.colors["primary"], linewidth=2
            ),
        )

        # Overall title
        fig.suptitle(
            "Kalshi Weather Markets - Market Overview\nReal-time Prediction Market Intelligence",
            fontsize=18,
            weight="bold",
            y=0.98,
        )

        # Branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: Kalshi.com | " + datetime.now().strftime("%B %d, %Y"),
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"✓ Saved: {output_path}")
        plt.close()

    def create_probability_heatmap(self, output_path="kalshi_temp_heatmap.png"):
        """Chart 4: Temperature Probability Heatmap"""
        print("Generating temperature heatmap...")

        # Filter to just temperature markets
        temp_df = self.df[self.df["market_type"].isin(["High Temp", "Low Temp"])].copy()

        # Extract temperature from title
        temp_df["temp"] = (
            temp_df["title"]
            .str.extract(r"(\d+)-(\d+)°")
            .apply(lambda x: (int(x[0]) + int(x[1])) / 2 if x[0] and x[1] else None, axis=1)
        )

        temp_df = temp_df.dropna(subset=["temp"])

        # Create pivot table
        pivot = temp_df.pivot_table(
            values="implied_prob", index="city", columns="temp", aggfunc="mean"
        )

        # Take top cities by volume
        top_cities = temp_df.groupby("city")["volume_24h"].sum().nlargest(8).index
        pivot = pivot.loc[top_cities]

        fig, ax = plt.subplots(figsize=(14, 8), facecolor="white")

        # Create heatmap
        sns.heatmap(
            pivot,
            annot=False,
            cmap="RdYlGn",
            cbar_kws={"label": "Implied Probability (%)"},
            linewidths=0.5,
            linecolor="white",
            ax=ax,
        )

        ax.set_xlabel("Temperature (°F)", fontsize=13, weight="bold")
        ax.set_ylabel("City", fontsize=13, weight="bold")
        ax.set_title(
            "Temperature Market Probability Heatmap\nImplied Probability by City and Temperature Range",
            fontsize=17,
            weight="bold",
            pad=20,
        )

        # Branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: Kalshi.com | " + datetime.now().strftime("%B %d, %Y"),
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"✓ Saved: {output_path}")
        plt.close()

    def generate_all(self):
        """Generate all newsletter charts"""
        print("\n" + "=" * 60)
        print("KALSHI NEWSLETTER CHART GENERATOR")
        print("=" * 60)

        self.create_volume_chart()
        self.create_city_comparison()
        self.create_market_overview()
        self.create_probability_heatmap()

        print("\n" + "=" * 60)
        print("✓ ALL CHARTS GENERATED!")
        print("=" * 60)
        print("\nNewsletter-ready images created:")
        print("  1. kalshi_top_volume.png - Top markets by trading volume")
        print("  2. kalshi_by_city.png - Average probability by city")
        print("  3. kalshi_market_overview.png - 4-panel dashboard")
        print("  4. kalshi_temp_heatmap.png - Temperature probability heatmap")
        print("\nAll charts are 300 DPI, publication-ready!")


def main():
    """Main execution"""
    try:
        generator = KalshiNewsletterCharts("kalshi_weather_markets.json")
        generator.generate_all()
    except FileNotFoundError:
        print("Error: kalshi_weather_markets.json not found!")
        print("Run the scraper first: python3 kalshi_authenticated_scraper.py")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
