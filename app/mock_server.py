"""
Realistic Mock B2B Directory Server for offline demonstrations and deterministic testing.
Serves structured real estate agency listings with pagination, profile pages, and intentional duplicates.
"""
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

mock_router = APIRouter(prefix="/mock-directory", tags=["Mock Target Directory"])

AGENCIES_DATABASE = [
    # Page 1 listings
    {
        "id": 1,
        "name": "Apex Commercial Realty Partners",
        "category": "Commercial Real Estate Brokerage",
        "phone": "(512) 555-0142",
        "email": "contact@apexcommercialrealty.com",
        "address": "100 Congress Ave, Suite 1200",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "rating": 4.9,
        "reviews": 48,
        "website": "https://www.apexcommercialrealty.com",
        "linkedin": "https://linkedin.com/company/apex-commercial-realty",
        "page": 1
    },
    {
        "id": 2,
        "name": "Lone Star Property Advisors LLC",
        "category": "Industrial & Retail Brokerage",
        "phone": "(512) 555-0188",
        "email": "info@lonestarpropadvisors.com",
        "address": "401 W 15th St, Floor 8",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "rating": 4.7,
        "reviews": 32,
        "website": "https://lonestarpropadvisors.com",
        "linkedin": "https://linkedin.com/company/lone-star-advisors",
        "page": 1
    },
    {
        "id": 3,
        "name": "Silicon Hills Tenant Representation",
        "category": "Office Space Leasing & Acquisition",
        "phone": "(512) 555-0199",
        "email": "brokers@siliconhillstenantrep.com",
        "address": "301 Congress Ave, Suite 400",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "rating": 5.0,
        "reviews": 19,
        "website": "https://siliconhillstenantrep.com",
        "linkedin": "https://linkedin.com/company/silicon-hills-rep",
        "page": 1
    },
    {
        "id": 4,
        "name": "Capitol Metro Land Group",
        "category": "Commercial Land & Development",
        "phone": "(512) 555-0177",
        "email": "deals@capitolmetroland.com",
        "address": "1221 S MoPac Expy, Suite 250",
        "city": "Austin",
        "state": "TX",
        "zip": "78746",
        "rating": 4.6,
        "reviews": 14,
        "website": "https://www.capitolmetroland.com",
        "linkedin": "https://linkedin.com/company/capitol-metro-land",
        "page": 1
    },
    # Page 2 listings (Includes intentional duplicates to verify deduplication)
    {
        "id": 5,
        "name": "Barton Springs Commercial Investments",
        "category": "Multifamily & Capital Markets",
        "phone": "(512) 555-0233",
        "email": "invest@bartonspringscapital.com",
        "address": "1601 S Lamar Blvd",
        "city": "Austin",
        "state": "TX",
        "zip": "78704",
        "rating": 4.8,
        "reviews": 27,
        "website": "https://bartonspringscapital.com",
        "linkedin": "https://linkedin.com/company/barton-springs-capital",
        "page": 2
    },
    {
        "id": 6,
        # INTENTIONAL DUPLICATE OF ID #1 (Apex Commercial Realty) with slightly altered name & parameters
        "name": "Apex Commercial Realty (Downtown Austin Office)",
        "category": "Commercial Real Estate",
        "phone": "512.555.0142",
        "email": "contact@apexcommercialrealty.com",
        "address": "100 Congress Ave",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "rating": 4.9,
        "reviews": 51,
        "website": "https://apexcommercialrealty.com?utm_source=directory&utm_medium=featured",
        "linkedin": "https://linkedin.com/company/apex-commercial-realty",
        "page": 2
    },
    {
        "id": 7,
        "name": "Highland Urban Real Estate Partners",
        "category": "Mixed-Use Property Management & Leasing",
        "phone": "(512) 555-0311",
        "email": "hello@highlandurbanpartners.com",
        "address": "6001 Airport Blvd",
        "city": "Austin",
        "state": "TX",
        "zip": "78752",
        "rating": 4.5,
        "reviews": 38,
        "website": "https://highlandurbanpartners.com",
        "linkedin": "https://linkedin.com/company/highland-urban",
        "page": 2
    },
    {
        "id": 8,
        # INTENTIONAL DUPLICATE OF ID #2 (Lone Star Property Advisors) with different phone format and missing www
        "name": "Lone Star Property Advisors",
        "category": "Retail & Industrial Real Estate",
        "phone": "+1 512 555 0188",
        "email": "info@lonestarpropadvisors.com",
        "address": "401 W 15th Street",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "rating": 4.7,
        "reviews": 32,
        "website": "https://www.lonestarpropadvisors.com/services",
        "linkedin": "https://linkedin.com/company/lone-star-advisors",
        "page": 2
    }
]


@mock_router.get("/search", response_class=HTMLResponse)
async def search_directory(
    q: str = Query(default="Commercial Real Estate"),
    loc: str = Query(default="Austin, TX"),
    page: int = Query(default=1, ge=1)
):
    """Renders a simulated real estate agency search results page."""
    current_page = page
    page_items = [item for item in AGENCIES_DATABASE if item["page"] == current_page]
    total_pages = 2
    has_next = current_page < total_pages

    cards_html = ""
    for item in page_items:
        cards_html += f"""
        <div class="agency-card" data-agency-id="{item['id']}">
            <div class="agency-header">
                <h2 class="agency-title">
                    <a class="agency-link" href="/mock-directory/agency/{item['id']}">{item['name']}</a>
                </h2>
                <span class="agency-category">{item['category']}</span>
            </div>
            <div class="agency-details">
                <p class="agency-phone">📞 <span>{item['phone']}</span></p>
                <p class="agency-address">📍 <span class="street">{item['address']}</span>, <span class="city-state">{item['city']}, {item['state']} {item['zip']}</span></p>
                <div class="rating-box">
                    <span class="rating-score">⭐ {item['rating']}</span>
                    <span class="review-count">({item['reviews']} verified reviews)</span>
                </div>
            </div>
            <div class="agency-actions">
                <a class="agency-website-link" href="{item['website']}" target="_blank" rel="noopener">Visit Website</a>
                <a class="agency-profile-link" href="/mock-directory/agency/{item['id']}">Full Profile & Contacts</a>
            </div>
        </div>
        """

    pagination_html = f"""
    <div class="pagination">
        <span>Page {current_page} of {total_pages}</span>
        {f'<a class="page-next" href="/mock-directory/search?q={q}&loc={loc}&page={current_page + 1}">Next Page &raquo;</a>' if has_next else '<span class="disabled">No more pages</span>'}
    </div>
    """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Commercial Real Estate Agencies in {loc} - Directory</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
            .container {{ max-width: 900px; margin: 0 auto; }}
            .header {{ background: #0f172a; color: white; padding: 24px; border-radius: 12px; margin-bottom: 24px; }}
            .agency-card {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 16px; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
            .agency-title {{ margin: 0 0 6px 0; font-size: 1.25rem; }}
            .agency-title a {{ color: #2563eb; text-decoration: none; }}
            .agency-category {{ display: inline-block; background: #eff6ff; color: #1d4ed8; padding: 4px 8px; border-radius: 4px; font-size: 0.85rem; font-weight: 500; }}
            .agency-details p {{ margin: 6px 0; color: #475569; }}
            .rating-box {{ margin-top: 8px; font-size: 0.9rem; color: #d97706; font-weight: 600; }}
            .agency-actions {{ margin-top: 14px; display: flex; gap: 12px; }}
            .agency-actions a {{ font-size: 0.9rem; text-decoration: none; padding: 6px 12px; border-radius: 6px; }}
            .agency-website-link {{ background: #2563eb; color: white; }}
            .agency-profile-link {{ background: #f1f5f9; color: #334155; }}
            .pagination {{ margin-top: 32px; text-align: center; display: flex; justify-content: center; align-items: center; gap: 16px; }}
            .page-next {{ background: #0f172a; color: white; padding: 8px 16px; border-radius: 6px; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Austin Commercial Real Estate Brokerages</h1>
                <p>Showing newly registered and verified commercial agencies in {loc} (Query: "{q}")</p>
            </div>
            <div class="results-list">
                {cards_html}
            </div>
            {pagination_html}
        </div>
    </body>
    </html>
    """


@mock_router.get("/agency/{agency_id}", response_class=HTMLResponse)
async def agency_profile(agency_id: int):
    """Simulated individual agency webpage with rich contact email and social profiles."""
    agency = next((a for a in AGENCIES_DATABASE if a["id"] == agency_id), None)
    if not agency:
        return HTMLResponse("<h1>Agency Not Found</h1>", status_code=404)

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{agency['name']} - Commercial Brokerage</title>
        <style>
            body {{ font-family: sans-serif; background: #f8fafc; padding: 40px; color: #1e293b; }}
            .profile {{ max-width: 700px; margin: 0 auto; background: white; padding: 32px; border-radius: 12px; border: 1px solid #e2e8f0; }}
            h1 {{ margin-top: 0; color: #0f172a; }}
            .field {{ margin: 12px 0; font-size: 1.05rem; }}
        </style>
    </head>
    <body>
        <div class="profile">
            <h1>{agency['name']}</h1>
            <p><strong>Specialty:</strong> {agency['category']}</p>
            <div class="field">📞 Phone: <span class="phone">{agency['phone']}</span></div>
            <div class="field">✉️ Contact Email: <a class="email" href="mailto:{agency['email']}">{agency['email']}</a></div>
            <div class="field">📍 Headquarters: {agency['address']}, {agency['city']}, {agency['state']} {agency['zip']}</div>
            <div class="field">🌐 Website: <a class="website" href="{agency['website']}">{agency['website']}</a></div>
            <div class="field">💼 LinkedIn: <a class="linkedin" href="{agency['linkedin']}">{agency['linkedin']}</a></div>
            <hr style="margin: 24px 0; border: 0; border-top: 1px solid #e2e8f0;">
            <p>About: Specializing in high-value commercial leases, acquisitions, multifamily investments, and development land across central Texas.</p>
        </div>
    </body>
    </html>
    """
