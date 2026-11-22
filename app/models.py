from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime


class LeadBase(BaseModel):
    name: str = Field(..., description="Business or brokerage agency name")
    category: str = Field(default="Real Estate Agency", description="Niche or category")
    domain: Optional[str] = Field(None, description="Normalized canonical domain (e.g. 'austinrealty.com')")
    phone: Optional[str] = Field(None, description="Normalized phone number")
    email: Optional[str] = Field(None, description="Direct or info email address")
    address: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = Field(None, description="City")
    state: Optional[str] = Field(None, description="State (e.g. TX, FL, CA)")
    zip_code: Optional[str] = Field(None, description="Postal / Zip code")
    rating: Optional[float] = Field(None, description="Star rating (e.g. 4.9)")
    review_count: Optional[int] = Field(default=0, description="Total verified reviews")
    website: Optional[str] = Field(None, description="Full website URL")
    social_linkedin: Optional[str] = Field(None, description="LinkedIn company or broker profile")
    social_facebook: Optional[str] = Field(None, description="Facebook page")
    source_url: Optional[str] = Field(None, description="Directory or webpage where lead was discovered")


class LeadCreate(LeadBase):
    pass


class LeadRecord(LeadBase):
    id: int
    dedupe_key: str
    is_new: bool = True
    times_seen: int = 1
    first_seen_at: str
    last_seen_at: str
    pushed_to_sheets: bool = False
    pushed_to_slack: bool = False


class ScrapeJobRequest(BaseModel):
    target: str = Field(default="real_estate", description="Target niche / scraper type")
    query: str = Field(default="Commercial Real Estate Agencies", description="Directory search keyword")
    location: str = Field(default="Austin, TX", description="City / state market location")
    limit: Optional[int] = Field(default=None, ge=1, le=500, description="Max agencies to process in batch")
    max_pages: int = Field(default=2, ge=1, le=10, description="Max directory pages to crawl")
    enrich_contacts: bool = Field(default=True, description="Visit agency websites to enrich email and social links")
    target_url: Optional[str] = Field(None, description="Custom directory URL (overrides default or mock)")
    use_mock: bool = Field(default=False, description="Scrape built-in realistic mock directory for instant demos")
    sync_to_sheets: bool = Field(default=True, description="Push clean new leads to Google Sheets")
    notify_slack: bool = Field(default=True, description="Send Block Kit alert card to Slack for new leads")


class ScrapeJobResponse(BaseModel):
    job_id: str
    status: str
    created_at: str
    message: str
    poll_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # pending, running, completed, failed
    query: str
    location: str
    total_found: int = 0
    new_leads_saved: int = 0
    duplicates_filtered: int = 0
    duration_seconds: Optional[float] = None
    leads: List[LeadRecord] = []
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class StatsResponse(BaseModel):
    total_leads_in_db: int
    total_unique_domains: int
    total_new_leads_this_session: int
    total_duplicates_filtered: int
    deduplication_rate_percent: float
    recent_leads: List[LeadRecord]
