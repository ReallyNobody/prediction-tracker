#!/usr/bin/env python3
"""
Catastrophe Loss Database & Visualization System
Demo with sample data structure
"""

import json
import sqlite3

import pandas as pd

# Sample data structure representing what we'd extract from SEC filings
SAMPLE_CAT_LOSS_DATA = [
    {
        "company": "RenaissanceRe Holdings",
        "ticker": "RNR",
        "filing_type": "10-Q",
        "filing_date": "2024-10-30",
        "quarter": "Q3 2024",
        "event_name": "Hurricane Milton",
        "event_date": "2024-10-09",
        "gross_loss_usd": 375000000,
        "net_loss_usd": 125000000,
        "loss_type": "Property",
        "geography": "United States - Florida",
        "context": "The Company estimates its net losses from Hurricane Milton to be approximately $125 million",
        "source_accession": "0001067983-24-000045",
    },
    {
        "company": "RenaissanceRe Holdings",
        "ticker": "RNR",
        "filing_type": "10-Q",
        "filing_date": "2024-10-30",
        "quarter": "Q3 2024",
        "event_name": "Hurricane Helene",
        "event_date": "2024-09-26",
        "gross_loss_usd": 425000000,
        "net_loss_usd": 150000000,
        "loss_type": "Property",
        "geography": "United States - Southeast",
        "context": "Hurricane Helene resulted in estimated net losses of $150 million for the quarter",
        "source_accession": "0001067983-24-000045",
    },
    {
        "company": "Everest Re Group",
        "ticker": "EG",
        "filing_type": "10-Q",
        "filing_date": "2024-11-05",
        "quarter": "Q3 2024",
        "event_name": "Hurricane Milton",
        "event_date": "2024-10-09",
        "gross_loss_usd": 450000000,
        "net_loss_usd": 180000000,
        "loss_type": "Property",
        "geography": "United States - Florida",
        "context": "Pre-tax catastrophe losses for Q3 included approximately $180 million from Hurricane Milton",
        "source_accession": "0001163165-24-000052",
    },
    {
        "company": "Arch Capital Group",
        "ticker": "ACGL",
        "filing_type": "10-Q",
        "filing_date": "2024-10-29",
        "quarter": "Q3 2024",
        "event_name": "Los Angeles Wildfires",
        "event_date": "2025-01-07",
        "gross_loss_usd": 285000000,
        "net_loss_usd": 95000000,
        "loss_type": "Property",
        "geography": "United States - California",
        "context": "Wildfire losses in Southern California resulted in net losses of approximately $95 million",
        "source_accession": "0000875159-25-000012",
    },
    {
        "company": "RenaissanceRe Holdings",
        "ticker": "RNR",
        "filing_type": "10-K",
        "filing_date": "2024-02-28",
        "quarter": "FY 2023",
        "event_name": "Maui Wildfires",
        "event_date": "2023-08-08",
        "gross_loss_usd": 350000000,
        "net_loss_usd": 175000000,
        "loss_type": "Property",
        "geography": "United States - Hawaii",
        "context": "The Maui wildfire event resulted in significant losses with net impact of $175 million",
        "source_accession": "0001067983-24-000008",
    },
    {
        "company": "Everest Re Group",
        "ticker": "EG",
        "filing_type": "10-Q",
        "filing_date": "2024-08-06",
        "quarter": "Q2 2024",
        "event_name": "Severe Convective Storms",
        "event_date": "2024-05-15",
        "gross_loss_usd": 220000000,
        "net_loss_usd": 85000000,
        "loss_type": "Property",
        "geography": "United States - Midwest",
        "context": "Second quarter severe convective storm activity resulted in losses of $85 million",
        "source_accession": "0001163165-24-000038",
    },
]


class CatLossDatabase:
    def __init__(self, db_path="/home/claude/cat_loss.db"):
        self.db_path = db_path
        self.setup_database()

    def setup_database(self):
        """Create database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Main catastrophe loss table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cat_losses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
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
                source_accession TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Index for common queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_company ON cat_losses(company)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_event ON cat_losses(event_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_filing_date ON cat_losses(filing_date)
        """)

        conn.commit()
        conn.close()
        print(f"Database initialized at: {self.db_path}")

    def insert_loss_data(self, loss_data):
        """Insert catastrophe loss record"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO cat_losses 
            (company, ticker, filing_type, filing_date, quarter, event_name, 
             event_date, gross_loss_usd, net_loss_usd, loss_type, geography, 
             context, source_accession)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                loss_data["company"],
                loss_data.get("ticker"),
                loss_data.get("filing_type"),
                loss_data.get("filing_date"),
                loss_data.get("quarter"),
                loss_data.get("event_name"),
                loss_data.get("event_date"),
                loss_data.get("gross_loss_usd"),
                loss_data.get("net_loss_usd"),
                loss_data.get("loss_type"),
                loss_data.get("geography"),
                loss_data.get("context"),
                loss_data.get("source_accession"),
            ),
        )

        conn.commit()
        conn.close()

    def get_all_losses(self):
        """Get all catastrophe losses"""
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM cat_losses ORDER BY filing_date DESC", conn)
        conn.close()
        return df

    def get_losses_by_event(self, event_name):
        """Get all losses for a specific event"""
        conn = sqlite3.connect(self.db_path)
        query = "SELECT * FROM cat_losses WHERE event_name LIKE ? ORDER BY net_loss_usd DESC"
        df = pd.read_sql_query(query, conn, params=(f"%{event_name}%",))
        conn.close()
        return df

    def get_losses_by_company(self, company):
        """Get all losses for a specific company"""
        conn = sqlite3.connect(self.db_path)
        query = "SELECT * FROM cat_losses WHERE company LIKE ? ORDER BY filing_date DESC"
        df = pd.read_sql_query(query, conn, params=(f"%{company}%",))
        conn.close()
        return df

    def get_summary_stats(self):
        """Get summary statistics"""
        conn = sqlite3.connect(self.db_path)

        queries = {
            "total_events": "SELECT COUNT(DISTINCT event_name) FROM cat_losses",
            "total_companies": "SELECT COUNT(DISTINCT company) FROM cat_losses",
            "total_net_losses": "SELECT SUM(net_loss_usd) FROM cat_losses",
            "total_gross_losses": "SELECT SUM(gross_loss_usd) FROM cat_losses",
            "avg_net_loss": "SELECT AVG(net_loss_usd) FROM cat_losses",
        }

        stats = {}
        for key, query in queries.items():
            cursor = conn.execute(query)
            stats[key] = cursor.fetchone()[0]

        conn.close()
        return stats

    def export_to_json(self, output_path="/home/claude/cat_loss_export.json"):
        """Export database to JSON for newsletter/web use"""
        df = self.get_all_losses()

        # Convert to JSON-friendly format
        data = df.to_dict("records")

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        print(f"Exported {len(data)} records to {output_path}")
        return output_path


def demo():
    """Demo the database system"""
    print("=" * 60)
    print("CATASTROPHE LOSS DATABASE DEMO")
    print("=" * 60)

    # Initialize database
    db = CatLossDatabase()

    # Insert sample data
    print(f"\nInserting {len(SAMPLE_CAT_LOSS_DATA)} sample records...")
    for record in SAMPLE_CAT_LOSS_DATA:
        db.insert_loss_data(record)

    # Get summary stats
    print("\n" + "=" * 60)
    print("SUMMARY STATISTICS")
    print("=" * 60)
    stats = db.get_summary_stats()
    print(f"Total Events Tracked: {stats['total_events']}")
    print(f"Total Companies: {stats['total_companies']}")
    print(f"Total Net Losses: ${stats['total_net_losses']:,.0f}")
    print(f"Total Gross Losses: ${stats['total_gross_losses']:,.0f}")
    print(f"Average Net Loss: ${stats['avg_net_loss']:,.0f}")

    # Query by event
    print("\n" + "=" * 60)
    print("HURRICANE MILTON LOSSES BY COMPANY")
    print("=" * 60)
    milton_losses = db.get_losses_by_event("Milton")
    print(
        milton_losses[
            ["company", "ticker", "net_loss_usd", "gross_loss_usd", "filing_date"]
        ].to_string(index=False)
    )

    # Query by company
    print("\n" + "=" * 60)
    print("RENAISSANCERE CATASTROPHE LOSSES")
    print("=" * 60)
    rnr_losses = db.get_losses_by_company("RenaissanceRe")
    print(
        rnr_losses[["event_name", "event_date", "net_loss_usd", "quarter"]].to_string(index=False)
    )

    # Export for web/newsletter
    export_path = db.export_to_json()

    print("\n" + "=" * 60)
    print("DATABASE READY!")
    print("=" * 60)
    print(f"SQLite Database: {db.db_path}")
    print(f"JSON Export: {export_path}")
    print("\nNext steps:")
    print("1. Connect SEC scraper to populate real data")
    print("2. Build web dashboard for visualization")
    print("3. Create newsletter-ready charts")


if __name__ == "__main__":
    demo()
