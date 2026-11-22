import asyncio
import re
import logging
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, quote_plus
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from app.config import settings
from app.models import LeadBase

logger = logging.getLogger("LeadPulse.Scraper")

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(\+?\d{1,2}\s?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})")


class RealEstateScraper:
    """
    High-performance Async Playwright scraper tailored for real estate directories & agencies.
    Features:
    - Route interception (drops heavy assets like images & fonts for 5x speed)
    - Anti-detection browser flags and realistic User-Agent
    - Resilient selector extraction (supports both live directories and mock target)
    - Deep contact enrichment (visits agency websites/profiles to extract email & LinkedIn)
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.timeout_ms = settings.PLAYWRIGHT_TIMEOUT_MS

    async def _setup_context(self, browser: Browser) -> BrowserContext:
        """Configures browser context with realistic viewport and stealth headers."""
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/Chicago",
        )

        if settings.BLOCK_ASSETS:
            # Abort heavy assets to drastically speed up crawling and save CPU/bandwidth
            async def intercept_route(route):
                resource_type = route.request.resource_type
                if resource_type in ["image", "media", "font", "stylesheet"]:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", intercept_route)

        return context

    async def scrape(
        self,
        query: str = "Commercial Real Estate Agencies",
        location: str = "Austin, TX",
        max_pages: int = 2,
        target_url: Optional[str] = None,
        enrich_contacts: bool = True
    ) -> List[LeadBase]:
        """
        Executes an asynchronous scraping run across directory pages and extracts structured leads.
        """
        results: List[LeadBase] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"
                ]
            )

            try:
                context = await self._setup_context(browser)
                page = await context.new_page()

                # Determine initial start URL
                if target_url:
                    current_url = target_url
                else:
                    encoded_q = quote_plus(query)
                    encoded_loc = quote_plus(location)
                    # Point to mock directory or real directory search
                    current_url = f"http://127.0.0.1:{settings.PORT}/mock-directory/search?q={encoded_q}&loc={encoded_loc}&page=1"

                for page_num in range(1, max_pages + 1):
                    logger.info(f"Navigating to directory page {page_num}: {current_url}")
                    try:
                        await page.goto(current_url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                    except Exception as e:
                        logger.error(f"Failed to load {current_url}: {e}")
                        break

                    # Extract agency cards on this page
                    leads_on_page = await self._extract_page_leads(page, current_url)
                    logger.info(f"Page {page_num} yielded {len(leads_on_page)} raw agency listings.")

                    # Optionally enrich leads with deep contact info (email, social)
                    if enrich_contacts and leads_on_page:
                        for lead in leads_on_page:
                            await self._enrich_lead_contact_info(context, lead)

                    results.extend(leads_on_page)

                    # Check for Next Page button
                    if page_num < max_pages:
                        next_url = await self._find_next_page_url(page, current_url)
                        if next_url and next_url != current_url:
                            current_url = next_url
                            await asyncio.sleep(settings.SCRAPE_DELAY_MS / 1000.0)
                        else:
                            logger.info("No next page link found; ending crawl.")
                            break

                await context.close()
            finally:
                await browser.close()

        return results

    async def _extract_page_leads(self, page: Page, source_url: str) -> List[LeadBase]:
        """Scrapes all agency listing cards from the current DOM."""
        leads: List[LeadBase] = []

        # Try directory card selectors (supports mock server & standard directory structures)
        cards = await page.query_selector_all(".agency-card, .result-card, .business-card, article.listing")
        
        if not cards:
            logger.warning(f"No agency cards found with standard selectors on {source_url}.")
            return leads

        for card in cards:
            try:
                # Name & Website
                title_elem = await card.query_selector(".agency-title a, .business-name a, h2 a, h3 a, .title")
                name = ""
                profile_link = ""
                if title_elem:
                    name = (await title_elem.inner_text()).strip()
                    profile_link = await title_elem.get_attribute("href") or ""
                    if profile_link and not profile_link.startswith("http"):
                        profile_link = urljoin(page.url, profile_link)
                else:
                    title_elem_raw = await card.query_selector("h2, h3, .name")
                    if title_elem_raw:
                        name = (await title_elem_raw.inner_text()).strip()

                if not name:
                    continue

                # Category
                category_elem = await card.query_selector(".agency-category, .category, .specialty")
                category = (await category_elem.inner_text()).strip() if category_elem else "Commercial Real Estate Agency"

                # Phone
                phone_elem = await card.query_selector(".agency-phone, .phone, [href^='tel:']")
                phone = ""
                if phone_elem:
                    phone_text = await phone_elem.inner_text()
                    match = PHONE_REGEX.search(phone_text)
                    phone = match.group(0).strip() if match else phone_text.replace("📞", "").strip()

                # Address / City / State
                address_elem = await card.query_selector(".agency-address, .address, .street")
                address = (await address_elem.inner_text()).replace("📍", "").strip() if address_elem else ""

                city_state_elem = await card.query_selector(".city-state, .locality")
                city = "Austin"
                state = "TX"
                zip_code = "78701"
                if city_state_elem:
                    cs_text = await city_state_elem.inner_text()
                    parts = cs_text.split(",")
                    if len(parts) >= 2:
                        city = parts[0].strip()
                        state_zip = parts[1].strip().split()
                        state = state_zip[0].strip() if state_zip else ""
                        zip_code = state_zip[1].strip() if len(state_zip) > 1 else ""

                # Rating and Reviews
                rating = None
                review_count = 0
                rating_elem = await card.query_selector(".rating-score, .rating, .stars")
                if rating_elem:
                    r_text = await rating_elem.inner_text()
                    r_match = re.search(r"(\d+(\.\d+)?)", r_text)
                    if r_match:
                        rating = float(r_match.group(1))

                reviews_elem = await card.query_selector(".review-count, .reviews, .count")
                if reviews_elem:
                    rev_text = await reviews_elem.inner_text()
                    rev_match = re.search(r"(\d+)", rev_text)
                    if rev_match:
                        review_count = int(rev_match.group(1))

                # Website
                website_elem = await card.query_selector(".agency-website-link, a[href*='http']:not([href*='mock-directory']):not(.page-link)")
                website = ""
                if website_elem:
                    website = await website_elem.get_attribute("href") or ""

                lead = LeadBase(
                    name=name,
                    category=category,
                    phone=phone,
                    address=address,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    rating=rating,
                    review_count=review_count,
                    website=website or None,
                    source_url=profile_link or source_url
                )
                leads.append(lead)
            except Exception as e:
                logger.error(f"Error parsing card on {source_url}: {e}")

        return leads

    async def _enrich_lead_contact_info(self, context: BrowserContext, lead: LeadBase):
        """
        Visits the agency's website or profile page to discover contact emails & LinkedIn profiles.
        """
        enrich_target = lead.source_url or lead.website
        if not enrich_target or not enrich_target.startswith("http"):
            return

        enrich_page = None
        try:
            enrich_page = await context.new_page()
            # Fast timeout for enrichment visits
            await enrich_page.goto(enrich_target, timeout=12000, wait_until="domcontentloaded")
            
            # 1. Search for mailto: link
            mailto_elem = await enrich_page.query_selector("a[href^='mailto:']")
            if mailto_elem:
                href = await mailto_elem.get_attribute("href") or ""
                email = href.replace("mailto:", "").split("?")[0].strip()
                if email and not lead.email:
                    lead.email = email

            # 2. Search page content for regex email if mailto not found
            if not lead.email:
                body_text = await enrich_page.inner_text("body")
                emails_found = EMAIL_REGEX.findall(body_text)
                valid_emails = [e for e in emails_found if not e.endswith((".png", ".jpg", ".webp", ".svg"))]
                if valid_emails:
                    lead.email = valid_emails[0]

            # 3. Search for LinkedIn URL
            if not lead.social_linkedin:
                linkedin_elem = await enrich_page.query_selector("a[href*='linkedin.com']")
                if linkedin_elem:
                    lead.social_linkedin = await linkedin_elem.get_attribute("href")

            # 4. Search for official website if missing
            if not lead.website:
                web_elem = await enrich_page.query_selector("a.website, a[href^='http']:not([href*='mock-directory'])")
                if web_elem:
                    lead.website = await web_elem.get_attribute("href")

        except Exception as e:
            logger.debug(f"Enrichment timed out or failed for {lead.name}: {e}")
        finally:
            if enrich_page:
                try:
                    await enrich_page.close()
                except Exception:
                    pass

    async def _find_next_page_url(self, page: Page, current_url: str) -> Optional[str]:
        """Detects pagination 'Next' link."""
        next_elem = await page.query_selector(".page-next, a[rel='next'], a:text-matches('Next', 'i')")
        if next_elem:
            href = await next_elem.get_attribute("href")
            if href:
                return urljoin(current_url, href)
        return None
