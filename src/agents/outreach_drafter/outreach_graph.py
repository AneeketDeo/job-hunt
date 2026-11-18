import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END

from config.settings import settings
from src.core.logger import logger
from src.integrations.ollama_client import OllamaClient
from src.integrations.gmail_client import GmailClient


class OutreachDrafterState(TypedDict, total=False):
    jd_text: str
    recipient_name: Optional[str]
    recipient_title: Optional[str]
    recipient_email: Optional[str]
    company_name: Optional[str]
    target_location: Optional[str]
    pain_point: str
    matched_anchor_project: Dict[str, Any]
    cover_letter_markdown: str
    direct_message_150words: str
    email_subject: str
    gmail_draft_result: Dict[str, Any]
    status: str
    errors: List[str]


class ContextAwareOutreachAgent:
    """Module 4: Context-Aware Cover Letter & DM Drafter

    Uses LangGraph to extract core pain points from target JDs, maps them to the
    candidate's production anchor project metrics, generates a formal 3-paragraph
    Cover Letter, crafts a punchy 150-word InMail message, and saves outbound drafts
    directly into Gmail Drafts.
    """

    def __init__(self):
        self.ollama = OllamaClient()
        self.gmail = GmailClient()
        self.profile_data = self._load_profile_data()
        self.workflow = self._build_graph()

    def _load_profile_data(self) -> Dict[str, Any]:
        profile_file = settings.DATA_DIR / "profile_data.json"
        with open(profile_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(OutreachDrafterState)

        builder.add_node("extract_pain_point", self.extract_pain_point_node)
        builder.add_node("draft_cover_letter", self.draft_cover_letter_node)
        builder.add_node("draft_direct_message", self.draft_direct_message_node)
        builder.add_node("save_gmail_draft", self.save_gmail_draft_node)

        builder.set_entry_point("extract_pain_point")
        builder.add_edge("extract_pain_point", "draft_cover_letter")
        builder.add_edge("draft_cover_letter", "draft_direct_message")
        builder.add_edge("draft_direct_message", "save_gmail_draft")
        builder.add_edge("save_gmail_draft", END)

        return builder.compile()

    # -------------------------------------------------------------------------
    # NODE 1: PAIN POINT EXTRACTION & ANCHOR PROJECT MAPPING
    # -------------------------------------------------------------------------
    async def extract_pain_point_node(self, state: OutreachDrafterState) -> Dict[str, Any]:
        logger.info("LangGraph Outreach Node [1/4]: Extracting JD pain points and mapping anchor project...")
        jd_text = state.get("jd_text", "")
        company = state.get("company_name", "Target Company")
        location = state.get("target_location", "Pune / Dubai")

        projects = self.profile_data.get("anchor_projects", [])

        prompt = f"""You are an executive talent strategist. Read this Job Description for {company} in {location}.
Identify the primary operational pain point or technical bottleneck the company wants to solve (e.g., manual review delays, support ticket overload, MTTR delays, or slow reporting).
Map it to one of these 4 project IDs:
1. "contract_redlining" (Contract review delays, legal risk, document extraction)
2. "agent_assist" (IT support ticket backlog, multi-agent automated triage)
3. "network_rag" (Telemetry, incident response, network MTTR)
4. "query_iq" (Slow BI reporting, text-to-sql, data accessibility)

Return JSON with:
- "pain_point": string (1 concise sentence describing the core pain point)
- "selected_project_id": string ("contract_redlining" | "agent_assist" | "network_rag" | "query_iq")
- "company_name": string
- "target_location": string ("Dubai, UAE" | "Pune, India")

Job Description:
{jd_text[:3000]}
"""
        extracted = await self.ollama.generate_json(prompt=prompt)

        # Fallback heuristic if Ollama is unreachable
        if not extracted or not extracted.get("selected_project_id"):
            text_lower = jd_text.lower()
            if "contract" in text_lower or "legal" in text_lower or "document" in text_lower or "compliance" in text_lower or "redlin" in text_lower:
                selected_id = "contract_redlining"
                pain = "High manual review latency and legal compliance verification bottlenecks."
            elif "incident" in text_lower or "network" in text_lower or "mttr" in text_lower or "telemetry" in text_lower:
                selected_id = "network_rag"
                pain = "Prolonged mean-time-to-resolution (MTTR) on complex technical support incidents."
            elif "sql" in text_lower or "analytics" in text_lower or "bi" in text_lower or "report" in text_lower:
                selected_id = "query_iq"
                pain = "Slow turnaround times for business users requesting ad-hoc data analytics."
            else:
                selected_id = "agent_assist"
                pain = "Scaling internal operational issue resolution without linearly growing support headcount."

            extracted = {
                "pain_point": pain,
                "selected_project_id": selected_id,
                "company_name": state.get("company_name") or "Target Company",
                "target_location": state.get("target_location") or ("Dubai, UAE" if "dubai" in text_lower else "Pune, India"),
            }

        selected_id = extracted.get("selected_project_id", "contract_redlining")
        matched_project = next((p for p in projects if p["id"] == selected_id), projects[0])

        logger.info(
            f"Mapped Core Pain Point: '{extracted.get('pain_point')}' -> "
            f"Anchor Project: [{matched_project['name']}] ({matched_project['metrics']})"
        )

        return {
            "pain_point": extracted.get("pain_point", ""),
            "matched_anchor_project": matched_project,
            "company_name": extracted.get("company_name", company),
            "target_location": extracted.get("target_location", location),
        }

    # -------------------------------------------------------------------------
    # NODE 2: FORMAL 3-PARAGRAPH COVER LETTER (ATS UPLOAD)
    # -------------------------------------------------------------------------
    async def draft_cover_letter_node(self, state: OutreachDrafterState) -> Dict[str, Any]:
        logger.info("LangGraph Outreach Node [2/4]: Generating formal 3-paragraph Cover Letter...")
        personal = self.profile_data["personal"]
        company = state.get("company_name", "Target Organization")
        location = state.get("target_location", "Pune, India")
        proj = state.get("matched_anchor_project", {})
        pain = state.get("pain_point", "")

        is_dubai = "dubai" in location.lower() or "uae" in location.lower()

        prompt = f"""Write a formal, compelling, high-converting 3-paragraph Cover Letter for a Senior GenAI / Agentic Systems Engineer application at {company} in {location}.

Candidate Profile:
- Name: {personal['full_name']}
- Experience: 3 years building production autonomous agents and RAG pipelines.
- Addressed Pain Point: {pain}
- Anchor Project Proof: {proj.get('name')} | Stack: {', '.join(proj.get('stack', []))} | Impact: {proj.get('metrics')}.
- Specific Accomplishment: {proj.get('core_pitch')}
- Relocation / Location: {'Immediate relocation to Dubai (Non-Resident / Willing to relocate - Immediately)' if is_dubai else 'Based in Pune, targeting GCC engineering leadership'}

Structure:
- Paragraph 1: Hook, role enthusiasm, acknowledgement of {company}'s focus and the specific challenge ({pain}).
- Paragraph 2: Deep technical walkthrough of how {proj.get('name')} solved a nearly identical bottleneck, highlighting exact stack ({', '.join(proj.get('stack', [])[:4])}) and quantifiable metric ({proj.get('metrics')}).
- Paragraph 3: Readiness with private local models (Ollama/Llama 3), FastAPI, deterministic guardrails, compensation/relocation alignment, and clear call to action.

Return ONLY the Markdown formatted letter with standard greeting and sign-off.
"""
        letter = await self.ollama.generate(prompt=prompt)

        if not letter or len(letter) < 150:
            # Deterministic high-converting template fallback
            relocation_line = (
                "I am currently based in Pune with full readiness for immediate relocation to Dubai as a Non-Resident."
                if is_dubai
                else "I am actively based in Pune, ready to deploy high-impact architectures across your engineering pods."
            )

            letter = f"""Dear Hiring Team at {company},

I am writing to express my strong enthusiasm for the GenAI / Agentic Systems Engineer role at {company}. Having spent the last 3 years engineering and shipping autonomous multi-agent architectures and high-precision retrieval systems, I have closely tracked your technological roadmap. I understand that a core operational imperative for {company} is tackling {pain.lower().rstrip('.')}, and I am eager to apply my production experience to deliver immediate architectural velocity to your team.

At the center of my production work is my experience architecting {proj.get('name')}, which directly delivered {proj.get('metrics').lower()}. Built on a resilient stack of {', '.join(proj.get('stack', []))}, this system automated mission-critical workflows with hierarchical self-critique loops and strict compliance guardrails. By fusing dense and sparse search techniques with deterministic fallback mechanisms, we eliminated severe manual bottlenecks and brought review cycles down by 60%. I am confident that deploying similar agentic state machines will yield identical high-reliability outcomes for {company}.

My technical foundation pairs cutting-edge agentic frameworks (LangGraph, AutoGen GroupChatManager, CrewAI) with rock-solid production engineering (FastAPI, Redis, Docker, and local Ollama inference for zero data leakage). {relocation_line} I have a 30-day negotiable notice period and would welcome the opportunity to discuss how my hands-on background can accelerate your upcoming quarterly deliverables.

Sincerely,  
**{personal['full_name']}**  
{personal['title']}  
📞 {personal['phone']} | ✉️ {personal['email']}  
[LinkedIn]({personal['linkedin']}) | [GitHub]({personal['github']})
"""

        # Save Cover Letter to data/generated_resumes/
        company_slug = re.sub(r"[^a-zA-Z0-9]", "_", company).lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cl_path = settings.DATA_DIR / "generated_resumes" / f"cover_letter_{company_slug}_{timestamp}.md"
        with open(cl_path, "w", encoding="utf-8") as f:
            f.write(letter)

        logger.info(f"Generated formal Cover Letter saved to: {cl_path}")
        return {"cover_letter_markdown": letter}

    # -------------------------------------------------------------------------
    # NODE 3: PUNCHY 150-WORD DIRECT MESSAGE (LINKEDIN / CUTSHORT)
    # -------------------------------------------------------------------------
    async def draft_direct_message_node(self, state: OutreachDrafterState) -> Dict[str, Any]:
        logger.info("LangGraph Outreach Node [3/4]: Drafting punchy 150-word InMail / Direct Message...")
        personal = self.profile_data["personal"]
        company = state.get("company_name", "Target Organization")
        recipient = state.get("recipient_name", "Hiring Leader")
        location = state.get("target_location", "Pune / Dubai")
        proj = state.get("matched_anchor_project", {})

        is_dubai = "dubai" in location.lower() or "uae" in location.lower()
        salutation = f"Hi {recipient.split()[0]}," if recipient and " " in recipient else f"Hi {recipient},"

        prompt = f"""Write a high-converting, punchy 150-word direct message (LinkedIn InMail / Cutshort DM) to {recipient} at {company}.
Candidate: GenAI Engineer (3 YOE)
Anchor Highlight: {proj.get('name')} -> {proj.get('core_pitch')} ({proj.get('metrics')})
Stack: {', '.join(proj.get('stack', [])[:4])}
Location Context: {'Willing to relocate to Dubai immediately' if is_dubai else 'Based in Pune (18-21+ LPA anchor)'}

Tone: Direct, technical, zero corporate fluff, quantified value-first.
Strict Constraint: Under 150 words.
"""
        dm = await self.ollama.generate(prompt=prompt)

        if not dm or len(dm.split()) > 180 or len(dm) < 50:
            # Deterministic fallback DM (exactly ~120 words)
            loc_context = "ready for immediate Dubai relocation" if is_dubai else "based locally in Pune"
            dm = (
                f"{salutation}\n\n"
                f"I noticed {company}'s ongoing expansion in autonomous GenAI solutions. As an engineer with 3 years "
                f"of production experience shipping multi-agent architectures (LangGraph, AutoGen GroupChatManager) and "
                f"hybrid RAG, I wanted to reach out directly.\n\n"
                f"Recently, I architected {proj.get('name')}, where we achieved {proj.get('metrics')} using "
                f"{', '.join(proj.get('stack', [])[:3])}. We resolved complex operational bottlenecks while keeping data "
                f"private with local Ollama inference.\n\n"
                f"I'm currently {loc_context} with a 30-day notice. Would you be open to a brief 10-minute technical exchange "
                f"this Tuesday or Thursday to see if my background matches your immediate engineering roadmap?\n\n"
                f"Best regards,\n"
                f"{personal['full_name']} | {personal['phone']}"
            )

        logger.info(f"Direct Message drafted ({len(dm.split())} words).")
        return {
            "direct_message_150words": dm.strip(),
            "email_subject": f"GenAI Engineer (3 YOE) | Multi-Agent Systems & RAG Architecture - {company}",
        }

    # -------------------------------------------------------------------------
    # NODE 4: GMAIL DRAFT INTEGRATION
    # -------------------------------------------------------------------------
    async def save_gmail_draft_node(self, state: OutreachDrafterState) -> Dict[str, Any]:
        logger.info("LangGraph Outreach Node [4/4]: Saving personalized outreach email to Gmail Drafts...")
        recipient_email = state.get("recipient_email") or "engineering-hiring@example.com"
        subject = state.get("email_subject", "GenAI Engineer Introduction")
        cover_letter = state.get("cover_letter_markdown", "")
        dm = state.get("direct_message_150words", "")

        # Use the 3-paragraph cover letter formatted as the email body, with DM as alternative
        email_body = f"""{dm}

---
ATTACHED FORMAL STATEMENT OF INTENT:

{cover_letter}
"""

        draft_result = self.gmail.create_draft(
            to_email=recipient_email,
            subject=subject,
            body_text=email_body,
        )

        return {
            "gmail_draft_result": draft_result,
            "status": "outreach_completed",
        }

    # -------------------------------------------------------------------------
    # PUBLIC RUNNER
    # -------------------------------------------------------------------------
    async def generate_outreach(
        self,
        jd_text: str,
        company_name: Optional[str] = None,
        recipient_name: Optional[str] = None,
        recipient_title: Optional[str] = None,
        recipient_email: Optional[str] = None,
        target_location: Optional[str] = None,
    ) -> OutreachDrafterState:
        """Execute the LangGraph outreach pipeline."""
        initial_state: OutreachDrafterState = {
            "jd_text": jd_text,
            "company_name": company_name,
            "recipient_name": recipient_name,
            "recipient_title": recipient_title,
            "recipient_email": recipient_email,
            "target_location": target_location,
            "errors": [],
        }
        return await self.workflow.ainvoke(initial_state)
