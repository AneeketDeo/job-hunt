import asyncio
from pathlib import Path
from typing import Any, Dict, Optional
from playwright.async_api import Page

from config.settings import settings
from src.core.logger import logger


class WorkdayAutoApplyEngine:
    """Specialized Playwright automation engine targeting Workday ATS portals

    via stable data-automation-id DOM attributes.
    """

    # Stable Workday DOM Selectors
    SELECTORS = {
        "resume_upload": "[data-automation-id='file-upload-input']",
        "quick_apply_dropzone": "[data-automation-id='file-upload-drop-zone']",
        "first_name": "[data-automation-id='legalNameSection_firstName']",
        "last_name": "[data-automation-id='legalNameSection_lastName']",
        "email": "[data-automation-id='email']",
        "phone_device_type": "[data-automation-id='phone-device-type']",
        "phone_number": "[data-automation-id='phone-number']",
        "country_phone_code": "[data-automation-id='country-phone-code']",
        "address_line": "[data-automation-id='addressSection_addressLine1']",
        "city": "[data-automation-id='addressSection_city']",
        "postal_code": "[data-automation-id='addressSection_postalCode']",
        "linkedin_url": "[data-automation-id='linkedin-url']",
        "website_url": "[data-automation-id='website-url']",
        "next_button": "[data-automation-id='bottom-navigation-next-button']",
        "submit_button": "[data-automation-id='bottom-navigation-submit-button']",
    }

    def __init__(self, page: Page, candidate_data: Dict[str, Any]):
        self.page = page
        self.candidate = candidate_data
        self.personal = candidate_data.get("personal", {})
        self.comp = candidate_data.get("compensation_targets", {})

    async def fill_candidate_info(self, is_dubai: bool = False) -> bool:
        """Fill standard personal and contact information using stable data-automation-id elements."""
        logger.info("Workday Engine: Populating standard personal identification fields...")

        # 1. Names
        names = self.personal.get("full_name", "GenAI Engineer").split(" ", 1)
        first_name = names[0]
        last_name = names[1] if len(names) > 1 else "Engineer"

        await self._safe_fill(self.SELECTORS["first_name"], first_name)
        await self._safe_fill(self.SELECTORS["last_name"], last_name)

        # 2. Email & Phone
        await self._safe_fill(self.SELECTORS["email"], self.personal.get("email", ""))
        await self._safe_fill(self.SELECTORS["phone_number"], self.personal.get("phone", "+919922425315"))

        # 3. Location / Address
        if is_dubai:
            await self._safe_fill(self.SELECTORS["city"], "Dubai")
        else:
            await self._safe_fill(self.SELECTORS["city"], "Pune")

        # 4. URLs
        await self._safe_fill(self.SELECTORS["linkedin_url"], self.personal.get("linkedin", ""))
        await self._safe_fill(self.SELECTORS["website_url"], self.personal.get("github", ""))

        return True

    async def upload_resume(self, pdf_path: Path) -> bool:
        """Upload the tailored PDF resume to the Workday file upload element."""
        if not pdf_path.exists():
            logger.error(f"Tailored PDF resume not found at: {pdf_path}")
            return False

        logger.info(f"Workday Engine: Uploading resume: {pdf_path.name}")
        try:
            file_input = await self.page.wait_for_selector(
                f"{self.SELECTORS['resume_upload']}, input[type='file']",
                timeout=15000,
            )
            if file_input:
                await file_input.set_input_files(str(pdf_path))
                logger.info("Successfully uploaded tailored PDF to Workday.")
                await asyncio.sleep(3)
                return True
        except Exception as e:
            logger.warning(f"Direct file input not found; attempting dropzone trigger: {e}")

        return False

    async def handle_regional_questionnaire(self, is_dubai: bool = False) -> None:
        """Answer standard Workday questionnaires with Dubai or Pune formatting."""
        logger.info(f"Workday Engine: Handling questionnaire (Dubai Formatter: {is_dubai})...")

        # Salary expectations
        salary_inputs = await self.page.query_selector_all(
            "input[data-automation-id*='salary'], input[name*='salary'], input[placeholder*='salary']"
        )
        for s_input in salary_inputs:
            if is_dubai:
                logger.info("Formatting UAE Salary: 'AED 18,000 – 22,000 / month'")
                await s_input.fill("AED 18,000 – 22,000")
            else:
                logger.info("Formatting India Salary: '18 - 21 LPA'")
                await s_input.fill("18 - 21 LPA")

        # Relocation / Residency Status
        relocation_inputs = await self.page.query_selector_all(
            "textarea[data-automation-id*='relocation'], input[data-automation-id*='relocation'], textarea[name*='relocate']"
        )
        for r_input in relocation_inputs:
            status_text = (
                "Non-Resident / Willing to relocate - Immediately"
                if is_dubai
                else "Based in Pune, available for immediate deployment"
            )
            await r_input.fill(status_text)

        # Notice period
        notice_inputs = await self.page.query_selector_all(
            "input[data-automation-id*='notice'], input[name*='notice'], input[placeholder*='notice']"
        )
        for n_input in notice_inputs:
            await n_input.fill("30 days / Negotiable")

    async def _safe_fill(self, selector: str, value: str) -> bool:
        """Fill an element safely if visible without throwing unhandled exceptions."""
        try:
            el = await self.page.query_selector(selector)
            if el and await el.is_visible():
                await el.fill(value)
                return True
        except Exception as e:
            logger.debug(f"Selector {selector} skipped: {e}")
        return False
