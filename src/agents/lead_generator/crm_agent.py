import argparse
import asyncio
from typing import List, Optional
from rich.console import Console
from rich.table import Table

from config.settings import settings
from src.core.logger import logger
from src.integrations.apollo_client import ApolloClient, LeadContact
from src.integrations.hunter_client import HunterClient
from src.integrations.google_sheets import GoogleSheetsCRM


class LeadGenerationCRMAgent:
    """Module 2: Lead Generation & CRM Agent

    Coordinates Apollo.io and Hunter.io leadership extraction for boutique AI
    agencies in Dubai and GCC hubs in Pune, syncing directly to Google Sheets CRM.
    """

    def __init__(self):
        self.apollo = ApolloClient()
        self.hunter = HunterClient()
        self.crm = GoogleSheetsCRM()
        self.console = Console()

    async def process_domain(
        self,
        domain: str,
        company_name: Optional[str] = None,
        location: Optional[str] = None,
    ) -> List[LeadContact]:
        """Scrape leadership contacts for a single domain and enrich missing emails."""
        logger.info(f"Targeting company domain: [{domain}]...")

        # 1. Apollo Search
        leads = await self.apollo.search_leadership(
            domain=domain,
            company_name=company_name,
            locations=[location] if location else settings.TARGET_HUBS,
            min_employees=settings.TARGET_AGENCY_MIN_EMPLOYEES,
            max_employees=settings.TARGET_AGENCY_MAX_EMPLOYEES,
        )

        # 2. Hunter.io Enrichment if Apollo has missing emails
        leads_without_email = [l for l in leads if not l.email]
        if (not leads or leads_without_email) and self.hunter.is_configured():
            logger.info(f"Querying Hunter.io for email enrichment at {domain}...")
            hunter_leads = await self.hunter.search_domain(domain=domain, limit=5)
            # Merge unique contacts
            existing_names = {l.full_name.lower() for l in leads}
            for hl in hunter_leads:
                if hl.full_name.lower() not in existing_names:
                    leads.append(hl)

        return leads

    async def run_pipeline(
        self,
        target_domains: Optional[List[str]] = None,
        company_name: Optional[str] = None,
        location: Optional[str] = None,
    ) -> List[LeadContact]:
        """Execute the end-to-end lead generation and CRM sync pipeline."""
        self.console.print("\n[bold cyan]═══ Module 2: Lead Generation & CRM Agent ═══[/bold cyan]")

        # Determine domains to process
        domains = target_domains
        if not domains:
            logger.info("Reading target domains from Google Sheets / configured targets...")
            domains = self.crm.read_target_domains()

        all_discovered_leads: List[LeadContact] = []

        for domain in domains:
            try:
                leads = await self.process_domain(
                    domain=domain,
                    company_name=company_name,
                    location=location,
                )
                all_discovered_leads.extend(leads)
            except Exception as e:
                logger.error(f"Error processing domain {domain}: {e}")

        # Sync to Google Sheets CRM & Local CSV
        synced_count = self.crm.append_leads(all_discovered_leads)
        logger.info(f"Successfully processed {len(domains)} domains. Total leads synced: {synced_count}")

        # Render Rich Table Summary
        self._display_summary_table(all_discovered_leads)

        return all_discovered_leads

    def _display_summary_table(self, leads: List[LeadContact]) -> None:
        """Render a clean terminal table of discovered leadership leads."""
        table = Table(title="🎯 Discovered Engineering Leadership Leads (Dubai & Pune)")
        table.add_column("Company", style="cyan", no_wrap=True)
        table.add_column("Contact Name", style="bold white")
        table.add_column("Title", style="green")
        table.add_column("Email", style="yellow")
        table.add_column("Location", style="magenta")
        table.add_column("Source", style="dim")

        for lead in leads:
            table.add_row(
                lead.company_name,
                lead.full_name,
                lead.title,
                lead.email or "N/A",
                lead.location,
                lead.source,
            )

        self.console.print(table)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
async def main():
    parser = argparse.ArgumentParser(
        description="Job Hunt Orchestrator - Module 2: Lead Generation & CRM Agent"
    )
    parser.add_argument(
        "--domain",
        type=str,
        help="Target company domain to scrape (e.g., derq.com, aiq.ae)",
    )
    parser.add_argument(
        "--company",
        type=str,
        help="Target company name (e.g., Derq, Quantiphi)",
    )
    parser.add_argument(
        "--location",
        type=str,
        help="Target location filter (e.g., 'Dubai, UAE' or 'Pune, India')",
    )
    parser.add_argument(
        "--sync-sheet",
        action="store_true",
        help="Read target domains from Google Sheets CRM and write back results.",
    )

    args = parser.parse_args()

    agent = LeadGenerationCRMAgent()

    domains = [args.domain] if args.domain else None
    await agent.run_pipeline(
        target_domains=domains,
        company_name=args.company,
        location=args.location,
    )


if __name__ == "__main__":
    asyncio.run(main())
