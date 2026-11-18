from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel

from config.settings import settings
from src.core.logger import logger
from src.integrations.apollo_client import LeadContact


class HunterClient:
    """Client for Hunter.io Domain Search and Email Verification APIs."""

    BASE_URL = "https://api.hunter.io/v2"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.HUNTER_API_KEY

    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    async def search_domain(self, domain: str, limit: int = 10) -> List[LeadContact]:
        """Search Hunter.io for executive and IT emails at target domain."""
        if not self.is_configured():
            logger.warning("Hunter API key not configured. Skipping Hunter query.")
            return []

        url = f"{self.BASE_URL}/domain-search"
        params = {
            "domain": domain,
            "api_key": self.api_key,
            "limit": limit,
            "department": "executive,it",
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.get(url, params=params)
                if res.status_code != 200:
                    logger.error(f"Hunter API error {res.status_code}: {res.text}")
                    return []

                data = res.json().get("data", {})
                emails = data.get("emails", [])
                org_name = data.get("organization") or domain.split(".")[0].title()

                contacts: List[LeadContact] = []
                for item in emails:
                    first = item.get("first_name") or ""
                    last = item.get("last_name") or ""
                    position = item.get("position") or "Technical Leader"
                    email = item.get("value")
                    confidence = item.get("confidence", 0)

                    contacts.append(
                        LeadContact(
                            first_name=first,
                            last_name=last,
                            full_name=f"{first} {last}".strip() or "Engineering Leader",
                            title=position,
                            email=email,
                            email_status=f"verified ({confidence}% confidence)" if confidence > 80 else "likely",
                            company_name=org_name,
                            company_domain=domain,
                            location="Dubai / Pune",
                            linkedin_url=item.get("linkedin"),
                            source="hunter.io",
                            notes=f"Found via Hunter.io domain search (confidence: {confidence}%)",
                        )
                    )

                logger.info(f"Hunter returned {len(contacts)} verified emails for {domain}.")
                return contacts

        except Exception as e:
            logger.error(f"Exception during Hunter API search: {e}")
            return []
