import argparse
import asyncio
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

from src.core.logger import logger
from src.agents.outreach_drafter.outreach_graph import ContextAwareOutreachAgent
from src.agents.resume_tailor.cli import SAMPLE_DUBAI_JD, SAMPLE_PUNE_JD


async def main():
    parser = argparse.ArgumentParser(
        description="Job Hunt Orchestrator - Module 4: Context-Aware Cover Letter & DM Drafter"
    )
    parser.add_argument(
        "--jd-file",
        type=str,
        help="Path to Job Description file",
    )
    parser.add_argument(
        "--jd",
        type=str,
        help="Inline Job Description string",
    )
    parser.add_argument(
        "--sample",
        choices=["dubai", "pune"],
        default="dubai",
        help="Use benchmark sample JD (default: dubai)",
    )
    parser.add_argument(
        "--company",
        type=str,
        default="Derq AI",
        help="Target company name",
    )
    parser.add_argument(
        "--recipient-name",
        type=str,
        default="Tariq Al-Mansoor",
        help="Hiring leader or recruiter name",
    )
    parser.add_argument(
        "--recipient-email",
        type=str,
        default="tariq@derq.com",
        help="Recipient email address",
    )
    parser.add_argument(
        "--location",
        type=str,
        help="Target location ('Dubai, UAE' or 'Pune, India')",
    )

    args = parser.parse_args()
    console = Console()

    console.print("\n[bold cyan]═══ Module 4: Context-Aware Cover Letter & DM Drafter (LangGraph) ═══[/bold cyan]")

    jd_content = ""
    if args.jd_file:
        file_path = Path(args.jd_file)
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                jd_content = f.read()
        else:
            logger.error(f"JD file not found: {file_path}")
            return
    elif args.jd:
        jd_content = args.jd
    else:
        jd_content = SAMPLE_DUBAI_JD if args.sample == "dubai" else SAMPLE_PUNE_JD

    agent = ContextAwareOutreachAgent()
    result = await agent.generate_outreach(
        jd_text=jd_content,
        company_name=args.company,
        recipient_name=args.recipient_name,
        recipient_email=args.recipient_email,
        target_location=args.location or ("Dubai, UAE" if args.sample == "dubai" else "Pune, India"),
    )

    console.print(Panel(
        f"[bold green]Target Recipient:[/bold green] {result.get('recipient_name')} ({result.get('recipient_email')})\n"
        f"[bold green]Target Company:[/bold green] {result.get('company_name')} ({result.get('target_location')})\n"
        f"[bold green]Core Pain Point:[/bold green] {result.get('pain_point')}\n"
        f"[bold green]Mapped Anchor Project:[/bold green] {result.get('matched_anchor_project', {}).get('name')}\n"
        f"[bold green]Email Subject:[/bold green] {result.get('email_subject')}\n"
        f"[bold green]Draft Result:[/bold green] {result.get('gmail_draft_result', {}).get('status')}",
        title="📬 Outreach Generation Overview",
        border_style="cyan"
    ))

    console.print("\n[bold yellow]─── Output 1: Formal 3-Paragraph Cover Letter (ATS Upload) ───[/bold yellow]")
    console.print(result.get("cover_letter_markdown", ""))

    console.print("\n[bold yellow]─── Output 2: Punchy 150-Word Direct Message (LinkedIn/Cutshort) ───[/bold yellow]")
    console.print(Panel(result.get("direct_message_150words", ""), border_style="green"))


if __name__ == "__main__":
    asyncio.run(main())
