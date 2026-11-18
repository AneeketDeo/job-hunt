import argparse
import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from src.core.logger import logger
from src.agents.simulator.negotiation_engine import NegotiationSimulatorEngine


BENCHMARK_SCRIPTS = {
    "pune_ideal": (
        "I appreciate your transparency regarding standard GCC bands. However, my expectation is firmly anchored at 20-21 LPA fixed. "
        "Out of mutual respect and signed confidentiality agreements, I cannot disclose the other firm's name. "
        "What I can share is that their offer reflects the production value of building AgentAssist—where we automated 70% of IT incidents "
        "with AutoGen GroupChatManager—and our Contract Redlining engine which slashed review latency by 60% with hybrid BGE Large and BM25 RAG. "
        "If your team can meet 20.5 LPA fixed, I am prepared to close all discussions and sign with you today."
    ),
    "pune_weak": (
        "Well, the offer is from another tech company. My current offer is 14 LPA, but I was hoping for something around 18-20 LPA. "
        "Could you please see if management can do 17 or 18 LPA? I really want to join your team."
    ),
    "dubai_ideal": (
        "Thank you, Kareem. While I understand AED 14,000 is common for junior hires, my target is strictly AED 19,000 – 21,000 per month. "
        "My 14 LPA baseline in India is irrelevant to the commercial ROI I deliver to your Dubai enterprise clients. "
        "From day one, I can deploy production agentic state machines that cut manual legal review by 60% and eliminate 70% of tier-1 support tickets. "
        "I am ready to relocate immediately without delay. At AED 20,000, you are getting an architect who ships immediately without training overhead."
    ),
}


async def run_benchmark(scenario: str):
    console = Console()
    console.print(f"\n[bold cyan]═══ Running Automated Benchmark Evaluation ({scenario.upper()}) ═══[/bold cyan]")

    engine = NegotiationSimulatorEngine(scenario=scenario)
    opener = engine.get_initial_opener()

    console.print(Panel(opener, title="🏢 HR Recruiter Opener", border_style="red"))

    script_key = f"{'pune' if 'pune' in scenario else 'dubai'}_ideal"
    candidate_reply = BENCHMARK_SCRIPTS[script_key]

    console.print(Panel(candidate_reply, title="🧑‍💻 Candidate Strategic Response", border_style="green"))

    # Evaluate
    evaluation = await engine.evaluate_candidate_response(
        candidate_response=candidate_reply,
        last_recruiter_challenge=opener,
    )

    # Render Evaluation Table
    table = Table(title="📊 Strategic Compensation Coach Scorecard")
    table.add_column("Criterion", style="cyan")
    table.add_column("Score / Status", style="bold white")
    table.add_column("Coach Notes", style="yellow")

    table.add_row(
        "Target Anchoring",
        f"{evaluation.get('anchoring_score')}/100",
        "Firmly held target range without premature concessions",
    )
    table.add_row(
        "Confidentiality Guardrail",
        "✅ PASSED (Protected)" if evaluation.get("confidentiality_maintained") else "❌ BREACHED",
        "Shielded competing company identity & preserved integrity",
    )
    table.add_row(
        "Value & Metric Framing",
        f"{evaluation.get('value_framing_score')}/100",
        "Quantified production agentic impact (60% review, 70% IT)",
    )
    table.add_row(
        "Overall Negotiation Score",
        f"[bold green]{evaluation.get('overall_score')}/100[/bold green]",
        "Executive-grade commercial positioning",
    )

    console.print(table)

    console.print("\n[bold green]Key Strengths:[/bold green]")
    for s in evaluation.get("strengths", []):
        console.print(f"  • {s}")

    console.print("\n[bold yellow]Recommended Next Turn Verbatim Script:[/bold yellow]")
    console.print(Panel(evaluation.get("recommended_counter_script", ""), border_style="magenta"))

    # Generate Recruiter Counter-Pushback
    counter = await engine.generate_recruiter_counter(candidate_reply)
    console.print(Panel(counter, title="🏢 Recruiter Immediate Pushback / Counter-Offer", border_style="yellow"))


async def run_interactive(scenario: str):
    console = Console()
    console.print(f"\n[bold cyan]═══ Module 6: Live Negotiation & Counter-Offer Simulator ═══[/bold cyan]")
    console.print(f"[dim]Scenario: {scenario.upper()} | Type 'quit' or 'exit' to end the session.[/dim]\n")

    engine = NegotiationSimulatorEngine(scenario=scenario)
    opener = engine.get_initial_opener()

    console.print(Panel(opener, title="🏢 HR Recruiter", border_style="red"))
    last_challenge = opener

    turn = 1
    while turn <= 5:
        candidate_input = Prompt.ask(f"\n[bold green]Turn {turn} - Your Response[/bold green]")
        if candidate_input.lower() in ("quit", "exit", "q"):
            console.print("\n[bold yellow]Ending simulation session.[/bold yellow]")
            break

        # 1. Coach Evaluation
        evaluation = await engine.evaluate_candidate_response(
            candidate_response=candidate_input,
            last_recruiter_challenge=last_challenge,
        )

        conf_status = "✅ PASSED" if evaluation.get("confidentiality_maintained") else "🚨 BREACHED (Company revealed)"
        console.print(Panel(
            f"[bold]Overall Score:[/bold] {evaluation.get('overall_score')}/100  |  "
            f"[bold]Anchoring:[/bold] {evaluation.get('anchoring_score')}/100  |  "
            f"[bold]Confidentiality:[/bold] {conf_status}\n"
            f"[bold]Value & Metrics:[/bold] {evaluation.get('value_framing_score')}/100\n\n"
            f"[bold yellow]Coach Feedback:[/bold yellow]\n" +
            "\n".join([f"  • {v}" for v in evaluation.get("vulnerabilities", [])]) + "\n\n" +
            f"[bold magenta]Recommended Counter-Script:[/bold magenta]\n{evaluation.get('recommended_counter_script')}",
            title="🎯 Coach Tactical Analysis",
            border_style="cyan"
        ))

        # 2. Recruiter Counter
        recruiter_reply = await engine.generate_recruiter_counter(candidate_input)
        console.print(Panel(recruiter_reply, title="🏢 HR Recruiter Pushback", border_style="red"))
        last_challenge = recruiter_reply
        turn += 1

    console.print("\n[bold green]Simulation Completed. Review coach transcripts in logs/orchestrator.log[/bold green]")


async def main():
    parser = argparse.ArgumentParser(
        description="Job Hunt Orchestrator - Module 6: Counter-Offer & Interview Simulator"
    )
    parser.add_argument(
        "--scenario",
        choices=["pune_gcc", "dubai_agency"],
        default="pune_gcc",
        help="Negotiation scenario (default: pune_gcc)",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run automated tactical benchmark evaluation on strategic anchor scripts",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run live interactive conversational terminal roleplay",
    )

    args = parser.parse_args()

    if args.interactive:
        await run_interactive(scenario=args.scenario)
    else:
        # Default to benchmark mode for non-interactive / test runs
        await run_benchmark(scenario=args.scenario)


if __name__ == "__main__":
    asyncio.run(main())
