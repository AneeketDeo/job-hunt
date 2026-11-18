import argparse
import asyncio
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

from src.core.logger import logger
from src.agents.resume_tailor.tailor_graph import DynamicResumeTailorAgent

SAMPLE_DUBAI_JD = """
Job Title: Senior GenAI Solutions Architect
Company: Falcon AI Innovations (Dubai, UAE)
Location: Dubai Internet City, Dubai (On-site / Hybrid)
Salary: AED 18,000 - 22,000 per month + Relocation Package

Role Overview:
We are seeking an experienced GenAI Engineer to architect autonomous multi-agent systems and high-precision RAG pipelines for legal and government enterprise clients across the GCC.

Requirements:
- 3+ years experience with Python, FastAPI, and local LLMs (Llama 3, Mistral via Ollama).
- Deep expertise in multi-agent orchestration using AutoGen GroupChatManager, LangGraph, or CrewAI.
- Proven experience with RAG, vector databases (ChromaDB), and hybrid search using BGE Large, BM25, and Query Fusion.
- Experience with document automation, contract analysis, and reducing manual review bottlenecks.
- Strong willingness to relocate to Dubai immediately.
"""

SAMPLE_PUNE_JD = """
Job Title: Principal AI / Agentic Systems Engineer
Company: Global GCC Hub (Pune)
Location: Magarpatta / Hinjewadi, Pune, India
Compensation: 18 - 22 LPA

Requirements:
- 3+ years in production AI engineering.
- Deep hands-on experience in building autonomous IT resolution systems and conversational analytics agents.
- Strong knowledge of AutoGen GroupChatManager, LangChain, FastAPI, Redis, and ChromaDB.
- Demonstrated success reducing mean-time-to-resolution (MTTR) on incident workflows and automating ad-hoc reporting.
"""


async def main():
    parser = argparse.ArgumentParser(
        description="Job Hunt Orchestrator - Module 3: Dynamic Resume Tailoring Agent (LangGraph + WeasyPrint)"
    )
    parser.add_argument(
        "--jd-file",
        type=str,
        help="Path to file containing target Job Description text",
    )
    parser.add_argument(
        "--jd",
        type=str,
        help="Direct inline string of the Job Description",
    )
    parser.add_argument(
        "--sample",
        choices=["dubai", "pune"],
        default="dubai",
        help="Use built-in benchmark JD sample (default: dubai)",
    )
    parser.add_argument(
        "--company",
        type=str,
        help="Target company name",
    )
    parser.add_argument(
        "--location",
        type=str,
        help="Target location (e.g. 'Dubai, UAE' or 'Pune, India')",
    )

    args = parser.parse_args()
    console = Console()

    console.print("\n[bold cyan]═══ Module 3: Dynamic Resume Tailoring Agent (LangGraph) ═══[/bold cyan]")

    jd_content = ""
    if args.jd_file:
        file_path = Path(args.jd_file)
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                jd_content = f.read()
            logger.info(f"Loaded JD from file: {file_path}")
        else:
            logger.error(f"JD file not found: {file_path}")
            return
    elif args.jd:
        jd_content = args.jd
    else:
        logger.info(f"Using default benchmark [{args.sample.upper()}] Job Description.")
        jd_content = SAMPLE_DUBAI_JD if args.sample == "dubai" else SAMPLE_PUNE_JD

    agent = DynamicResumeTailorAgent()
    result = await agent.tailor_resume(
        jd_text=jd_content,
        company_name=args.company,
        target_location=args.location,
    )

    # Display Rich Results
    console.print(Panel(
        f"[bold green]Target Company:[/bold green] {result.get('company_name')}\n"
        f"[bold green]Target Location:[/bold green] {result.get('target_location')}\n"
        f"[bold green]Primary Focus Area:[/bold green] {result.get('target_focus_area')}\n"
        f"[bold green]Prioritized Project #1:[/bold green] {result.get('ranked_projects', [{}])[0].get('name')}\n"
        f"[bold green]Status:[/bold green] {result.get('status')}\n"
        f"[bold green]Output File:[/bold green] {result.get('pdf_path')}",
        title="✨ Resume Tailoring Summary",
        border_style="cyan"
    ))

    console.print("\n[bold yellow]Tailored Executive Summary:[/bold yellow]")
    console.print(result.get("tailored_summary", ""))


if __name__ == "__main__":
    asyncio.run(main())
