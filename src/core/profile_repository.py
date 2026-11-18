import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.settings import settings
from src.core.logger import logger
from src.core.llm_router import LLMRouter


class ProfileRepository:
    """Central repository maintaining candidate profile, screening Q&A bank,

    and intelligent answer resolution for automated job applications.
    """

    FILE_PATH = settings.DATA_DIR / "candidate_profile.json"

    DEFAULT_PROFILE = {
        "personal": {
            "full_name": "Aneeket Deo",
            "email": "aneeketdeowork@gmail.com",
            "phone": "+919922425315",
            "headline": "Serving Notice Period | GenAI Developer",
            "current_location": "Pune, India",
            "target_locations": ["Pune, India", "Dubai, UAE"],
            "years_of_experience": 3.0,
            "relevant_experience_years": 3.0,
            "notice_period": "30 days (Serving notice period)",
            "notice_period_days": 30,
            "serving_notice_period": True,
            "last_working_day": "Immediate / 30 Days",
            "gender": "Male",
            "marital_status": "Single",
            "address": "Pune, Maharashtra, India",
            "pin_code": "411057",
            "country": "India",
            "citizenship": "Indian",
            "valid_passport": True,
        },
        "credentials": {
            "standard_email": os.getenv("STANDARD_PORTAL_EMAIL", "candidate@example.com"),
            "standard_password": os.getenv("STANDARD_PORTAL_PASSWORD", "Candidate@123"),
            "alternate_email": os.getenv("STANDARD_PORTAL_ALT_EMAIL", "candidate@example.com"),
        },
        "urls": {
            "linkedin": "https://www.linkedin.com/in/genai-architect",
            "github": "https://github.com/genai-architect",
            "portfolio": "https://genai-architect.dev",
        },
        "compensation": {
            "currency_india": "INR",
            "current_ctc_lpa": 14.0,
            "expected_ctc_min_lpa": 18.5,
            "expected_ctc_ideal_lpa": 21.5,
            "current_offer_lpa": 14.0,
            "currency_uae": "AED",
            "dubai_expected_aed_monthly": 20000,
            "dubai_min_aed_monthly": 18000,
            "is_negotiable": True,
        },
        "education": {
            "degree": "Bachelor of Engineering (B.E.) / B.Tech in Computer Science",
            "university": "Savitribai Phule Pune University",
            "graduation_year": 2021,
            "gpa_or_percentage": "8.2 CGPA / 82%",
        },
        "experience": {
            "current_company": "Enterprise AI Solutions",
            "current_designation": "GenAI Systems Engineer",
            "experience_years": 3,
            "industry": "Artificial Intelligence / Software Development",
        },
        "skills": [
            "Python", "LangGraph", "AutoGen", "CrewAI", "FastAPI",
            "ChromaDB", "BGE Large", "BM25", "Hybrid Retrieval",
            "Playwright", "Docker", "PostgreSQL", "Redis", "SQL",
            "RAG Pipelines", "Agentic Systems", "Prompt Engineering"
        ],
        "summary": (
            "Production-focused GenAI Developer & Autonomous Agent Architect (3 YOE) "
            "specialized in multi-agent orchestration (LangGraph, AutoGen GroupChatManager, CrewAI) "
            "and high-precision RAG (ChromaDB, BGE Large, BM25 Query Fusion). Proven track record delivering "
            "production impact: 60% manual review reduction via Contract Redlining, 70% IT incident resolution "
            "automation via AgentAssist, 40% network MTTR reduction, and 50% faster decision speed with QueryIQ. "
            "Actively targeting high-impact engineering leadership roles across Pune GCCs and Dubai AI agencies."
        ),
        "qa_bank": {
            "notice_period": "30 days (Currently serving notice period, negotiable for immediate joining)",
            "serving_notice": "Yes, currently serving notice period with 30 days remaining",
            "current_ctc": "14 LPA",
            "expected_ctc": "18.5 - 21.5 LPA",
            "expected_ctc_dubai": "AED 20,000 / month",
            "relocation_pune": "Yes, currently residing in Pune and available for on-site/hybrid work",
            "relocation_dubai": "Yes, fully willing to relocate immediately to Dubai/UAE upon offer",
            "total_experience": "3 years",
            "genai_experience": "3 years in production agentic systems, LangGraph, and RAG",
            "python_experience": "3+ years production Python and asynchronous architectures",
            "holding_offer": "Yes, currently holding an active offer of 14 LPA",
            "reason_for_change": (
                "Seeking high-impact engineering leadership roles in production agentic workflows, "
                "multi-agent orchestration, and large-scale LLM application architectures."
            ),
            "work_authorization_india": "Authorized to work in India (Citizen)",
            "work_authorization_uae": "Require standard employment visa sponsorship for UAE",
            "leadership_experience": (
                "Led architecture of enterprise multi-agent systems and guided cross-functional teams "
                "deploying mission-critical LLM agents to production."
            ),
        },
        "active_resume_filename": "resume_target_organization_20261112_204636.pdf",
        "excluded_companies": [
            "Tata Consultancy Services",
            "TCS",
            "Infosys",
            "Wipro",
            "Cognizant",
            "HCL Technologies",
            "Tech Mahindra"
        ],
    }

    def __init__(self):
        self._ensure_profile_file()

    def _ensure_profile_file(self) -> None:
        """Create or initialize candidate_profile.json if missing."""
        if not self.FILE_PATH.exists():
            # Check if old profile_data.json exists to port extra details
            old_path = settings.DATA_DIR / "profile_data.json"
            profile = dict(self.DEFAULT_PROFILE)
            if old_path.exists():
                try:
                    with open(old_path, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                    if "personal" in old_data:
                        profile["personal"]["phone"] = old_data["personal"].get("phone", profile["personal"]["phone"])
                    if "compensation_targets" in old_data:
                        india_c = old_data["compensation_targets"].get("india", {})
                        if "current_offer" in india_c:
                            profile["compensation"]["current_offer_lpa"] = india_c["current_offer"]
                            profile["compensation"]["current_ctc_lpa"] = india_c["current_offer"]
                        if "target_minimum" in india_c:
                            profile["compensation"]["expected_ctc_min_lpa"] = india_c["target_minimum"]
                        if "target_ideal" in india_c:
                            profile["compensation"]["expected_ctc_ideal_lpa"] = india_c["target_ideal"]
                except Exception as e:
                    logger.warning(f"Could not migrate legacy profile_data.json: {e}")

            self.save(profile)

    def load(self) -> Dict[str, Any]:
        """Load candidate profile from disk."""
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {self.FILE_PATH}: {e}. Returning default profile.")
            return dict(self.DEFAULT_PROFILE)

    def save(self, data: Dict[str, Any]) -> None:
        """Save updated profile to disk."""
        self.FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(self.FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Updated candidate profile repository at {self.FILE_PATH}")

    @staticmethod
    def get_persistent_resume_path() -> Optional[Path]:
        """Return the persistent candidate resume path if it exists."""
        persistent_file = settings.DATA_DIR / "resumes" / "candidate_resume.pdf"
        if persistent_file.exists():
            return persistent_file
        fallback = Path("data/resumes/candidate_resume.pdf")
        if fallback.exists():
            return fallback.resolve()
        return None

    def get_resume_path(self) -> Path:
        """Locate the candidate's active PDF resume."""
        persistent = self.get_persistent_resume_path()
        if persistent and persistent.exists():
            return persistent

        profile = self.load()
        active_name = profile.get("active_resume_filename")
        if active_name:
            p = settings.GENERATED_RESUMES_DIR / active_name
            if p.exists():
                return p

        # Fallback to latest PDF in generated_resumes
        pdf_files = list(settings.GENERATED_RESUMES_DIR.glob("*.pdf"))
        if pdf_files:
            pdf_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            return pdf_files[0]

        raise FileNotFoundError(f"No PDF resume found in {settings.GENERATED_RESUMES_DIR}")

    def get_excluded_companies(self) -> List[str]:
        """Retrieve the list of excluded company names from candidate profile."""
        profile = self.load()
        excluded = profile.get("excluded_companies")
        if excluded is None:
            excluded = profile.get("preferences", {}).get("excluded_companies", [])
        if not excluded:
            excluded = self.DEFAULT_PROFILE.get("excluded_companies", [])
        if isinstance(excluded, str):
            excluded = [c.strip() for c in excluded.split(",") if c.strip()]
        elif isinstance(excluded, list):
            excluded = [str(c).strip() for c in excluded if str(c).strip()]
        else:
            excluded = []
        return excluded

    @staticmethod
    def is_company_excluded(company_name: str, excluded_companies: List[str]) -> bool:
        """Check if a company matches the exclusion list (case-insensitive substring or token match)."""
        if not company_name or not excluded_companies:
            return False
        clean_comp = re.sub(r"[^a-zA-Z0-9\s]", "", company_name).lower().strip()
        comp_tokens = set(clean_comp.split())

        for exc in excluded_companies:
            clean_exc = re.sub(r"[^a-zA-Z0-9\s]", "", str(exc)).lower().strip()
            if not clean_exc:
                continue
            # Direct match or substring
            if clean_exc in clean_comp or clean_comp in clean_exc:
                return True
            # Word token match (e.g. "TCS" in "Tata Consultancy Services (TCS)")
            exc_tokens = set(clean_exc.split())
            common_tokens = exc_tokens.intersection(comp_tokens)
            if common_tokens:
                meaningful = [
                    t for t in common_tokens
                    if len(t) >= 3 and t not in {
                        "pvt", "ltd", "inc", "corp", "services", "solutions",
                        "technologies", "technology", "india", "private", "limited"
                    }
                ]
                if meaningful:
                    return True
        return False

    def get_answer(
        self,
        question: str,
        choices: Optional[List[str]] = None,
        is_dubai: bool = False,
    ) -> str:
        """Determine the optimal answer for an application screening question

        using rule-based Q&A matching, numeric extraction, or LLM fallback.
        """
        profile = self.load()
        personal = profile.get("personal", {})
        comp = profile.get("compensation", {})
        urls = profile.get("urls", {})
        qa = profile.get("qa_bank", {})

        q_lower = question.lower().strip()

        # 1. Direct Pattern Matching
        if any(w in q_lower for w in ["serving notice", "notice period served"]):
            ans = "Yes" if personal.get("serving_notice_period", True) else "No"
            return self._match_or_value(ans, choices)

        if "notice period" in q_lower or "notice" in q_lower or "days required" in q_lower:
            days = str(personal.get("notice_period_days", 30))
            if choices:
                # Prioritize choice containing "30" (candidate notice)
                for c in choices:
                    if "30" in c.lower():
                        return c
                for c in choices:
                    if "month" in c.lower() or "immediate" in c.lower():
                        return c
                return choices[0]
            return f"{days} days"

        # Expected CTC / Compensation (detects expected fixed ctc, expected package, etc.)
        if "expect" in q_lower and any(w in q_lower for w in ["ctc", "salary", "compensation", "fixed", "package"]):
            exp_val = comp.get("expected_ctc_min_lpa", 18.5)
            if is_dubai or "aed" in q_lower:
                aed_val = comp.get("dubai_expected_aed_monthly", 20000)
                if choices:
                    return self._find_best_numeric_choice(aed_val, choices)
                return f"{int(aed_val)} AED"
            if choices:
                return self._find_best_numeric_choice(exp_val, choices)
            return f"{exp_val} LPA"

        if "current ctc" in q_lower or "current salary" in q_lower or "current compensation" in q_lower:
            ctc_val = comp.get("current_ctc_lpa", 14.0)
            if is_dubai:
                return f"{int(comp.get('dubai_min_aed_monthly', 18000))} AED/month"
            if choices:
                return self._find_best_numeric_choice(ctc_val, choices)
            return f"{ctc_val} LPA"

        if "total experience" in q_lower or "overall experience" in q_lower or "years of experience" in q_lower:
            yoe = str(personal.get("years_of_experience", 3))
            if choices:
                return self._find_best_numeric_choice(float(yoe), choices)
            return yoe

        if any(w in q_lower for w in ["python experience", "genai experience", "relevant experience"]):
            yoe = str(personal.get("relevant_experience_years", 3))
            if choices:
                return self._find_best_numeric_choice(float(yoe), choices)
            return yoe

        if any(w in q_lower for w in ["interview", "available to attend", "virtual interview"]):
            return self._match_or_value("Yes", choices)

        if "relocate" in q_lower or "relocation" in q_lower or "willing to work in" in q_lower:
            ans = "Yes"
            return self._match_or_value(ans, choices)

        if "linkedin" in q_lower:
            return urls.get("linkedin", "")

        if "github" in q_lower:
            return urls.get("github", "")

        if "portfolio" in q_lower or "website" in q_lower:
            return urls.get("portfolio", "")

        if "holding any offer" in q_lower or "other offers" in q_lower:
            ans = "Yes"
            return self._match_or_value(ans, choices)

        if "gender" in q_lower:
            return self._match_or_value(personal.get("gender", "Male"), choices)

        if "current location" in q_lower or "current city" in q_lower:
            return self._match_or_value(personal.get("current_location", "Pune, India"), choices)

        if "highest qualification" in q_lower or "degree" in q_lower or "education" in q_lower:
            edu = profile.get("education", {}).get("degree", "B.E. / B.Tech")
            return self._match_or_value(edu, choices)

        # 2. Check QA Bank Keys (with token overlap matching)
        q_tokens = set(re.findall(r"\w+", q_lower))
        for key, val in qa.items():
            key_clean = key.replace("_", " ")
            key_tokens = set(re.findall(r"\w+", key_clean))
            if key_clean in q_lower or key_tokens.issubset(q_tokens) or (len(key_tokens) >= 2 and len(key_tokens.intersection(q_tokens)) >= len(key_tokens) - 1):
                return self._match_or_value(val, choices)

        # 3. Fallback for choices: match Yes/No or first positive option
        if choices:
            for c in choices:
                if c.lower() in ["yes", "immediate", "30 days", "full time", "experienced"]:
                    return c
            return choices[0]

        # 4. Open-ended question fallback
        return (
            "Over 3 years of hands-on experience building production autonomous agent workflows "
            "(LangGraph, AutoGen, CrewAI) and hybrid retrieval RAG systems in Python. "
            "Proven track record cutting legal review latency by 60% and automating 70% of IT incidents."
        )

    def get_answer_details(
        self,
        question: str,
        choices: Optional[List[str]] = None,
        is_dubai: bool = False,
    ) -> Dict[str, Any]:
        """Determine answer and verify whether candidate profile has a confident match.

        Returns:
            {
                "answer": str,
                "is_known": bool,
                "question": question,
                "choices": choices or [],
                "reason": str
            }
        """
        profile = self.load()
        personal = profile.get("personal", {})
        comp = profile.get("compensation", {})
        urls = profile.get("urls", {})
        qa = profile.get("qa_bank", {})
        skills = [s.lower() for s in profile.get("skills", [])]

        q_lower = question.lower().strip()
        q_tokens = set(re.findall(r"\w+", q_lower))

        # Check Notice Period
        if any(w in q_lower for w in ["serving notice", "notice period served"]):
            ans = "Yes" if personal.get("serving_notice_period", True) else "No"
            return {"answer": self._match_or_value(ans, choices), "is_known": True, "reason": "serving_notice"}

        if "notice period" in q_lower or "notice" in q_lower or "days required" in q_lower:
            days = str(personal.get("notice_period_days", 30))
            if choices:
                for c in choices:
                    if "30" in c.lower() or "month" in c.lower() or "immediate" in c.lower():
                        return {"answer": c, "is_known": True, "reason": "notice_period_choice"}
                return {"answer": choices[0], "is_known": True, "reason": "notice_period_default"}
            return {"answer": f"{days} days", "is_known": True, "reason": "notice_period_days"}

        # Expected CTC
        if "expect" in q_lower and any(w in q_lower for w in ["ctc", "salary", "compensation", "fixed", "package"]):
            exp_val = comp.get("expected_ctc_min_lpa", 18.5)
            if is_dubai or "aed" in q_lower:
                aed_val = comp.get("dubai_expected_aed_monthly", 20000)
                ans = self._find_best_numeric_choice(aed_val, choices) if choices else f"{int(aed_val)} AED"
                return {"answer": ans, "is_known": True, "reason": "dubai_expected_ctc"}
            ans = self._find_best_numeric_choice(exp_val, choices) if choices else f"{exp_val} LPA"
            return {"answer": ans, "is_known": True, "reason": "india_expected_ctc"}

        # Current CTC
        if "current ctc" in q_lower or "current salary" in q_lower or "current compensation" in q_lower:
            ctc_val = comp.get("current_ctc_lpa", 14.0)
            ans = self._find_best_numeric_choice(ctc_val, choices) if choices else f"{ctc_val} LPA"
            return {"answer": ans, "is_known": True, "reason": "current_ctc"}

        # Experience
        if "total experience" in q_lower or "overall experience" in q_lower or "years of experience" in q_lower:
            yoe = personal.get("years_of_experience", 3)
            ans = self._find_best_numeric_choice(float(yoe), choices) if choices else str(yoe)
            return {"answer": ans, "is_known": True, "reason": "total_experience"}

        if any(w in q_lower for w in ["python experience", "genai experience", "relevant experience"]):
            yoe = personal.get("relevant_experience_years", 3)
            ans = self._find_best_numeric_choice(float(yoe), choices) if choices else str(yoe)
            return {"answer": ans, "is_known": True, "reason": "relevant_experience"}

        # Interview Availability
        if any(w in q_lower for w in ["interview", "available to attend", "virtual interview"]):
            return {"answer": self._match_or_value("Yes", choices), "is_known": True, "reason": "interview_availability_yes"}

        # Relocation
        if any(w in q_lower for w in ["interview", "available to attend", "virtual interview"]):
            return self._match_or_value("Yes", choices)

        if "relocate" in q_lower or "relocation" in q_lower or "willing to work in" in q_lower:
            # Check if location is in target locations (Pune, Dubai)
            if any(loc in q_lower for loc in ["pune", "dubai", "uae", "mumbai", "hybrid", "remote"]):
                return {"answer": self._match_or_value("Yes", choices), "is_known": True, "reason": "target_relocation_yes"}
            if any(loc in q_lower for loc in ["chennai", "kolkata", "hyderabad"]):
                # Might need user confirmation or Yes if flexible
                return {"answer": self._match_or_value("Yes", choices), "is_known": False, "reason": "non_primary_location"}

        # URLs
        if "linkedin" in q_lower:
            return {"answer": urls.get("linkedin", ""), "is_known": True, "reason": "linkedin_url"}
        if "github" in q_lower:
            return {"answer": urls.get("github", ""), "is_known": True, "reason": "github_url"}
        if "portfolio" in q_lower or "website" in q_lower:
            return {"answer": urls.get("portfolio", ""), "is_known": True, "reason": "portfolio_url"}

        # Standard Demographics
        if "gender" in q_lower:
            return {"answer": self._match_or_value(personal.get("gender", "Male"), choices), "is_known": True, "reason": "gender"}
        if "current location" in q_lower or "current city" in q_lower:
            return {"answer": self._match_or_value(personal.get("current_location", "Pune, India"), choices), "is_known": True, "reason": "current_location"}
        if "highest qualification" in q_lower or "degree" in q_lower or "education" in q_lower:
            return {"answer": self._match_or_value(profile.get("education", {}).get("degree", "B.E. / B.Tech"), choices), "is_known": True, "reason": "degree"}
        if "holding any offer" in q_lower or "other offers" in q_lower:
            return {"answer": self._match_or_value("Yes", choices), "is_known": True, "reason": "holding_offer"}

        # Known QA Bank matching
        for key, val in qa.items():
            key_clean = key.replace("_", " ")
            key_tokens = set(re.findall(r"\w+", key_clean))
            if key_clean in q_lower or key_tokens.issubset(q_tokens) or (len(key_tokens) >= 2 and len(key_tokens.intersection(q_tokens)) >= len(key_tokens) - 1):
                return {"answer": self._match_or_value(val, choices), "is_known": True, "reason": f"qa_bank:{key}"}

        # Check if question asks about a skill candidate has
        for s in skills:
            if s in q_lower:
                return {"answer": self._match_or_value("Yes", choices), "is_known": True, "reason": f"matched_skill:{s}"}

        # If question is completely unknown to profile
        fallback_ans = self.get_answer(question, choices, is_dubai)
        return {
            "answer": fallback_ans,
            "is_known": False,
            "question": question,
            "choices": choices or [],
            "reason": "unknown_question_requires_input"
        }

    def add_qa_answer(self, question: str, answer: str) -> None:
        """Add or update an answer in candidate's qa_bank."""
        profile = self.load()
        qa = profile.setdefault("qa_bank", {})
        slug = re.sub(r"[^a-zA-Z0-9\s]", "", question).lower().strip().replace(" ", "_")
        slug = "_".join(slug.split()[:6]) or "custom_question"
        qa[slug] = answer
        self.save(profile)
        logger.info(f"Added new Q&A mapping '{slug}': '{answer}' to Candidate Profile Repository.")

    def _match_or_value(self, target: str, choices: Optional[List[str]]) -> str:
        if not choices:
            return target
        target_l = target.lower()
        for c in choices:
            c_l = c.lower()
            if target_l in c_l or c_l in target_l:
                return c
        return choices[0]

    def _find_best_numeric_choice(self, val: float, choices: List[str]) -> str:
        """Find the dropdown or radio choice closest to a numerical target."""
        best_choice = choices[0]
        min_diff = float("inf")

        for c in choices:
            nums = re.findall(r"\d+(?:\.\d+)?", c)
            if nums:
                choice_val = float(nums[0])
                diff = abs(choice_val - val)
                if diff < min_diff:
                    min_diff = diff
                    best_choice = c

        return best_choice
