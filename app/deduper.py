import sqlite3
import hashlib
import re
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
from app.config import settings
from app.models import LeadBase, LeadRecord


class LeadDeduper:
    """
    Intelligent SQLite-backed deduplication and enrichment store.
    Uses multi-factor canonical hashing (domain, phone, normalized name).
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(settings.DATA_DIR / "leads.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging for high concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                category TEXT DEFAULT 'Real Estate Agency',
                domain TEXT,
                phone TEXT,
                email TEXT,
                address TEXT,
                city TEXT,
                state TEXT,
                zip_code TEXT,
                rating REAL,
                review_count INTEGER DEFAULT 0,
                website TEXT,
                social_linkedin TEXT,
                social_facebook TEXT,
                source_url TEXT,
                times_seen INTEGER DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                pushed_to_sheets INTEGER DEFAULT 0,
                pushed_to_slack INTEGER DEFAULT 0
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_dedupe ON leads(dedupe_key);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_city_state ON leads(city, state);")
            conn.commit()

    @staticmethod
    def normalize_domain(url_or_domain: Optional[str]) -> Optional[str]:
        """
        Strips scheme, credentials, www, paths, queries, ports, and fragments
        to return the clean canonical host domain.
        """
        if not url_or_domain:
            return None
        raw = url_or_domain.strip().lower()
        if not raw:
            return None
        # Handle protocol-relative or bare domains
        if raw.startswith("//"):
            raw = "https:" + raw
        elif not (raw.startswith("http://") or raw.startswith("https://")):
            raw = "https://" + raw

        try:
            parsed = urlparse(raw)
            # parsed.hostname correctly handles ports, auth, IPv6 brackets
            host = parsed.hostname or parsed.netloc or parsed.path
            if not host:
                return None
            host = host.split(":")[0].strip().lower()
            if host.startswith("www."):
                host = host[4:]
            return host.strip() if ("." in host and len(host) > 3) else None
        except Exception:
            return None

    @staticmethod
    def normalize_phone(phone: Optional[str]) -> Optional[str]:
        """
        Extracts digits, handles office extensions (e.g. ext 102, x44),
        and formats US numbers consistently as (XXX) XXX-XXXX [ext XX].
        """
        if not phone:
            return None
        raw = phone.strip()
        if not raw:
            return None

        # Extract extension if present (e.g. "ext 102", "x12", "#4")
        ext_match = re.search(r"(?:ext|x|#)\.?\s*(\d+)", raw, re.IGNORECASE)
        main_part = raw[:ext_match.start()] if ext_match else raw
        digits = re.sub(r"\D", "", main_part)
        ext_str = f" ext {ext_match.group(1)}" if ext_match else ""

        if len(digits) == 10:
            return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}{ext_str}"
        elif len(digits) == 11 and digits.startswith("1"):
            return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:11]}{ext_str}"
        elif len(digits) >= 7:
            return f"+{digits}{ext_str}"
        return None

    @staticmethod
    def normalize_name(name: str) -> str:
        """Removes corporate noise words & punctuation for normalized key matching."""
        clean = name.lower()
        clean = re.sub(r"[^\w\s]", " ", clean)
        # Remove common corporate suffixes
        suffixes = [r"\bllc\b", r"\binc\b", r"\bcorp\b", r"\bco\b", r"\bltd\b", r"\bgroup\b", r"\brealty\b", r"\brealtors\b"]
        for s in suffixes:
            clean = re.sub(s, "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def generate_dedupe_key(self, lead: LeadBase) -> str:
        """
        Calculates a deterministic multi-factor deduplication key.
        Priority:
        1. Domain (if company website exists)
        2. Normalized Phone + Normalized Name
        3. Normalized Name + City + State
        """
        canonical_domain = self.normalize_domain(lead.website or lead.domain)
        canonical_phone = self.normalize_phone(lead.phone)
        norm_name = self.normalize_name(lead.name)

        if canonical_domain:
            key_raw = f"domain:{canonical_domain}"
        elif canonical_phone and norm_name:
            key_raw = f"phone:{canonical_phone}:{norm_name}"
        else:
            loc = f"{lead.city or ''}_{lead.state or ''}".lower()
            key_raw = f"name_loc:{norm_name}:{loc}"

        return hashlib.sha256(key_raw.encode("utf-8")).hexdigest()

    def check_and_upsert(self, lead: LeadBase) -> Tuple[LeadRecord, bool]:
        """
        Checks if lead already exists.
        If NEW: Inserts lead, returns (LeadRecord, is_new=True).
        If DUPLICATE: Updates times_seen, last_seen_at, enriches missing fields, returns (LeadRecord, is_new=False).
        """
        dedupe_key = self.generate_dedupe_key(lead)
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_domain = self.normalize_domain(lead.website or lead.domain)
        clean_phone = self.normalize_phone(lead.phone)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM leads WHERE dedupe_key = ?", (dedupe_key,))
            existing = cursor.fetchone()

            if existing is None:
                # Insert brand new lead
                cursor.execute("""
                INSERT INTO leads (
                    dedupe_key, name, category, domain, phone, email,
                    address, city, state, zip_code, rating, review_count,
                    website, social_linkedin, social_facebook, source_url,
                    times_seen, first_seen_at, last_seen_at, pushed_to_sheets, pushed_to_slack
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, 0)
                """, (
                    dedupe_key,
                    lead.name.strip(),
                    lead.category,
                    clean_domain,
                    clean_phone,
                    lead.email.strip().lower() if lead.email else None,
                    lead.address.strip() if lead.address else None,
                    lead.city.strip() if lead.city else None,
                    lead.state.strip() if lead.state else None,
                    lead.zip_code.strip() if lead.zip_code else None,
                    lead.rating,
                    lead.review_count or 0,
                    lead.website.strip() if lead.website else None,
                    lead.social_linkedin,
                    lead.social_facebook,
                    lead.source_url,
                    now_iso,
                    now_iso
                ))
                new_id = cursor.lastrowid
                conn.commit()

                record = LeadRecord(
                    id=new_id,
                    dedupe_key=dedupe_key,
                    name=lead.name.strip(),
                    category=lead.category,
                    domain=clean_domain,
                    phone=clean_phone,
                    email=lead.email.strip().lower() if lead.email else None,
                    address=lead.address,
                    city=lead.city,
                    state=lead.state,
                    zip_code=lead.zip_code,
                    rating=lead.rating,
                    review_count=lead.review_count or 0,
                    website=lead.website,
                    social_linkedin=lead.social_linkedin,
                    social_facebook=lead.social_facebook,
                    source_url=lead.source_url,
                    times_seen=1,
                    first_seen_at=now_iso,
                    last_seen_at=now_iso,
                    is_new=True,
                    pushed_to_sheets=False,
                    pushed_to_slack=False
                )
                return record, True
            else:
                # Duplicate lead! Update metrics & enrich any missing fields (e.g. newly discovered email, phone, address, etc.)
                row_dict = dict(existing)
                new_email = (lead.email.strip().lower() if lead.email else None) or row_dict["email"]
                new_phone = clean_phone or row_dict["phone"]
                new_website = (lead.website.strip() if lead.website else None) or row_dict["website"]
                new_domain = clean_domain or row_dict["domain"]
                new_address = (lead.address.strip() if lead.address else None) or row_dict["address"]
                new_city = (lead.city.strip() if lead.city else None) or row_dict["city"]
                new_state = (lead.state.strip() if lead.state else None) or row_dict["state"]
                new_zip = (lead.zip_code.strip() if lead.zip_code else None) or row_dict["zip_code"]
                new_linkedin = lead.social_linkedin or row_dict["social_linkedin"]
                new_facebook = lead.social_facebook or row_dict["social_facebook"]
                new_rating = lead.rating if lead.rating is not None else row_dict["rating"]
                new_reviews = max(lead.review_count or 0, row_dict["review_count"] or 0)

                cursor.execute("""
                UPDATE leads SET
                    times_seen = times_seen + 1,
                    last_seen_at = ?,
                    email = ?,
                    phone = ?,
                    website = ?,
                    domain = ?,
                    address = ?,
                    city = ?,
                    state = ?,
                    zip_code = ?,
                    social_linkedin = ?,
                    social_facebook = ?,
                    rating = ?,
                    review_count = ?
                WHERE id = ?
                """, (
                    now_iso,
                    new_email,
                    new_phone,
                    new_website,
                    new_domain,
                    new_address,
                    new_city,
                    new_state,
                    new_zip,
                    new_linkedin,
                    new_facebook,
                    new_rating,
                    new_reviews,
                    row_dict["id"]
                ))
                conn.commit()

                record = LeadRecord(
                    id=row_dict["id"],
                    dedupe_key=dedupe_key,
                    name=row_dict["name"],
                    category=row_dict["category"],
                    domain=new_domain,
                    phone=new_phone,
                    email=new_email,
                    address=new_address,
                    city=new_city,
                    state=new_state,
                    zip_code=new_zip,
                    rating=new_rating,
                    review_count=new_reviews,
                    website=new_website,
                    social_linkedin=new_linkedin,
                    social_facebook=new_facebook,
                    source_url=row_dict["source_url"],
                    times_seen=row_dict["times_seen"] + 1,
                    first_seen_at=row_dict["first_seen_at"],
                    last_seen_at=now_iso,
                    is_new=False,
                    pushed_to_sheets=bool(row_dict["pushed_to_sheets"]),
                    pushed_to_slack=bool(row_dict["pushed_to_slack"])
                )
                return record, False

    def mark_synced(self, lead_id: int, sheets: bool = False, slack: bool = False):
        with self._get_connection() as conn:
            if sheets:
                conn.execute("UPDATE leads SET pushed_to_sheets = 1 WHERE id = ?", (lead_id,))
            if slack:
                conn.execute("UPDATE leads SET pushed_to_slack = 1 WHERE id = ?", (lead_id,))
            conn.commit()

    def get_all(self, limit: int = 100, offset: int = 0, city: Optional[str] = None, state: Optional[str] = None) -> List[LeadRecord]:
        query = "SELECT * FROM leads WHERE 1=1"
        params: List[Any] = []
        if city:
            query += " AND LOWER(city) LIKE ?"
            params.append(f"%{city.lower()}%")
        if state:
            query += " AND LOWER(state) = ?"
            params.append(state.lower())
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                LeadRecord(
                    id=r["id"],
                    dedupe_key=r["dedupe_key"],
                    name=r["name"],
                    category=r["category"],
                    domain=r["domain"],
                    phone=r["phone"],
                    email=r["email"],
                    address=r["address"],
                    city=r["city"],
                    state=r["state"],
                    zip_code=r["zip_code"],
                    rating=r["rating"],
                    review_count=r["review_count"],
                    website=r["website"],
                    social_linkedin=r["social_linkedin"],
                    social_facebook=r["social_facebook"],
                    source_url=r["source_url"],
                    times_seen=r["times_seen"],
                    first_seen_at=r["first_seen_at"],
                    last_seen_at=r["last_seen_at"],
                    is_new=False,
                    pushed_to_sheets=bool(r["pushed_to_sheets"]),
                    pushed_to_slack=bool(r["pushed_to_slack"])
                )
                for r in rows
            ]

    def get_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            total_records = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            total_domains = conn.execute("SELECT COUNT(DISTINCT domain) FROM leads WHERE domain IS NOT NULL").fetchone()[0]
            total_times_seen = conn.execute("SELECT COALESCE(SUM(times_seen), 0) FROM leads").fetchone()[0]
            duplicates_caught = max(0, total_times_seen - total_records)
            dedupe_rate = (duplicates_caught / total_times_seen * 100) if total_times_seen > 0 else 0.0

            return {
                "total_leads_in_db": total_records,
                "total_unique_domains": total_domains,
                "total_duplicates_filtered": duplicates_caught,
                "deduplication_rate_percent": round(dedupe_rate, 2),
                "total_scrape_events": total_times_seen
            }
