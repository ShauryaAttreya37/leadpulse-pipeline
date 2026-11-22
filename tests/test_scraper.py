import pytest
from playwright.async_api import async_playwright
from app.scraper import RealEstateScraper
from app.mock_server import AGENCIES_DATABASE


@pytest.mark.asyncio
async def test_mock_directory_data_integrity():
    assert len(AGENCIES_DATABASE) == 8
    # Page 1 has 4 agencies, Page 2 has 4 agencies (with 2 duplicates)
    p1 = [a for a in AGENCIES_DATABASE if a["page"] == 1]
    p2 = [a for a in AGENCIES_DATABASE if a["page"] == 2]
    assert len(p1) == 4
    assert len(p2) == 4


@pytest.mark.asyncio
async def test_scraper_dom_extraction():
    """Tests Async Playwright selector extraction against a realistic agency card fixture."""
    html_content = """
    <html>
      <body>
        <div class="agency-card">
          <h2 class="agency-title"><a href="https://www.testaustinrealty.com">Test Austin Realty Group</a></h2>
          <span class="agency-category">Commercial Brokerage</span>
          <p class="agency-phone">📞 (512) 555-4321</p>
          <p class="agency-address">📍 500 Congress Ave, Austin, TX 78701</p>
          <span class="city-state">Austin, TX 78701</span>
          <div class="rating-box">
            <span class="rating-score">⭐ 4.8</span>
            <span class="review-count">(35 verified reviews)</span>
          </div>
          <a class="agency-website-link" href="https://www.testaustinrealty.com">Website</a>
        </div>
      </body>
    </html>
    """
    scraper = RealEstateScraper(headless=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_content(html_content)

        leads = await scraper._extract_page_leads(page, "https://test-directory.com")
        assert len(leads) == 1
        lead = leads[0]
        assert lead.name == "Test Austin Realty Group"
        assert lead.category == "Commercial Brokerage"
        assert lead.phone == "(512) 555-4321"
        assert lead.rating == 4.8
        assert lead.review_count == 35
        assert lead.website == "https://www.testaustinrealty.com"
        assert lead.city == "Austin"
        assert lead.state == "TX"

        await context.close()
        await browser.close()

