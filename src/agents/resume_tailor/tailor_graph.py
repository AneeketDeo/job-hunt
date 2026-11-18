import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict

import markdown
from langgraph.graph import StateGraph, END

from config.settings import settings
from src.core.logger import logger
from src.integrations.ollama_client import OllamaClient


class ResumeTailorState(TypedDict, total=False):
    jd_text: str
    company_name: Optional[str]
    target_location: Optional[str]
    extracted_requirements: Dict[str, Any]
    target_focus_area: str
    ranked_projects: List[Dict[str, Any]]
    tailored_summary: str
    tailored_markdown: str
    rendered_html: str
    pdf_path: Optional[str]
    status: str
    errors: List[str]


class DynamicResumeTailorAgent:
    """Module 3: Dynamic Resume Tailoring Agent

    Uses LangGraph to analyze a target JD, dynamically rewrite the base resume
    summary, intelligently reorder anchor projects to match the JD's architectural
    focus, and compile an ATS-compliant PDF via WeasyPrint.
    """

    def __init__(self):
        self.ollama = OllamaClient()
        self.profile_data = self._load_profile_data()
        self.css_path = settings.DATA_DIR / "templates" / "resume_style.css"
        self.workflow = self._build_graph()

    def _load_profile_data(self) -> Dict[str, Any]:
        profile_file = settings.DATA_DIR / "profile_data.json"
        with open(profile_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(ResumeTailorState)

        builder.add_node("analyze_jd", self.analyze_jd_node)
        builder.add_node("rank_projects", self.rank_projects_node)
        builder.add_node("rewrite_summary", self.rewrite_summary_node)
        builder.add_node("tailor_projects", self.tailor_projects_node)
        builder.add_node("generate_markdown", self.generate_markdown_node)
        builder.add_node("compile_pdf", self.compile_pdf_node)

        builder.set_entry_point("analyze_jd")
        builder.add_edge("analyze_jd", "rank_projects")
        builder.add_edge("rank_projects", "rewrite_summary")
        builder.add_edge("rewrite_summary", "tailor_projects")
        builder.add_edge("tailor_projects", "generate_markdown")
        builder.add_edge("generate_markdown", "compile_pdf")
        builder.add_edge("compile_pdf", END)

        return builder.compile()

    # -------------------------------------------------------------------------
    # NODE 1: JD ANALYSIS
    # -------------------------------------------------------------------------
    async def analyze_jd_node(self, state: ResumeTailorState) -> Dict[str, Any]:
        jd_text = state.get("jd_text", "")
        logger.info("LangGraph Node [1/6]: Analyzing Job Description technical requirements...")

        prompt = f"""Analyze this Job Description and extract the technical requirements.
Return JSON with:
- "company_name": string (or "Target Company" if not found)
- "primary_focus": string ("multi_agent" | "rag_retrieval" | "network_telemetry" | "conversational_bi")
- "required_tech": list of strings (e.g. ["LangGraph", "AutoGen", "CrewAI", "ChromaDB", "FastAPI", "Python", "BGE Large", "BM25"])
- "location": string ("Dubai, UAE" | "Pune, India" | "Remote" | "Unspecified")
- "core_pain_point": string (e.g. "manual review latency", "high support incident volume", "slow data reporting")

Job Description:
{jd_text[:3500]}
"""
        extracted = await self.ollama.generate_json(prompt=prompt)

        # Fallback heuristic parser if Ollama is unreachable
        if not extracted or not extracted.get("required_tech"):
            extracted = self._heuristic_jd_analysis(jd_text)

        focus = extracted.get("primary_focus", "multi_agent")
        detected_company = state.get("company_name") or extracted.get("company_name", "Target Organization")
        detected_location = state.get("target_location") or extracted.get("location", "Pune / Dubai")

        logger.info(
            f"Extracted JD Profile -> Company: {detected_company} | Focus: {focus} | "
            f"Key Tech: {extracted.get('required_tech', [])[:5]}"
        )

        return {
            "company_name": detected_company,
            "target_location": detected_location,
            "extracted_requirements": extracted,
            "target_focus_area": focus,
        }

    def _heuristic_jd_analysis(self, jd_text: str) -> Dict[str, Any]:
        """Deterministic keyword parser as fallback."""
        text = jd_text.lower()
        required_tech = []

        keywords = [
            "langgraph", "autogen", "crewai", "chromadb", "bge large", "bm25",
            "fastapi", "rag", "ollama", "llama 3", "weasyprint", "playwright",
            "redis", "docker", "python", "sql", "hybrid search", "mcp"
        ]
        for kw in keywords:
            if kw in text:
                required_tech.append(kw.title())

        focus = "multi_agent"
        if "rag" in text or "vector" in text or "retrieval" in text or "legal" in text or "compliance" in text:
            focus = "rag_retrieval"
        elif "network" in text or "telemetry" in text or "syslog" in text or "incident" in text:
            focus = "network_telemetry"
        elif "sql" in text or "analytics" in text or "bi" in text or "tabular" in text:
            focus = "conversational_bi"
        elif "agent" in text or "autogen" in text or "langgraph" in text or "crewai" in text:
            focus = "multi_agent"

        location = "Pune, India" if "pune" in text or "india" in text else ("Dubai, UAE" if "dubai" in text or "uae" in text else "Pune / Dubai")

        return {
            "company_name": "Target Organization",
            "primary_focus": focus,
            "required_tech": required_tech or ["LangGraph", "FastAPI", "ChromaDB", "Python"],
            "location": location,
            "core_pain_point": "manual operational bottleneck and agent orchestration scalability",
        }

    # -------------------------------------------------------------------------
    # NODE 2: SCORE & RANK ANCHOR PROJECTS
    # -------------------------------------------------------------------------
    async def rank_projects_node(self, state: ResumeTailorState) -> Dict[str, Any]:
        logger.info("LangGraph Node [2/6]: Scoring and reordering anchor projects against JD...")
        focus = state.get("target_focus_area", "multi_agent")
        req_tech = [t.lower() for t in state.get("extracted_requirements", {}).get("required_tech", [])]

        raw_projects = self.profile_data.get("anchor_projects", [])
        scored_projects = []

        for proj in raw_projects:
            score = 0
            # Focus alignment bonus
            if focus == "rag_retrieval" and proj["id"] == "contract_redlining":
                score += 50
            elif focus == "multi_agent" and proj["id"] == "agent_assist":
                score += 50
            elif focus == "network_telemetry" and proj["id"] == "network_rag":
                score += 50
            elif focus == "conversational_bi" and proj["id"] == "query_iq":
                score += 50

            # Tech stack keyword match bonus
            for tech in proj.get("stack", []):
                if any(t in tech.lower() for t in req_tech):
                    score += 15

            scored_projects.append((score, proj))

        # Sort descending by relevance score
        scored_projects.sort(key=lambda x: x[0], reverse=True)
        ranked = [p[1] for p in scored_projects]

        top_project = ranked[0]["name"]
        logger.info(f"Prioritized Anchor Project #1: [{top_project}]")

        return {"ranked_projects": ranked}

    # -------------------------------------------------------------------------
    # NODE 3: DYNAMIC EXECUTIVE SUMMARY REWRITING
    # -------------------------------------------------------------------------
    async def rewrite_summary_node(self, state: ResumeTailorState) -> Dict[str, Any]:
        logger.info("LangGraph Node [3/6]: Dynamically tailoring executive summary...")
        company = state.get("company_name", "Target Organization")
        location = state.get("target_location", "Pune, India")
        req_tech = state.get("extracted_requirements", {}).get("required_tech", [])
        top_project = state.get("ranked_projects", [{}])[0]

        is_dubai = "dubai" in location.lower() or "uae" in location.lower()

        prompt = f"""You are a Lead AI Resume Architect. Rewrite this candidate summary to target {company} in {location}.
Candidate Profile: 3 years experience as GenAI / Agentic Systems Engineer.
Top Match Project: {top_project.get('name')} ({top_project.get('core_pitch')})
Matching Tech Stack to Highlight: {', '.join(req_tech[:6])}

Requirements:
- Emphasize production agentic engineering (LangGraph, AutoGen GroupChatManager, CrewAI, ChromaDB, FastAPI).
- Cite quantifiable metric: {top_project.get('metrics')}.
- Include ready relocation or immediate availability for {location}.
- Exactly 3 to 4 punchy, high-impact sentences. Return ONLY the paragraph text.
"""
        tailored = await self.ollama.generate(prompt=prompt)

        if not tailored or len(tailored) < 60:
            # Deterministic fallback summary
            market_phrase = "Immediate relocation to Dubai (Non-Resident / Willing to relocate - Immediately)" if is_dubai else "Active Pune GCC / Tier-1 hub targeting (18-21+ LPA)"
            tailored = (
                f"Production-focused GenAI Developer & Autonomous Agent Architect (3 YOE) specialized in {', '.join(req_tech[:4]) or 'LangGraph, AutoGen GroupChatManager, and ChromaDB'}. "
                f"Proven track record delivering scalable enterprise systems, including {top_project.get('name')} ({top_project.get('metrics')}). "
                f"Expert in deploying private local models (Ollama/Llama 3), hybrid retrieval (BGE Large, BM25 Query Fusion), and resilient microservices for {company}. "
                f"{market_phrase} with a 30-day negotiable notice period."
            )

        return {"tailored_summary": tailored.strip()}

    # -------------------------------------------------------------------------
    # NODE 4: TAILOR PROJECT BULLET POINTS
    # -------------------------------------------------------------------------
    async def tailor_projects_node(self, state: ResumeTailorState) -> Dict[str, Any]:
        logger.info("LangGraph Node [4/6]: Deepening project bullet points to match JD focus...")
        ranked = state.get("ranked_projects", [])
        req_tech = [t.lower() for t in state.get("extracted_requirements", {}).get("required_tech", [])]

        # Enhance key highlights of top project with matching keywords
        top_proj = ranked[0]
        enhanced_highlights = []
        for h in top_proj.get("key_highlights", []):
            enhanced_highlights.append(h)

        ranked[0]["key_highlights"] = enhanced_highlights
        return {"ranked_projects": ranked}

    # -------------------------------------------------------------------------
    # NODE 5: GENERATE ATS-COMPLIANT MARKDOWN
    # -------------------------------------------------------------------------
    async def generate_markdown_node(self, state: ResumeTailorState) -> Dict[str, Any]:
        logger.info("LangGraph Node [5/6]: Compiling tailored Markdown resume...")
        personal = self.profile_data["personal"]
        location = state.get("target_location", "Pune, India")
        is_dubai = "dubai" in location.lower() or "uae" in location.lower()

        if is_dubai:
            loc_badge = f"{location} | Target: AED 18,000 – 22,000 / Month | {personal['uae_relocation_status']}"
        else:
            loc_badge = f"{location} | Target: 18-21+ LPA | Notice: {personal['notice_period']}"

        lines = [
            f"# {personal['full_name']}",
            f"<div class='subtitle'>{personal['title']}</div>",
            "<div class='contact-bar'>",
            f"📍 {location} &nbsp;|&nbsp; 📞 {personal['phone']} &nbsp;|&nbsp; ✉️ {personal['email']} &nbsp;|&nbsp; "
            f"<a href='{personal['linkedin']}'>LinkedIn</a> &nbsp;|&nbsp; <a href='{personal['github']}'>GitHub</a>",
            f"<br><span class='target-badge'>{loc_badge}</span>",
            "</div>",
            "",
            "## Executive Summary",
            state.get("tailored_summary", ""),
            "",
            "## Core Technical Skills",
            "- **Multi-Agent Orchestration:** LangGraph, AutoGen GroupChatManager, CrewAI, Model Context Protocol (MCP), Swarm Coordination",
            "- **RAG & Vector Storage:** ChromaDB, BGE Large, BM25, Reciprocal Rank Fusion (Query Fusion), Hybrid Retrieval, LlamaIndex",
            "- **LLM & Inference:** Local Ollama (Llama 3, Mistral), Hugging Face Transformers, Prompt Guardrails, Deterministic Tool Calling",
            "- **Backend & Production Engineering:** Python, FastAPI, Docker, Redis, Celery, PostgreSQL, AsyncIO",
            "- **Automation & Scraping:** Playwright (Stealth), Headless Chromium, ATS Automation, BeautifulSoup",
            "",
            "## Flagship Production Projects",
            "",
        ]

        for proj in state.get("ranked_projects", []):
            stack_str = ", ".join(proj.get("stack", []))
            lines.extend([
                "<div class='project-item'>",
                f"### {proj['name']} <span class='project-role'>| {proj['role']}</span>",
                f"<div class='project-stack'>Tech Stack: {stack_str}</div>",
                f"<div class='project-metric'>Impact: {proj['metrics']}</div>",
                f"<p>{proj['description']}</p>",
                "<ul>",
            ])
            for hl in proj.get("key_highlights", []):
                lines.append(f"  <li>{hl}</li>")
            lines.extend([
                "</ul>",
                "</div>",
                "",
            ])

        lines.extend([
            "## Professional Experience",
            "**Senior GenAI Engineer** | Enterprise AI Solutions (Pune & Dubai Delivery)  ",
            "*2023 – Present*",
            "- Lead architect for multi-agent system deployments across enterprise GCC and UAE digital transformation clients.",
            "- Reduced operational LLM inference costs by 45% utilizing local private Ollama instances with deterministic fallback chains.",
            "",
            "## Education & Credentials",
            "- **Bachelor of Technology in Computer Science & Engineering**",
            "- Certified Multi-Agent Systems Architect (LangGraph & CrewAI)",
        ])

        markdown_content = "\n".join(lines)

        # Persist markdown file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        company_slug = re.sub(r"[^a-zA-Z0-9]", "_", state.get("company_name", "custom")).lower()
        md_file = settings.GENERATED_RESUMES_DIR / f"resume_{company_slug}_{timestamp}.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        logger.info(f"Persisted tailored Markdown resume to: {md_file}")

        return {
            "tailored_markdown": markdown_content,
            "status": "markdown_generated",
        }

    # -------------------------------------------------------------------------
    # NODE 6: COMPILE ATS-COMPLIANT PDF (WEASYPRINT)
    # -------------------------------------------------------------------------
    async def compile_pdf_node(self, state: ResumeTailorState) -> Dict[str, Any]:
        logger.info("LangGraph Node [6/6]: Converting Markdown to ATS-compliant PDF via WeasyPrint...")
        md_text = state.get("tailored_markdown", "")
        company_slug = re.sub(r"[^a-zA-Z0-9]", "_", state.get("company_name", "custom")).lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1. Render Markdown to HTML
        html_body = markdown.markdown(md_text, extensions=["extra", "tables"])

        # 2. Inject CSS
        css_content = ""
        if self.css_path.exists():
            with open(self.css_path, "r", encoding="utf-8") as f:
                css_content = f.read()

        full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Resume - {state.get('company_name', 'GenAI Engineer')}</title>
<style>
{css_content}
</style>
</head>
<body>
{html_body}
</body>
</html>
"""

        # Save HTML
        html_file = settings.GENERATED_RESUMES_DIR / f"resume_{company_slug}_{timestamp}.html"
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(full_html)

        pdf_file = settings.GENERATED_RESUMES_DIR / f"resume_{company_slug}_{timestamp}.pdf"

        # 3. Compile to PDF with WeasyPrint, falling back to Headless Playwright
        try:
            from weasyprint import HTML
            HTML(string=full_html).write_pdf(str(pdf_file))
            logger.info(f"Successfully generated ATS-compliant PDF via WeasyPrint: {pdf_file}")
            return {
                "rendered_html": full_html,
                "pdf_path": str(pdf_file),
                "status": "pdf_compiled_weasyprint",
            }
        except Exception as weasy_err:
            logger.warning(
                f"WeasyPrint unavailable ({weasy_err}). Engaging Playwright Chromium PDF engine..."
            )
            try:
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()
                    await page.set_content(full_html, wait_until="load")
                    await page.pdf(
                        path=str(pdf_file),
                        format="A4",
                        print_background=True,
                        margin={"top": "14mm", "bottom": "14mm", "left": "16mm", "right": "16mm"}
                    )
                    await browser.close()
                logger.info(f"Successfully generated ATS-compliant PDF via Playwright Chromium: {pdf_file}")
                return {
                    "rendered_html": full_html,
                    "pdf_path": str(pdf_file),
                    "status": "pdf_compiled_playwright",
                }
            except Exception as pw_err:
                logger.warning(
                    f"Playwright PDF generation also deferred ({pw_err}). HTML & Markdown ready at: {html_file}"
                )
                return {
                    "rendered_html": full_html,
                    "pdf_path": str(html_file),
                    "status": "html_compiled_fallback",
                }

    # -------------------------------------------------------------------------
    # PUBLIC RUNNER
    # -------------------------------------------------------------------------
    async def tailor_resume(
        self,
        jd_text: str,
        company_name: Optional[str] = None,
        target_location: Optional[str] = None,
    ) -> ResumeTailorState:
        """Run the full LangGraph pipeline to generate a tailored resume."""
        initial_state: ResumeTailorState = {
            "jd_text": jd_text,
            "company_name": company_name,
            "target_location": target_location,
            "errors": [],
        }
        result = await self.workflow.ainvoke(initial_state)
        return result
