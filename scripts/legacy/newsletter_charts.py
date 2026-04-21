#!/usr/bin/env python3
"""
Newsletter Chart Generator
Creates publication-ready visualizations for Risk Market News
"""

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# Set style for professional charts
plt.style.use("seaborn-v0_8-darkgrid")
sns.set_palette("husl")


class NewsletterChartGenerator:
    def __init__(self):
        self.brand_colors = {
            "primary": "#3b82f6",
            "secondary": "#ef4444",
            "accent": "#10b981",
            "dark": "#1e293b",
            "light": "#f1f5f9",
        }

    def create_event_comparison_chart(self, data, output_path="/home/claude/event_comparison.png"):
        """Create bar chart comparing losses by event"""
        # Aggregate by event
        event_totals = (
            data.groupby("event_name")
            .agg({"net_loss_usd": "sum", "gross_loss_usd": "sum"})
            .sort_values("net_loss_usd", ascending=True)
        )

        fig, ax = plt.subplots(figsize=(12, 6), facecolor="white")

        y_pos = range(len(event_totals))

        # Create bars
        bars1 = ax.barh(
            y_pos,
            event_totals["gross_loss_usd"] / 1e6,
            color=self.brand_colors["primary"],
            alpha=0.6,
            label="Gross Loss",
        )
        bars2 = ax.barh(
            y_pos,
            event_totals["net_loss_usd"] / 1e6,
            color=self.brand_colors["secondary"],
            alpha=0.9,
            label="Net Loss",
        )

        # Formatting
        ax.set_yticks(y_pos)
        ax.set_yticklabels(event_totals.index, fontsize=11, weight="bold")
        ax.set_xlabel("Loss (USD Millions)", fontsize=12, weight="bold")
        ax.set_title(
            "Catastrophe Losses by Event\nGross vs Net Retained", fontsize=16, weight="bold", pad=20
        )

        # Add value labels
        for i, (gross, net) in enumerate(
            zip(event_totals["gross_loss_usd"], event_totals["net_loss_usd"])
        ):
            ax.text(
                gross / 1e6 + 10,
                i,
                f"${gross / 1e6:.0f}M",
                va="center",
                fontsize=10,
                color=self.brand_colors["dark"],
            )
            ax.text(
                net / 1e6 + 10,
                i - 0.15,
                f"${net / 1e6:.0f}M",
                va="center",
                fontsize=10,
                weight="bold",
                color=self.brand_colors["secondary"],
            )

        ax.legend(loc="lower right", fontsize=11, framealpha=0.9)
        ax.grid(axis="x", alpha=0.3)

        # Add branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: SEC Filings",
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"Event comparison chart saved: {output_path}")
        return output_path

    def create_company_market_share_chart(
        self, data, output_path="/home/claude/company_market_share.png"
    ):
        """Create pie chart of losses by company"""
        company_totals = data.groupby("ticker")["net_loss_usd"].sum().sort_values(ascending=False)

        fig, ax = plt.subplots(figsize=(10, 8), facecolor="white")

        colors = [
            self.brand_colors["primary"],
            self.brand_colors["secondary"],
            self.brand_colors["accent"],
            "#f59e0b",
            "#8b5cf6",
        ]

        wedges, texts, autotexts = ax.pie(
            company_totals,
            labels=company_totals.index,
            autopct="%1.1f%%",
            colors=colors,
            startangle=90,
            textprops={"fontsize": 12, "weight": "bold"},
        )

        # Enhance text
        for autotext in autotexts:
            autotext.set_color("white")
            autotext.set_fontsize(14)
            autotext.set_weight("bold")

        ax.set_title(
            "Net Catastrophe Losses by Company\nMarket Share of Reported Losses",
            fontsize=16,
            weight="bold",
            pad=20,
        )

        # Add legend with dollar amounts
        legend_labels = [f"{ticker}: ${amt / 1e6:.0f}M" for ticker, amt in company_totals.items()]
        ax.legend(
            legend_labels, loc="upper left", bbox_to_anchor=(1, 1), fontsize=11, framealpha=0.9
        )

        # Add branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: SEC Filings",
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"Company market share chart saved: {output_path}")
        return output_path

    def create_timeline_chart(self, data, output_path="/home/claude/loss_timeline.png"):
        """Create timeline of catastrophe events"""
        # Sort by event date
        timeline_data = data.sort_values("event_date")
        timeline_data["event_date"] = pd.to_datetime(timeline_data["event_date"])

        fig, ax = plt.subplots(figsize=(14, 7), facecolor="white")

        # Create scatter plot sized by loss amount
        colors = [
            self.brand_colors["primary"],
            self.brand_colors["secondary"],
            self.brand_colors["accent"],
        ] * (len(timeline_data) // 3 + 1)
        scatter = ax.scatter(
            timeline_data["event_date"],
            timeline_data["net_loss_usd"] / 1e6,
            s=timeline_data["gross_loss_usd"] / 1e6 / 2,  # Size by gross loss
            c=colors[: len(timeline_data)],
            alpha=0.6,
            edgecolors="black",
            linewidth=2,
        )

        # Add labels for each point
        for idx, row in timeline_data.iterrows():
            ax.annotate(
                row["event_name"],
                xy=(row["event_date"], row["net_loss_usd"] / 1e6),
                xytext=(10, 10),
                textcoords="offset points",
                fontsize=9,
                weight="bold",
                bbox=dict(
                    boxstyle="round,pad=0.5",
                    facecolor="white",
                    edgecolor=self.brand_colors["dark"],
                    alpha=0.8,
                ),
            )

        ax.set_xlabel("Event Date", fontsize=12, weight="bold")
        ax.set_ylabel("Net Loss (USD Millions)", fontsize=12, weight="bold")
        ax.set_title(
            "Catastrophe Loss Timeline\nBubble size represents gross loss",
            fontsize=16,
            weight="bold",
            pad=20,
        )

        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)

        # Format x-axis dates
        fig.autofmt_xdate()

        # Add branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: SEC Filings",
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"Timeline chart saved: {output_path}")
        return output_path

    def create_retention_ratio_chart(self, data, output_path="/home/claude/retention_ratios.png"):
        """Create chart showing retention ratios (net/gross) by company"""
        company_data = data.groupby("company").agg({"net_loss_usd": "sum", "gross_loss_usd": "sum"})
        company_data["retention_ratio"] = (
            company_data["net_loss_usd"] / company_data["gross_loss_usd"] * 100
        )
        company_data = company_data.sort_values("retention_ratio", ascending=True)

        fig, ax = plt.subplots(figsize=(12, 6), facecolor="white")

        y_pos = range(len(company_data))
        bars = ax.barh(
            y_pos,
            company_data["retention_ratio"],
            color=[
                self.brand_colors["primary"]
                if x < 40
                else self.brand_colors["accent"]
                if x < 50
                else self.brand_colors["secondary"]
                for x in company_data["retention_ratio"]
            ],
        )

        ax.set_yticks(y_pos)
        ax.set_yticklabels(company_data.index, fontsize=11, weight="bold")
        ax.set_xlabel("Net Retention Ratio (%)", fontsize=12, weight="bold")
        ax.set_title(
            "Catastrophe Loss Retention Ratios by Company\nNet Loss as % of Gross Loss",
            fontsize=16,
            weight="bold",
            pad=20,
        )

        # Add value labels
        for i, (ratio, net, gross) in enumerate(
            zip(
                company_data["retention_ratio"],
                company_data["net_loss_usd"],
                company_data["gross_loss_usd"],
            )
        ):
            ax.text(ratio + 1, i, f"{ratio:.1f}%", va="center", fontsize=11, weight="bold")
            ax.text(
                ratio - 5,
                i,
                f"${net / 1e6:.0f}M / ${gross / 1e6:.0f}M",
                va="center",
                ha="right",
                fontsize=9,
                style="italic",
            )

        ax.axvline(x=33.3, color="gray", linestyle="--", alpha=0.5, label="Industry Avg ~33%")
        ax.legend(fontsize=10)
        ax.grid(axis="x", alpha=0.3)
        ax.set_xlim(0, max(company_data["retention_ratio"]) + 10)

        # Add branding
        fig.text(
            0.99,
            0.01,
            "Risk Market News | Source: SEC Filings",
            ha="right",
            fontsize=9,
            style="italic",
            color="gray",
        )

        plt.tight_layout()
        plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"Retention ratio chart saved: {output_path}")
        return output_path


def generate_all_newsletter_charts():
    """Generate all newsletter charts"""
    # Load sample data
    sample_data = pd.DataFrame(
        [
            {
                "company": "RenaissanceRe Holdings",
                "ticker": "RNR",
                "event_name": "Hurricane Milton",
                "event_date": "2024-10-09",
                "net_loss_usd": 125000000,
                "gross_loss_usd": 375000000,
            },
            {
                "company": "RenaissanceRe Holdings",
                "ticker": "RNR",
                "event_name": "Hurricane Helene",
                "event_date": "2024-09-26",
                "net_loss_usd": 150000000,
                "gross_loss_usd": 425000000,
            },
            {
                "company": "Everest Re Group",
                "ticker": "EG",
                "event_name": "Hurricane Milton",
                "event_date": "2024-10-09",
                "net_loss_usd": 180000000,
                "gross_loss_usd": 450000000,
            },
            {
                "company": "Arch Capital Group",
                "ticker": "ACGL",
                "event_name": "Los Angeles Wildfires",
                "event_date": "2025-01-07",
                "net_loss_usd": 95000000,
                "gross_loss_usd": 285000000,
            },
            {
                "company": "RenaissanceRe Holdings",
                "ticker": "RNR",
                "event_name": "Maui Wildfires",
                "event_date": "2023-08-08",
                "net_loss_usd": 175000000,
                "gross_loss_usd": 350000000,
            },
            {
                "company": "Everest Re Group",
                "ticker": "EG",
                "event_name": "Severe Convective Storms",
                "event_date": "2024-05-15",
                "net_loss_usd": 85000000,
                "gross_loss_usd": 220000000,
            },
        ]
    )

    generator = NewsletterChartGenerator()

    print("=" * 60)
    print("GENERATING NEWSLETTER CHARTS")
    print("=" * 60)

    charts = []
    charts.append(generator.create_event_comparison_chart(sample_data))
    charts.append(generator.create_company_market_share_chart(sample_data))
    charts.append(generator.create_timeline_chart(sample_data))
    charts.append(generator.create_retention_ratio_chart(sample_data))

    print("\n" + "=" * 60)
    print(f"Generated {len(charts)} newsletter-ready charts!")
    print("=" * 60)

    return charts


if __name__ == "__main__":
    generate_all_newsletter_charts()
