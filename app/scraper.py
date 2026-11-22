import asyncio
import re
import logging
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, urlparse, quote_plus
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from app.config import settings
from app.models import LeadBase

logger = logging.getLogger("LeadPulse.Scraper")

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(\+?\d{1,2}\s?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})")

# Real-world public commercial real estate directory default
PUBLIC_CRE_DIRECTORY_URL = "https://www.reca.org/corporate--elite-member-companies.html"


class RealEstateScraper:
    """
    High-performance Async Playwright scraper tailored for real estate directories & agencies.
    Features:
    - Route asset interception (drops images, fonts, media for 5x speed)
    - Anti-detection browser flags and realistic User-Agent
    - Resilient multi-tier selector extraction (supports real directories and mock sandbox)
    - Bounded concurrent contact enrichment (asyncio.Semaphore(5) with strict 8s timeout)
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
        use_mock: bool = False,
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

                # Determine start URL
                if target_url:
                    current_url = target_url
                elif use_mock:
                    encoded_q = quote_plus(query)
                    encoded_loc = quote_plus(location)
                    current_url = f"http://127.0.0.1:{settings.PORT}/mock-directory/search?q={encoded_q}&loc={encoded_loc}&page=1"
                else:
                    current_url = PUBLIC_CRE_DIRECTORY_URL

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

                    # Bounded concurrent enrichment with asyncio.Semaphore(5) and strict 8s timeout
                    if enrich_contacts and leads_on_page:
                        enrich_semaphore = asyncio.Semaphore(5)

                        async def _safe_enrich(lead: LeadBase):
                            async with enrich_semaphore:
                                try:
                                    await self._enrich_lead_contact_info(context, lead)
                                except Exception as err:
                                    logger.debug(f"Enrichment exception for {lead.name}: {err}")

                        # Limit enrichment to top leads per page if batch is large
                        enrich_targets = leads_on_page[:15]
                        await asyncio.gather(*[_safe_enrich(lead) for lead in enrich_targets], return_exceptions=True)

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
        """Scrapes agency listing cards from the current DOM with multi-tier fallback."""
        leads: List[LeadBase] = []

        # Tier 1: Structured directory cards (.agency-card, .result-card, .business-card, article.listing)
        cards = await page.query_selector_all(".agency-card, .result-card, .business-card, article.listing")

        if cards:
            for card in cards:
                try:
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

                    category_elem = await card.query_selector(".agency-category, .category, .specialty")
                    category = (await category_elem.inner_text()).strip() if category_elem else "Commercial Real Estate Agency"

                    phone_elem = await card.query_selector(".agency-phone, .phone, [href^='tel:']")
                    phone = ""
                    if phone_elem:
                        phone_text = await phone_elem.inner_text()
                        match = PHONE_REGEX.search(phone_text)
                        phone = match.group(0).strip() if match else phone_text.replace("📞", "").strip()

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

                    website_elem = await card.query_selector(".agency-website-link, a[href*='http']:not([href*='mock-directory']):not(.page-link)")
                    website = ""
                    if website_elem:
                        website = await website_elem.get_attribute("href") or ""

                    leads.append(LeadBase(
                        name=name,
                        category=category,
                        phone=phone or None,
                        address=address or None,
                        city=city,
                        state=state,
                        zip_code=zip_code or None,
                        rating=rating,
                        review_count=review_count,
                        website=website or None,
                        source_url=profile_link or source_url
                    ))
                except Exception as e:
                    logger.error(f"Error parsing card on {source_url}: {e}")

            return leads

        # Tier 2: Real-world public directory listings (e.g. RECA, Association corporate directories)
        links = await page.query_selector_all("p a[href^='http'], li a[href^='http'], div.member-item a")
        skip_hosts = [
            "reca.org", "facebook.com", "twitter.com", "linkedin.com", "instagram.com",
            "memberclicks.com", "google.com", "youtube.com", "associationcareernetwork.com"
        ]

        for l in links:
            try:
                name = (await l.inner_text()).strip()
                href = (await l.get_attribute("href") or "").strip()
                if not name or len(name) < 3 or not href.startswith("http"):
                    continue

                # Filter out generic portal links (e.g., "Career Center", "Login")
                if any(w in name.lower() for w in ["career center", "member login", "privacy policy", "terms of use"]):
                    continue

                parsed = urlparse(href)
                domain = (parsed.hostname or parsed.netloc).lower().replace("www.", "")
                if any(sh in domain for sh in skip_hosts):
                    continue

                # Categorize based on company name
                category = "Commercial Real Estate Brokerage"
                if any(w in name.lower() for w in ["development", "dev"]):
                    category = "Commercial Development & Land"
                elif any(w in name.lower() for w in ["capital", "invest", "trust", "bank"]):
                    category = "Real Estate Capital & Investment"
                elif any(w in name.lower() for w in ["builder", "construction"]):
                    category = "Commercial Construction & Contracting"

                leads.append(LeadBase(
                    name=name,
                    category=category,
                    website=href,
                    city="Austin",
                    state="TX",
                    source_url=href
                ))
            except Exception as e:
                logger.debug(f"Error extracting link on {source_url}: {e}")

        return leads

    async def _enrich_lead_contact_info(self, context: BrowserContext, lead: LeadBase):
        """
        Visits the agency website with a strict 8s timeout to extract:
        - Direct phone number (tel: tags or DOM regex)
        - Contact email (mailto: tags or regex)
        - LinkedIn profile URL
        """
        enrich_target = lead.website or lead.source_url
        if not enrich_target or not enrich_target.startswith("http"):
            return

        enrich_page = None
        try:
            enrich_page = await context.new_page()
            # Strict 8-second timeout per sub-page visit to prevent stalled connections
            await enrich_page.goto(enrich_target, timeout=8000, wait_until="domcontentloaded")

            # 1. Phone extraction from tel: link or text
            if not lead.phone:
                tel_elem = await enrich_page.query_selector("a[href^='tel:']")
                if tel_elem:
                    tel_href = await tel_elem.get_attribute("href") or ""
                    raw_tel = tel_href.replace("tel:", "").strip()
                    if raw_tel:
                        lead.phone = raw_tel

            # 2. Email extraction from mailto: link
            if not lead.email:
                mailto_elem = await enrich_page.query_selector("a[href^='mailto:']")
                if mailto_elem:
                    href = await mailto_elem.get_attribute("href") or ""
                    email = href.replace("mailto:", "").split("?")[0].strip()
                    if email and "@" in email:
                        lead.email = email

            # 3. LinkedIn profile extraction
            if not lead.social_linkedin:
                linkedin_elem = await enrich_page.query_selector("a[href*='linkedin.com/company/'], a[href*='linkedin.com/in/']")
                if linkedin_elem:
                    lead.social_linkedin = await linkedin_elem.get_attribute("href")

            # 4. Search text body if email or phone still missing
            if not lead.email or not lead.phone:
                body_text = await enrich_page.inner_text("body")
                if not lead.email:
                    emails_found = EMAIL_REGEX.findall(body_text)
                    valid_emails = [e for e in emails_found if not any(e.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".css", ".js"])]
                    if valid_emails:
                        lead.email = valid_emails[0]

                if not lead.phone:
                    phone_match = PHONE_REGEX.search(body_text)
                    if phone_match:
                        lead.phone = phone_match.group(0).strip()

        except Exception as e:
            logger.debug(f"Enrichment timed out (8s) or failed for {lead.name} ({enrich_target}): {e}")
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
