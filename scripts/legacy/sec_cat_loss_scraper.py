#!/usr/bin/env python3
"""
SEC Catastrophe Loss Data Scraper
Prototype for extracting cat loss data from SEC filings
"""

import json
import re
import time

import requests
from bs4 import BeautifulSoup


class SECCatLossScraper:
    def __init__(self):
        self.base_url = "https://www.sec.gov"
        self.headers = {"User-Agent": "Risk Market News research@riskmarketnews.com"}

    def search_company_filings(self, cik, filing_type="10-Q", count=10):
        """
        Search for company filings by CIK
        cik: Company CIK number (e.g., '0001067983' for RenaissanceRe)
        filing_type: Type of filing (10-K, 10-Q, 8-K)
        """
        # Format CIK with leading zeros
        cik_padded = str(cik).zfill(10)

        # SEC EDGAR API endpoint
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

        print(f"Fetching filings for CIK {cik_padded}...")

        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            # Extract recent filings
            filings = data.get("filings", {}).get("recent", {})

            results = []
            for i in range(len(filings.get("form", []))):
                if filings["form"][i] == filing_type:
                    results.append(
                        {
                            "form": filings["form"][i],
                            "filing_date": filings["filingDate"][i],
                            "accession_number": filings["accessionNumber"][i],
                            "primary_document": filings["primaryDocument"][i],
                            "company": data["name"],
                        }
                    )

                    if len(results) >= count:
                        break

            return results

        except Exception as e:
            print(f"Error fetching filings: {e}")
            return []

    def get_filing_text(self, accession_number, primary_document, cik):
        """Fetch the full text of a filing"""
        # Remove dashes from accession number for URL
        acc_no_dashes = accession_number.replace("-", "")
        cik_padded = str(cik).zfill(10)

        url = f"{self.base_url}/Archives/edgar/data/{cik}/{acc_no_dashes}/{primary_document}"

        print(f"Fetching filing: {url}")

        try:
            time.sleep(0.2)  # Be nice to SEC servers
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Error fetching filing text: {e}")
            return None

    def extract_cat_loss_data(self, html_text, filing_info):
        """Extract catastrophe loss mentions from filing text"""
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text()

        # Keywords to search for
        cat_keywords = [
            "catastrophe loss",
            "cat loss",
            "natural catastrophe",
            "hurricane",
            "wildfire",
            "earthquake",
            "flood",
            "tornado",
            "winter storm",
            "severe convective storm",
            "hailstorm",
        ]

        findings = []

        # Search for catastrophe loss mentions with context
        for keyword in cat_keywords:
            pattern = re.compile(f".{{0,200}}{re.escape(keyword)}.{{0,200}}", re.IGNORECASE)
            matches = pattern.findall(text)

            for match in matches[:3]:  # Limit to first 3 matches per keyword
                # Try to extract dollar amounts near the keyword
                dollar_pattern = r"\$\s*[\d,]+(?:\.\d+)?\s*(?:million|billion)?"
                amounts = re.findall(dollar_pattern, match, re.IGNORECASE)

                findings.append(
                    {
                        "keyword": keyword,
                        "context": match.strip(),
                        "amounts_found": amounts,
                        "filing_date": filing_info["filing_date"],
                        "company": filing_info["company"],
                    }
                )

        return findings

    def scrape_company(self, cik, company_name=None, filing_type="10-Q", count=3):
        """Main method to scrape a company's cat loss data"""
        print(f"\n{'=' * 60}")
        print(f"Scraping {company_name or 'company'} (CIK: {cik})")
        print(f"{'=' * 60}\n")

        # Get recent filings
        filings = self.search_company_filings(cik, filing_type, count)

        if not filings:
            print("No filings found")
            return []

        print(f"Found {len(filings)} {filing_type} filings\n")

        all_cat_data = []

        for filing in filings:
            print(f"\nProcessing {filing['form']} from {filing['filing_date']}...")

            # Get filing text
            html_text = self.get_filing_text(
                filing["accession_number"], filing["primary_document"], cik
            )

            if html_text:
                # Extract cat loss data
                cat_data = self.extract_cat_loss_data(html_text, filing)
                all_cat_data.extend(cat_data)
                print(f"Found {len(cat_data)} catastrophe loss mentions")

        return all_cat_data


def main():
    scraper = SECCatLossScraper()

    # Example: RenaissanceRe Holdings Ltd.
    # CIK: 1067983
    companies = [
        {"cik": "1067983", "name": "RenaissanceRe Holdings"},
        # Add more companies as needed:
        # {'cik': '0001163165', 'name': 'Everest Re Group'},
        # {'cik': '0000875159', 'name': 'Arch Capital Group'},
    ]

    all_results = []

    for company in companies:
        cat_data = scraper.scrape_company(
            cik=company["cik"],
            company_name=company["name"],
            filing_type="10-Q",
            count=2,  # Get last 2 quarters
        )
        all_results.extend(cat_data)

    # Save results
    output_file = "/home/claude/cat_loss_data.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"Scraping complete! Found {len(all_results)} total mentions")
    print(f"Results saved to: {output_file}")
    print(f"{'=' * 60}\n")

    # Print sample results
    if all_results:
        print("\nSample findings:")
        for i, result in enumerate(all_results[:5]):
            print(f"\n{i + 1}. {result['company']} - {result['filing_date']}")
            print(f"   Keyword: {result['keyword']}")
            print(f"   Amounts: {result['amounts_found']}")
            print(f"   Context: {result['context'][:150]}...")


if __name__ == "__main__":
    main()
