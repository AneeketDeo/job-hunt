import json
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from config.settings import settings
from src.core.logger import logger
from src.core.llm_router import LLMRouter
from src.agents.simulator.prompts import (
    HR_PUNE_GCC_SYSTEM_PROMPT,
    HR_DUBAI_AGENCY_SYSTEM_PROMPT,
    COACH_EVALUATOR_SYSTEM_PROMPT,
)


class NegotiationTurnResult(BaseModel):
    turn_index: int
    recruiter_message: str
    candidate_message: Optional[str] = None
    anchoring_score: int = 0
    confidentiality_maintained: bool = True
    value_framing_score: int = 0
    overall_score: int = 0
    strengths: List[str] = Field(default_factory=list)
    vulnerabilities: List[str] = Field(default_factory=list)
    recommended_counter_script: str = ""


class NegotiationSimulatorEngine:
    """Module 6: Counter-Offer & Interview Simulator Engine

    Simulates high-stakes compensation negotiations between an ambitious GenAI engineer
    and seasoned HR recruiters in Pune GCCs and Dubai AI agencies.
    """

    def __init__(self, scenario: str = "pune_gcc", temperature: float = 0.3):
        self.scenario = scenario.lower()
        self.router = LLMRouter(temperature=temperature)
        self.conversation_history: List[Dict[str, str]] = []
        self.turn_count = 0
        self.profile_data = self._load_profile_data()

    def _load_profile_data(self) -> Dict[str, Any]:
        with open(settings.DATA_DIR / "profile_data.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def get_system_prompt(self) -> str:
        if "dubai" in self.scenario:
            return HR_DUBAI_AGENCY_SYSTEM_PROMPT
        return HR_PUNE_GCC_SYSTEM_PROMPT

    def get_initial_opener(self) -> str:
        """Provide realistic, high-pressure opening line from HR recruiter."""
        if "dubai" in self.scenario:
            return (
                "Hi! The technical team was thoroughly impressed with your agentic architecture portfolio. "
                "However, your expectation of AED 18,000 – 22,000 is quite steep for an overseas hire without a UAE residence visa. "
                "Given your 14 LPA Indian baseline, an offer of AED 14,000 per month tax-free with full visa sponsorship is already "
                "more than double your current earnings. Why should we stretch to AED 20,000?"
            )
        return (
            "Hi! We've completed your leadership interview rounds for the Senior GenAI role, and feedback is strong. "
            "However, on compensation: our standard GCC policy caps 3 YOE profiles at a 20% hike, which puts you at 16.5 LPA fixed. "
            "You mentioned having a competing 14 LPA offer on the table. To evaluate an exception for 18-21 LPA, which company "
            "issued that offer, and can you share their formal letter with us today?"
        )

    async def evaluate_candidate_response(
        self,
        candidate_response: str,
        last_recruiter_challenge: str,
    ) -> Dict[str, Any]:
        """Coach evaluates candidate response on target anchoring, confidentiality, and metric framing."""
        # 1. Rule-based confidentiality guardrail check
        confidentiality_failed = False
        forbidden_patterns = [
            r"offer is from\s+([A-Za-z0-9]+)",
            r"company is\s+([A-Za-z0-9]+)",
            r"it is\s+(google|microsoft|amazon|tcs|infosys|wipro|cognizant|accenture|meta)",
            r"i got the offer from\s+([A-Za-z0-9]+)",
        ]
        for pattern in forbidden_patterns:
            if re.search(pattern, candidate_response, re.IGNORECASE):
                confidentiality_failed = True
                break

        # 2. Rule-based metrics & tech stack keyword detection
        has_metrics = any(
            m in candidate_response.lower()
            for m in ["60%", "70%", "40%", "50%", "reduction", "automated", "mttr", "latency"]
        )
        has_tech = any(
            t in candidate_response.lower()
            for t in ["autogen", "langgraph", "crewai", "chromadb", "bge large", "bm25", "query fusion", "fastapi", "ollama"]
        )
        defended_anchor = any(
            a in candidate_response.lower()
            for a in ["18", "19", "20", "21", "aed 18", "aed 20", "aed 22", "20,000", "22,000"]
        )

        prompt = f"""Recruiter Challenge:
"{last_recruiter_challenge}"

Candidate Response:
"{candidate_response}"

Evaluate the candidate's negotiation tactics based on:
1. Did they hold the target anchor (18-21+ LPA or AED 18K-22K)?
2. Did they maintain strict confidentiality without naming the competing firm?
3. Did they frame value using quantifiable production metrics (60% review reduction, 70% IT incident automation, 40% MTTR reduction)?
4. What is the verbatim counter-script they should use for maximum leverage?
"""
        eval_json = await self.router.generate_json(
            prompt=prompt,
            system_prompt=COACH_EVALUATOR_SYSTEM_PROMPT,
        )

        if not eval_json or not isinstance(eval_json, dict):
            # Deterministic fallback evaluation
            overall = 75
            if not defended_anchor:
                overall -= 25
            if confidentiality_failed:
                overall -= 30
            if has_metrics:
                overall += 15

            vulnerabilities = []
            if confidentiality_failed:
                vulnerabilities.append("Disclosed or hinted at the competing company identity; violates confidentiality.")
            if not defended_anchor:
                vulnerabilities.append("Did not explicitly re-anchor to the 18-21+ LPA / AED 18K-22K range.")
            if not has_metrics:
                vulnerabilities.append("Lacked hard production metrics; sounded like a general plea rather than an ROI justification.")

            strengths = []
            if not confidentiality_failed:
                strengths.append("Strictly safeguarded competing offer confidentiality.")
            if has_metrics:
                strengths.append("Anchored value in production quantifiable impact.")
            if defended_anchor:
                strengths.append("Stood firm on target compensation band.")

            recommended = (
                "I appreciate you sharing your standard band, but my expectation remains anchored at 19-21 LPA based on immediate production velocity. "
                "Out of respect for mutual confidentiality and signed NDAs, I cannot disclose the other firm's name, but their offer reflects the market value "
                "of someone who has automated 70% of IT incidents with AutoGen and cut contract review latency by 60% with hybrid RAG. "
                "If we can align around 20 LPA fixed, I am prepared to sign with your team immediately."
            )

            eval_json = {
                "anchoring_score": 85 if defended_anchor else 50,
                "confidentiality_maintained": not confidentiality_failed,
                "value_framing_score": 85 if (has_metrics and has_tech) else 55,
                "overall_score": max(min(overall, 100), 20),
                "strengths": strengths or ["Maintained professional composure."],
                "vulnerabilities": vulnerabilities or ["Can push harder on immediate sign-on timeline."],
                "recommended_counter_script": recommended,
            }

        return eval_json

    async def generate_recruiter_counter(self, candidate_response: str) -> str:
        """Generate realistic pushback from the HR persona."""
        self.conversation_history.append({"role": "candidate", "content": candidate_response})

        history_str = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in self.conversation_history[-6:]])

        prompt = f"""Conversation History:
{history_str}

Candidate just said:
"{candidate_response}"

Respond in character as the HR recruiter.
Push back firmly. If they refuse to reveal the company name, test their conviction. If they ask for 18-21 LPA / AED 20K, counter-propose a compromise with performance bonuses, or demand proof of their agentic metrics.
Keep response concise (2-4 sentences).
"""
        response = await self.router.generate(
            prompt=prompt,
            system_prompt=self.get_system_prompt(),
        )

        if not response or len(response) < 20:
            if "dubai" in self.scenario:
                response = (
                    "I respect your point on the 60% review reduction, but Dubai is a different operating environment. "
                    "How about we structure this as AED 16,000 basic plus a quarterly performance bonus tied to agent reliability? "
                    "That gets you close to your AED 19,000 target while managing our risk during your 6-month probation."
                )
            else:
                response = (
                    "I understand you have an NDA, but without seeing the competing offer letter, management cannot sign off on an exception above 16.5 LPA fixed. "
                    "Can we bridge the gap with a 2 LPA joining bonus payable after 90 days, bringing your first-year CTC to 18.5 LPA?"
                )

        self.conversation_history.append({"role": "recruiter", "content": response.strip()})
        self.turn_count += 1
        return response.strip()
