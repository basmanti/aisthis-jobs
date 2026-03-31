#!/usr/bin/env python3
"""
Aisthis Observer Job Posts — Static Site Generator
===================================================
Reads the Notion Job Posts DB and generates static HTML pages
with Google Jobs structured data (JSON-LD) for each profession × country.

Usage:
    export NOTION_API_KEY="secret_xxx"
    python build.py

Output:
    ./output/
    ├── index.html                          (career landing page)
    ├── nl/electrician/index.html           (per-country job pages)
    ├── de/electrician/index.html
    ├── ...
    ├── sitemap-jobs.xml
    └── logo.png                            (copied if present)
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
DATABASE_ID = "39e18306-969e-4fbe-8fee-35914297270f"
BASE_URL = "https://jobs.aisthis.com"
LOGO_URL = "https://www.aisthis.com/logo.png"
APPLY_URL = "https://www.aisthis.com/quick-apply"
OBSERVER_EMAIL = "observer.jobs@aisthis.com"
OUTPUT_DIR = Path("./output")
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Publish posts with these statuses
# Ready = normal publish, Paused = publish with "paused hiring" banner
PUBLISH_STATUSES = ["Ready", "Paused"]

# Default target countries if field is empty
DEFAULT_COUNTRIES = ["NL", "DE", "FR", "BE", "UK", "US"]

# Country config: code → (full name, addressCountry for JSON-LD, currency, rate multiplier)
COUNTRIES = {
    "NL": {"name": "Netherlands", "address_country": "NL", "currency": "EUR", "multiplier": 1.0, "city": "Amsterdam", "state": "North Holland"},
    "DE": {"name": "Germany",     "address_country": "DE", "currency": "EUR", "multiplier": 1.0, "city": "Berlin", "state": "Berlin"},
    "FR": {"name": "France",      "address_country": "FR", "currency": "EUR", "multiplier": 1.0, "city": "Paris", "state": "Île-de-France"},
    "BE": {"name": "Belgium",     "address_country": "BE", "currency": "EUR", "multiplier": 1.0, "city": "Brussels", "state": "Brussels"},
    "UK": {"name": "United Kingdom", "address_country": "GB", "currency": "EUR", "multiplier": 1.0, "city": "London", "state": "England"},
    "US": {"name": "United States",  "address_country": "US", "currency": "USD", "multiplier": 1.08, "city": "New York", "state": "NY"},
}


# ──────────────────────────────────────────────
# NOTION API
# ──────────────────────────────────────────────

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def notion_query_database(database_id):
    """Query all pages from a Notion database (handles pagination)."""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    pages = []
    payload = {"page_size": 100}

    while True:
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data["results"])
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]

    return pages


def notion_get_blocks(page_id):
    """Get all child blocks of a page (handles pagination)."""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    blocks = []
    params = {"page_size": 100}

    while True:
        resp = requests.get(url, headers=NOTION_HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()
        blocks.extend(data["results"])
        if not data.get("has_more"):
            break
        params["start_cursor"] = data["next_cursor"]

    return blocks


# ──────────────────────────────────────────────
# NOTION → STRUCTURED DATA
# ──────────────────────────────────────────────

def extract_text(rich_text_array: list) -> str:
    """Extract plain text from Notion rich_text array."""
    return "".join(rt.get("plain_text", "") for rt in rich_text_array)


def extract_property(props: dict, name: str, default=""):
    """Extract a property value from Notion page properties."""
    prop = props.get(name)
    if not prop:
        return default

    ptype = prop.get("type", "")

    if ptype == "title":
        return extract_text(prop.get("title", []))
    elif ptype == "rich_text":
        return extract_text(prop.get("rich_text", []))
    elif ptype == "number":
        return prop.get("number") or default
    elif ptype == "select":
        sel = prop.get("select")
        return sel["name"] if sel else default
    elif ptype == "multi_select":
        return [s["name"] for s in prop.get("multi_select", [])]
    elif ptype == "checkbox":
        return prop.get("checkbox", False)
    elif ptype == "unique_id":
        uid = prop.get("unique_id", {})
        prefix = uid.get("prefix", "")
        number = uid.get("number", "")
        return f"{prefix}-{number}" if prefix else str(number)
    elif ptype == "url":
        return prop.get("url") or default

    return default


def parse_job_post(page: dict):
    """Parse a Notion page into a job post dict."""
    props = page.get("properties", {})
    status = extract_property(props, "Status")

    if status not in PUBLISH_STATUSES:
        return None

    slug = extract_property(props, "Slug")
    if not slug:
        # Generate slug from profession if not set
        profession = extract_property(props, "Profession")
        slug = re.sub(r'[^a-z0-9]+', '-', profession.lower()).strip('-')

    hourly_min = extract_property(props, "Hourly Rate Min", 0)
    hourly_max = extract_property(props, "Hourly Rate Max", 0)

    # Get target countries
    target_countries = extract_property(props, "Target Countries", [])
    if not target_countries:
        target_countries = DEFAULT_COUNTRIES

    return {
        "id": page["id"],
        "status": status,
        "title": extract_property(props, "Job Post Title"),
        "profession": extract_property(props, "Profession"),
        "profession_code": extract_property(props, "Profession Code"),
        "slug": slug,
        "cluster": extract_property(props, "Cluster"),
        "pay_tier": extract_property(props, "Pay Tier"),
        "pay_range": extract_property(props, "Pay Range"),
        "hourly_min": float(hourly_min) if hourly_min else 0,
        "hourly_max": float(hourly_max) if hourly_max else 0,
        "eu_shortage": extract_property(props, "EU Shortage", False),
        "post_id": extract_property(props, "Post ID"),
        "target_countries": target_countries,
    }


# ──────────────────────────────────────────────
# BLOCKS → HTML
# ──────────────────────────────────────────────

def rich_text_to_html(rich_text_array: list) -> str:
    """Convert Notion rich_text array to HTML with annotations."""
    parts = []
    for rt in rich_text_array:
        text = rt.get("plain_text", "")
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ann = rt.get("annotations", {})
        if ann.get("bold"):
            text = f"<strong>{text}</strong>"
        if ann.get("italic"):
            text = f"<em>{text}</em>"
        if ann.get("code"):
            text = f"<code>{text}</code>"
        href = rt.get("href")
        if href:
            text = f'<a href="{href}">{text}</a>'
        parts.append(text)
    return "".join(parts)


def blocks_to_html(blocks: list) -> str:
    """Convert Notion blocks to HTML."""
    html_parts = []
    list_open = None  # Track if we're in a list

    for block in blocks:
        btype = block.get("type", "")
        bdata = block.get(btype, {})
        rich_text = bdata.get("rich_text", [])
        content = rich_text_to_html(rich_text)

        # Close list if we're leaving one
        if btype not in ("bulleted_list_item", "numbered_list_item") and list_open:
            html_parts.append(f"</{list_open}>")
            list_open = None

        if btype == "heading_1":
            html_parts.append(f"<h2>{content}</h2>")
        elif btype == "heading_2":
            html_parts.append(f"<h2>{content}</h2>")
        elif btype == "heading_3":
            html_parts.append(f"<h3>{content}</h3>")
        elif btype == "paragraph":
            if content.strip():
                html_parts.append(f"<p>{content}</p>")
        elif btype == "bulleted_list_item":
            if list_open != "ul":
                if list_open:
                    html_parts.append(f"</{list_open}>")
                html_parts.append("<ul>")
                list_open = "ul"
            html_parts.append(f"<li>{content}</li>")
        elif btype == "numbered_list_item":
            if list_open != "ol":
                if list_open:
                    html_parts.append(f"</{list_open}>")
                html_parts.append("<ol>")
                list_open = "ol"
            html_parts.append(f"<li>{content}</li>")
        elif btype == "divider":
            html_parts.append("<hr>")
        elif btype == "quote":
            html_parts.append(f"<blockquote>{content}</blockquote>")

    # Close any open list
    if list_open:
        html_parts.append(f"</{list_open}>")

    return "\n".join(html_parts)


# ──────────────────────────────────────────────
# JSON-LD GENERATION
# ──────────────────────────────────────────────

def generate_jsonld(job: dict, country_code: str, description_html: str) -> str:
    """Generate Google Jobs JSON-LD for a job × country."""
    country = COUNTRIES[country_code]
    multiplier = country["multiplier"]

    # Clean profession name for title (Google penalises marketing hooks)
    clean_title = f"{job['profession']} — Observer Programme"

    jsonld = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": clean_title,
        "description": description_html,
        "identifier": {
            "@type": "PropertyValue",
            "name": "Aisthis",
            "value": job["post_id"],
        },
        "datePosted": TODAY,
        "employmentType": "CONTRACTOR",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Aisthis",
            "sameAs": "https://www.aisthis.com",
            "logo": LOGO_URL,
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressCountry": country["address_country"],
            },
        },
        "directApply": True,
    }

    # Add salary if rates are set
    if job["hourly_min"] > 0 and job["hourly_max"] > 0:
        jsonld["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": country["currency"],
            "value": {
                "@type": "QuantitativeValue",
                "minValue": round(job["hourly_min"] * multiplier, 2),
                "maxValue": round(job["hourly_max"] * multiplier, 2),
                "unitText": "HOUR",
            },
        }

    return json.dumps(jsonld, indent=2, ensure_ascii=False)


# ──────────────────────────────────────────────
# CONTENT FILTERING (strip friction copy)
# ──────────────────────────────────────────────

def _filter_friction_content(html):
    """Remove friction-creating content from job page HTML."""
    # Remove "Dedicated contact person" lines
    html = re.sub(r'<li>[^<]*[Dd]edicated contact person[^<]*</li>\s*', '', html)

    # Remove "biometric wristband if you don't own..." lines
    html = re.sub(r'<li>[^<]*[Bb]iometric wristband[^<]*</li>\s*', '', html)
    html = re.sub(r'<li>[^<]*wristband if you don[^<]*</li>\s*', '', html)

    # Remove single-line mentions of wristband provision (but keep "wear a wristband" instructions)
    html = re.sub(r'<li>[^<]*[Ii]f you don\'t own a compatible[^<]*wristband[^<]*</li>\s*', '', html)

    # Remove "Quick apply — 3 questions" entire section
    # This section starts with an h3 containing "Quick apply" and goes to the end of the content
    html = re.sub(
        r'<h3>[^<]*[Qq]uick [Aa]pply[^<]*</h3>.*',
        '', html, flags=re.DOTALL
    )

    # Remove standalone "dedicated contact person" in comma-separated lists
    html = re.sub(r',?\s*dedicated contact person', '', html, flags=re.IGNORECASE)
    html = re.sub(r'dedicated contact person,?\s*', '', html, flags=re.IGNORECASE)

    # Remove standalone "biometric wristband" in comma-separated lists  
    html = re.sub(r',?\s*biometric wristband', '', html, flags=re.IGNORECASE)
    html = re.sub(r'biometric wristband,?\s*', '', html, flags=re.IGNORECASE)

    # Clean up any double commas or trailing commas from list removals
    html = re.sub(r',\s*,', ',', html)
    html = re.sub(r',\s*</', '</', html)

    # Remove "and a wristband" / "and a biometric wristband" from prose
    html = re.sub(r' and a (?:biometric )?wristband', '', html, flags=re.IGNORECASE)

    return html


# ──────────────────────────────────────────────
# HTML TEMPLATE
# ──────────────────────────────────────────────

def generate_page_html(job: dict, country_code: str, description_html: str) -> str:
    """Generate a complete HTML page for a job × country."""
    country = COUNTRIES[country_code]
    jsonld = generate_jsonld(job, country_code, description_html)

    # Meta description
    meta_desc = (
        f"{job['profession']} — Earn €{job['pay_range']} on top of your salary. "
        f"Record your working day with Aisthis. {country['name']}."
    )

    # EU shortage badge
    eu_badge = ""
    if job.get("eu_shortage") and country_code in ("NL", "DE", "FR", "BE"):
        eu_badge = (
            '<div class="eu-badge">'
            '🇪🇺 This profession is on the European Commission\'s official list of '
            'critical shortage professions.'
            '</div>'
        )

    # Pay display
    multiplier = country["multiplier"]
    currency_symbol = "$" if country["currency"] == "USD" else "€"
    if job["hourly_min"] > 0:
        h_min = round(job["hourly_min"] * multiplier)
        h_max = round(job["hourly_max"] * multiplier)
        pay_display = f"{currency_symbol}{h_min}–{currency_symbol}{h_max} per recorded hour"
        # Monthly estimate (assume 4hrs/day × 20 days)
        m_min = h_min * 4 * 20
        m_max = h_max * 4 * 20
        monthly_display = f"{currency_symbol}{m_min:,}–{currency_symbol}{m_max:,}/month"
    else:
        pay_display = job["pay_range"]
        monthly_display = ""

    # Apply URL with profession + country params
    slug = job["slug"]
    apply_url = f"{APPLY_URL}?profession={slug}&country={country_code}"

    # Email subject line
    email_subject = f"Observer Application — {job['profession']} — {country['name']}"
    email_subject = email_subject.replace(" ", "%20").replace("—", "%E2%80%94")

    # Paused banner
    paused_banner = ""
    if job.get("status") == "Paused":
        paused_banner = (
            '<div class="paused-banner">'
            '<strong>We\'ve paused hiring for this role.</strong> '
            'Leave your details below and we\'ll notify you when it reopens.'
            '</div>'
        )

    # Filter friction content from description
    description_html = _filter_friction_content(description_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{job['profession']} — Observer Programme | Aisthis ({country['name']})</title>
    <meta name="description" content="{meta_desc}">
    <meta property="og:title" content="{job['profession']} — Observer Programme | Aisthis">
    <meta property="og:description" content="{meta_desc}">
    <meta property="og:image" content="{LOGO_URL}">
    <meta property="og:url" content="{BASE_URL}/{country_code.lower()}/{job['slug']}/">
    <meta property="og:type" content="website">
    <link rel="canonical" href="{BASE_URL}/{country_code.lower()}/{job['slug']}/">
    <script type="application/ld+json">
{jsonld}
    </script>
    <style>
        :root {{
            --bg: #f5f5f0;
            --bg-dark: #0a0f0d;
            --surface: #ffffff;
            --border: #e0ddd8;
            --text: #1a1a1a;
            --text-muted: #6b6b6b;
            --text-light: #999;
            --accent: #34d399;
            --accent-dim: rgba(52, 211, 153, 0.1);
            --radius: 12px;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
            -webkit-font-smoothing: antialiased;
        }}
        .header {{
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(245, 245, 240, 0.85);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            padding: 1rem 2rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .logo {{ height: 44px; border-radius: 6px; }}
        .header-nav {{ display: flex; align-items: center; gap: 1.5rem; }}
        .header-nav a {{
            color: var(--text-muted);
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: 500;
        }}
        .header-nav a:hover {{ color: var(--text); }}
        .header-nav .nav-cta {{
            color: var(--text);
            border: 1.5px solid var(--text);
            padding: 0.4rem 1.1rem;
            border-radius: 100px;
            font-weight: 600;
            font-size: 0.85rem;
            transition: background 0.2s, color 0.2s;
        }}
        .header-nav .nav-cta:hover {{ background: var(--text); color: var(--bg); }}
        .container {{
            max-width: 720px;
            margin: 0 auto;
            padding: 3rem 1.5rem 5rem;
        }}
        .breadcrumb {{
            font-size: 0.85rem;
            color: var(--text-light);
            margin-bottom: 2rem;
        }}
        .breadcrumb a {{ color: var(--text-muted); text-decoration: none; }}
        .breadcrumb a:hover {{ color: var(--text); }}
        h1 {{
            font-size: 2.2rem;
            font-weight: 700;
            line-height: 1.15;
            margin-bottom: 0.5rem;
            letter-spacing: -0.02em;
            color: var(--text);
        }}
        .pay-badge {{
            display: inline-block;
            background: var(--accent-dim);
            color: #0d7a52;
            padding: 0.45rem 1.1rem;
            border-radius: 100px;
            font-size: 1rem;
            font-weight: 600;
            margin: 1rem 0 0.25rem;
        }}
        .pay-monthly {{
            font-size: 0.95rem;
            color: var(--text-muted);
            margin-bottom: 1.5rem;
        }}
        .eu-badge {{
            background: #eef3ff;
            border-left: 3px solid #3b82f6;
            color: #1e3a8a;
            padding: 0.75rem 1rem;
            border-radius: 0 var(--radius) var(--radius) 0;
            font-size: 0.9rem;
            margin-bottom: 2rem;
        }}
        .job-content h2 {{
            font-size: 1.35rem;
            font-weight: 700;
            margin: 2.5rem 0 0.75rem;
            color: var(--text);
            letter-spacing: -0.01em;
        }}
        .job-content h3 {{
            font-size: 1.1rem;
            font-weight: 600;
            margin: 2rem 0 0.5rem;
            color: var(--text);
        }}
        .job-content p {{
            margin-bottom: 1rem;
            color: var(--text-muted);
        }}
        .job-content strong {{ color: var(--text); font-weight: 600; }}
        .job-content ul, .job-content ol {{
            margin: 0.5rem 0 1rem 1.5rem;
            color: var(--text-muted);
        }}
        .job-content li {{ margin-bottom: 0.4rem; }}
        .job-content hr {{
            border: none;
            border-top: 1px solid var(--border);
            margin: 2.5rem 0;
        }}
        .job-content blockquote {{
            border-left: 3px solid var(--accent);
            padding-left: 1rem;
            color: var(--text-muted);
            font-style: italic;
        }}
        .apply-cta {{
            margin-top: 3rem;
            text-align: center;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 2.5rem 2rem;
        }}
        .apply-btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            background: var(--text);
            color: var(--bg);
            padding: 0.9rem 2rem;
            border-radius: 100px;
            font-size: 1rem;
            font-weight: 600;
            text-decoration: none;
            transition: opacity 0.2s;
        }}
        .apply-btn:hover {{ opacity: 0.85; }}
        .apply-btn::after {{ content: "\u2192"; }}
        .apply-note {{
            font-size: 0.85rem;
            color: var(--text-light);
            margin-top: 0.75rem;
        }}
        .apply-divider {{
            display: flex;
            align-items: center;
            gap: 1rem;
            margin: 1.25rem 0;
            color: var(--text-light);
            font-size: 0.85rem;
        }}
        .apply-divider::before, .apply-divider::after {{
            content: "";
            flex: 1;
            border-top: 1px solid var(--border);
        }}
        .email-apply {{
            font-size: 0.95rem;
            color: var(--text-muted);
        }}
        .email-apply a {{
            color: var(--text);
            font-weight: 600;
            text-decoration: none;
            border-bottom: 1px solid var(--border);
        }}
        .email-apply a:hover {{ border-color: var(--accent); }}
        .paused-banner {{
            background: #fef3cd;
            border: 1px solid #f0d78c;
            border-radius: var(--radius);
            padding: 1.25rem 1.5rem;
            margin-bottom: 2rem;
            color: #856404;
            font-size: 0.95rem;
        }}
        .paused-banner strong {{ color: #664d03; }}
        .footer {{
            background: var(--bg-dark);
            padding: 3rem 2rem;
            text-align: center;
            font-size: 0.85rem;
            color: #888;
        }}
        .footer a {{ color: #ccc; text-decoration: none; }}
        .footer a:hover {{ color: var(--accent); }}
        .footer-logo {{ font-size: 1.1rem; font-weight: 700; color: #fff; margin-bottom: 0.3rem; }}
        .footer-logo .dot {{ color: var(--accent); }}
        .footer-tagline {{ color: #888; font-size: 0.8rem; margin-bottom: 1.5rem; }}
        .footer-links {{ display: flex; gap: 1.5rem; justify-content: center; flex-wrap: wrap; }}
        .country-tag {{
            display: inline-block;
            background: var(--surface);
            border: 1px solid var(--border);
            padding: 0.2rem 0.7rem;
            border-radius: 100px;
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-bottom: 1rem;
        }}
        @media (max-width: 600px) {{
            .container {{ padding: 2rem 1rem; }}
            h1 {{ font-size: 1.6rem; }}
            .header {{ padding: 0.75rem 1rem; }}
        }}
    </style>
</head>
<body>
    <header class="header">
        <a href="https://www.aisthis.com">
            <img src="{LOGO_URL}" alt="Aisthis" class="logo">
        </a>
        <nav class="header-nav">
            <a href="{BASE_URL}/">Positions</a>
            <a href="https://www.aisthis.com/observers">Observers</a>
            <a href="{APPLY_URL}" class="nav-cta">Apply</a>
        </nav>
    </header>

    <main class="container">
        <div class="breadcrumb">
            <a href="{BASE_URL}/">Jobs</a> &rsaquo;
            <a href="{BASE_URL}/">{country['name']}</a> &rsaquo;
            {job['profession']}
        </div>

        <span class="country-tag">📍 {country['name']}</span>
        <h1>{job['title']}</h1>
        <div class="pay-badge">{pay_display}</div>
        <div class="pay-monthly">{monthly_display} on top of your salary</div>

        {eu_badge}

        {paused_banner}

        <div class="job-content">
            {description_html}
        </div>

        <div class="apply-cta">
            <a href="{apply_url}" class="apply-btn">Apply Now</a>
            <p class="apply-note">Takes ~1 minute. No login required.</p>
            <div class="apply-divider">or</div>
            <p class="email-apply">Send your CV and a short motivation to<br>
            <a href="mailto:{OBSERVER_EMAIL}?subject={email_subject}">{OBSERVER_EMAIL}</a></p>
        </div>
    </main>

    <footer class="footer">
        <div class="footer-logo">aisthis<span class="dot">.</span></div>
        <div class="footer-tagline">Human skills for Embodied AI</div>
        <div class="footer-links">
            <a href="https://www.aisthis.com/ourplan">Our Plan</a>
            <a href="https://www.aisthis.com/observers">Observers</a>
            <a href="https://www.aisthis.com/apprentice">Apprentice</a>
            <a href="https://www.aisthis.com/trust">Privacy</a>
            <a href="https://www.aisthis.com/about">About</a>
        </div>
    </footer>
</body>
</html>"""


# ──────────────────────────────────────────────
# LANDING PAGE
# ──────────────────────────────────────────────

def generate_landing_page(jobs):
    """Generate the /jobs landing page listing all positions."""
    # Group by cluster
    clusters = {}
    for job in jobs:
        cluster = job.get("cluster", "Other")
        clusters.setdefault(cluster, []).append(job)

    job_cards = []
    for cluster_name in sorted(clusters.keys()):
        cluster_jobs = sorted(clusters[cluster_name], key=lambda j: j["profession"])
        job_cards.append(f'<h2 class="cluster-heading">{cluster_name}</h2>')
        job_cards.append('<div class="job-grid">')
        for job in cluster_jobs:
            countries_html = " ".join(
                f'<a href="{BASE_URL}/{c.lower()}/{job["slug"]}/" class="country-link">{c}</a>'
                for c in job["target_countries"]
                if c in COUNTRIES
            )
            job_cards.append(f"""
            <div class="job-card">
                <h3>{job['profession']}</h3>
                <span class="pay-tag">{job['pay_range']}</span>
                <div class="countries">{countries_html}</div>
            </div>""")
        job_cards.append('</div>')

    cards_html = "\n".join(job_cards)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Observer Programme — Open Positions | Aisthis</title>
    <meta name="description" content="Join the Aisthis Observer Programme. Earn extra income by recording your working day. 96 professions across 6 countries.">
    <meta property="og:title" content="Observer Programme — Open Positions | Aisthis">
    <meta property="og:image" content="{LOGO_URL}">
    <link rel="canonical" href="{BASE_URL}/">
    <style>
        :root {{
            --bg: #f5f5f0;
            --bg-dark: #0a0f0d;
            --surface: #ffffff;
            --border: #e0ddd8;
            --text: #1a1a1a;
            --text-muted: #6b6b6b;
            --text-light: #999;
            --accent: #34d399;
            --accent-dim: rgba(52, 211, 153, 0.1);
            --radius: 12px;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }}
        .header {{
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(245, 245, 240, 0.85);
            backdrop-filter: blur(12px);
            padding: 1rem 2rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .logo {{ height: 44px; border-radius: 6px; }}
        .header-nav {{ display: flex; align-items: center; gap: 1.5rem; }}
        .header-nav a {{
            color: var(--text-muted);
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: 500;
        }}
        .header-nav a:hover {{ color: var(--text); }}
        .header-nav .nav-cta {{
            color: var(--text);
            border: 1.5px solid var(--text);
            padding: 0.4rem 1.1rem;
            border-radius: 100px;
            font-weight: 600;
            font-size: 0.85rem;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 3rem 1.5rem 5rem;
        }}
        .hero {{ margin-bottom: 3rem; }}
        .hero h1 {{ font-size: 2.4rem; font-weight: 700; margin-bottom: 0.75rem; letter-spacing: -0.02em; }}
        .hero p {{ color: var(--text-muted); font-size: 1.05rem; max-width: 600px; line-height: 1.7; }}
        .cluster-heading {{
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin: 2.5rem 0 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid var(--border);
        }}
        .job-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1rem;
        }}
        .job-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 1.25rem;
            transition: border-color 0.2s;
        }}
        .job-card:hover {{ border-color: #bbb; }}
        .job-card h3 {{ font-size: 1rem; font-weight: 600; margin-bottom: 0.5rem; }}
        .pay-tag {{
            display: inline-block;
            font-size: 0.85rem;
            color: #0d7a52;
            margin-bottom: 0.75rem;
        }}
        .countries {{ display: flex; gap: 0.4rem; flex-wrap: wrap; }}
        .country-link {{
            display: inline-block;
            background: var(--bg);
            border: 1px solid var(--border);
            padding: 0.15rem 0.5rem;
            border-radius: 100px;
            font-size: 0.75rem;
            color: var(--text-muted);
            text-decoration: none;
        }}
        .country-link:hover {{ border-color: var(--accent); color: #0d7a52; }}
        .footer {{
            background: var(--bg-dark);
            padding: 3rem 2rem;
            text-align: center;
            font-size: 0.85rem;
            color: #888;
        }}
        .footer a {{ color: #ccc; text-decoration: none; }}
        .footer a:hover {{ color: var(--accent); }}
        .footer-logo {{ font-size: 1.1rem; font-weight: 700; color: #fff; margin-bottom: 0.3rem; }}
        .footer-logo .dot {{ color: var(--accent); }}
        .footer-tagline {{ color: #888; font-size: 0.8rem; margin-bottom: 1.5rem; }}
        .footer-links {{ display: flex; gap: 1.5rem; justify-content: center; flex-wrap: wrap; }}
    </style>
</head>
<body>
    <header class="header">
        <a href="https://www.aisthis.com">
            <img src="{LOGO_URL}" alt="Aisthis" class="logo">
        </a>
        <nav class="header-nav">
            <a href="https://www.aisthis.com/observers">Observers</a>
            <a href="https://www.aisthis.com/apprentice">Apprentice</a>
            <a href="{APPLY_URL}" class="nav-cta">Apply</a>
        </nav>
    </header>

    <main class="container">
        <div class="hero">
            <h1>Observer Programme — Open Positions</h1>
            <p>Earn extra income by recording your working day. You wear lightweight smart glasses and a wristband. You do your job — we capture the expertise.</p>
        </div>

        {cards_html}
    </main>

    <footer class="footer">
        <div class="footer-logo">aisthis<span class="dot">.</span></div>
        <div class="footer-tagline">Human skills for Embodied AI</div>
        <div class="footer-links">
            <a href="https://www.aisthis.com/ourplan">Our Plan</a>
            <a href="https://www.aisthis.com/observers">Observers</a>
            <a href="https://www.aisthis.com/apprentice">Apprentice</a>
            <a href="https://www.aisthis.com/trust">Privacy</a>
            <a href="https://www.aisthis.com/about">About</a>
        </div>
    </footer>
</body>
</html>"""


# ──────────────────────────────────────────────
# SITEMAP
# ──────────────────────────────────────────────

def generate_sitemap(urls):
    """Generate sitemap XML."""
    entries = []
    for url in urls:
        entries.append(f"""  <url>
    <loc>{url}</loc>
    <lastmod>{TODAY}</lastmod>
    <changefreq>weekly</changefreq>
  </url>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{BASE_URL}/</loc>
    <lastmod>{TODAY}</lastmod>
    <changefreq>weekly</changefreq>
  </url>
{"".join(entries)}
</urlset>"""


# ──────────────────────────────────────────────
# XML JOB FEEDS (Indeed, Jooble, Adzuna, etc.)
# ──────────────────────────────────────────────

def _cdata(text):
    """Wrap text in CDATA for XML."""
    return f"<![CDATA[{text}]]>"


def _xml_escape(text):
    """Escape text for XML (non-CDATA contexts)."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def _unescape_html(text):
    """Reverse HTML escaping for use inside CDATA blocks.
    
    Indeed requires raw HTML inside CDATA — NOT escaped entities.
    Our blocks_to_html() escapes &, <, > in text content, which is
    correct for browser rendering but breaks Indeed's XML parser.
    """
    return (text
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'"))


def _cluster_to_category(cluster):
    """Map our cluster names to generic job categories."""
    mapping = {
        "A — Construction": "Construction",
        "B — Healthcare": "Healthcare",
        "C — Logistics": "Logistics & Warehousing",
        "D — Manufacturing": "Manufacturing",
        "E — Food & Hospitality": "Food & Hospitality",
        "F — Emergency Services": "Emergency Services",
        "G — Skilled Trades": "Skilled Trades",
        "H — Agriculture": "Agriculture & Environment",
        "I — Education & Care": "Education & Social Care",
        "J — Specialist Niches": "Specialist",
        "K — Hazardous & Specialist": "Hazardous & Specialist",
    }
    return mapping.get(cluster, "Other")


def _experience_years(pay_tier):
    """Estimate minimum experience from pay tier for feed metadata."""
    mapping = {
        "Tier 1 — Standard": "1",
        "Tier 2 — Skilled": "3",
        "Tier 3 — Specialist": "3",
        "Tier 4 — High Scarcity": "3",
        "Tier 5 — Elite": "5",
    }
    return mapping.get(pay_tier, "1")


def generate_indeed_feed(jobs, job_html):
    """Generate Indeed-format XML feed. Also works for SimplyHired/Glassdoor."""
    from email.utils import formatdate
    import time

    build_date = formatdate(time.time(), usegmt=True)
    job_entries = []

    for job in jobs:
        description = job_html.get(job["id"], "")
        # Indeed requires raw HTML inside CDATA — un-escape our HTML-escaped content
        raw_description = _unescape_html(description)
        for cc in job["target_countries"]:
            if cc not in COUNTRIES:
                continue
            country = COUNTRIES[cc]
            m = country["multiplier"]
            cur = country["currency"]
            sym = "$" if cur == "USD" else "EUR"

            salary_str = ""
            if job["hourly_min"] > 0:
                sal_min = round(job["hourly_min"] * m)
                sal_max = round(job["hourly_max"] * m)
                salary_str = f"{sal_min}-{sal_max} {cur} per hour"

            ref = f"{job['post_id']}-{cc}"
            url = f"{BASE_URL}/{cc.lower()}/{job['slug']}/"

            job_entries.append(f"""  <job>
    <title><![CDATA[{job['profession']} — Observer Programme]]></title>
    <date>{TODAY}</date>
    <referencenumber><![CDATA[{ref}]]></referencenumber>
    <url><![CDATA[{url}]]></url>
    <company><![CDATA[Aisthis]]></company>
    <city><![CDATA[{country['city']}]]></city>
    <state><![CDATA[{country['state']}]]></state>
    <country>{country['address_country']}</country>
    <description><![CDATA[{raw_description}]]></description>
    <salary><![CDATA[{salary_str}]]></salary>
    <jobtype><![CDATA[contract]]></jobtype>
    <category><![CDATA[{_cluster_to_category(job['cluster'])}]]></category>
    <experience><![CDATA[{_experience_years(job['pay_tier'])}+ years]]></experience>
  </job>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<source>
  <publisher>Aisthis</publisher>
  <publisherurl>https://www.aisthis.com</publisherurl>
  <lastBuildDate>{build_date}</lastBuildDate>
{"".join(job_entries)}
</source>"""


def generate_jooble_feed(jobs, job_html):
    """Generate Jooble-format XML feed."""
    job_entries = []
    today_jooble = datetime.now(timezone.utc).strftime("%d.%m.%Y")

    for job in jobs:
        description = job_html.get(job["id"], "")
        for cc in job["target_countries"]:
            if cc not in COUNTRIES:
                continue
            country = COUNTRIES[cc]
            m = country["multiplier"]
            cur = country["currency"]

            salary_str = ""
            if job["hourly_min"] > 0:
                sym = "$" if cur == "USD" else "\u20ac"
                sal_min = round(job["hourly_min"] * m)
                sal_max = round(job["hourly_max"] * m)
                salary_str = f"{sym}{sal_min}-{sym}{sal_max}/hour"

            ref = f"{job['post_id']}-{cc}"
            url = f"{BASE_URL}/{cc.lower()}/{job['slug']}/"

            job_entries.append(f"""  <job>
    <link>{_xml_escape(url)}</link>
    <name>{_xml_escape(job['profession'])} — Observer Programme</name>
    <region>{_xml_escape(country['name'])}</region>
    <description>{_cdata(_unescape_html(description))}</description>
    <pubdate>{today_jooble}</pubdate>
    <company>Aisthis</company>
    <salary>{_xml_escape(salary_str)}</salary>
    <jobtype>contract</jobtype>
    <id>{_xml_escape(ref)}</id>
  </job>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<jobs>
{"".join(job_entries)}
</jobs>"""


def generate_adzuna_feed(jobs, job_html):
    """Generate Adzuna/Trovit/Talent.com compatible XML feed."""
    job_entries = []

    for job in jobs:
        description = job_html.get(job["id"], "")
        for cc in job["target_countries"]:
            if cc not in COUNTRIES:
                continue
            country = COUNTRIES[cc]
            m = country["multiplier"]
            cur = country["currency"]

            salary_min = ""
            salary_max = ""
            if job["hourly_min"] > 0:
                salary_min = str(round(job["hourly_min"] * m, 2))
                salary_max = str(round(job["hourly_max"] * m, 2))

            ref = f"{job['post_id']}-{cc}"
            url = f"{BASE_URL}/{cc.lower()}/{job['slug']}/"

            job_entries.append(f"""  <ad>
    <id>{_xml_escape(ref)}</id>
    <url>{_xml_escape(url)}</url>
    <title>{_xml_escape(job['profession'])} — Observer Programme</title>
    <company>Aisthis</company>
    <description>{_cdata(_unescape_html(description))}</description>
    <country>{country['address_country']}</country>
    <category>{_xml_escape(_cluster_to_category(job['cluster']))}</category>
    <contract>Contract</contract>
    <salary_currency>{cur}</salary_currency>
    <salary_min>{salary_min}</salary_min>
    <salary_max>{salary_max}</salary_max>
    <salary_frequency>hourly</salary_frequency>
    <date>{TODAY}</date>
  </ad>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ads>
{"".join(job_entries)}
</ads>"""


# ──────────────────────────────────────────────
# MAIN BUILD
# ──────────────────────────────────────────────

def main():
    if not NOTION_API_KEY:
        print("ERROR: Set NOTION_API_KEY environment variable")
        print("  export NOTION_API_KEY='secret_xxx'")
        sys.exit(1)

    print(f"📡 Querying Notion database {DATABASE_ID}...")
    pages = notion_query_database(DATABASE_ID)
    print(f"   Found {len(pages)} pages")

    # Parse job posts
    jobs = []
    for page in pages:
        job = parse_job_post(page)
        if job:
            jobs.append(job)

    print(f"   {len(jobs)} publishable posts (Ready + Paused)")

    if not jobs:
        print("⚠️  No publishable jobs found. Check PUBLISH_STATUS setting.")
        sys.exit(0)

    # Fetch content for each job
    print(f"\n📄 Fetching content for {len(jobs)} posts...")
    job_html = {}
    for i, job in enumerate(jobs):
        print(f"   [{i+1}/{len(jobs)}] {job['profession']}...")
        blocks = notion_get_blocks(job["id"])
        html = blocks_to_html(blocks)
        job_html[job["id"]] = html

    # Generate pages
    print(f"\n🏗️  Generating HTML pages...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_urls = []
    page_count = 0

    for job in jobs:
        description_html = job_html[job["id"]]
        for country_code in job["target_countries"]:
            if country_code not in COUNTRIES:
                continue

            page_dir = OUTPUT_DIR / country_code.lower() / job["slug"]
            page_dir.mkdir(parents=True, exist_ok=True)

            html = generate_page_html(job, country_code, description_html)
            (page_dir / "index.html").write_text(html, encoding="utf-8")

            url = f"{BASE_URL}/{country_code.lower()}/{job['slug']}/"
            all_urls.append(url)
            page_count += 1

    # Generate landing page
    landing_html = generate_landing_page(jobs)
    (OUTPUT_DIR / "index.html").write_text(landing_html, encoding="utf-8")

    # Generate sitemap
    sitemap_xml = generate_sitemap(all_urls)
    (OUTPUT_DIR / "sitemap-jobs.xml").write_text(sitemap_xml, encoding="utf-8")

    # Generate XML feeds for job boards
    print(f"\n📡 Generating XML job feeds...")
    feeds_dir = OUTPUT_DIR / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)

    # Indeed / SimplyHired / Glassdoor
    indeed_xml = generate_indeed_feed(jobs, job_html)
    (feeds_dir / "indeed.xml").write_text(indeed_xml, encoding="utf-8")

    # Jooble
    jooble_xml = generate_jooble_feed(jobs, job_html)
    (feeds_dir / "jooble.xml").write_text(jooble_xml, encoding="utf-8")

    # Adzuna / Trovit / Talent.com
    adzuna_xml = generate_adzuna_feed(jobs, job_html)
    (feeds_dir / "adzuna.xml").write_text(adzuna_xml, encoding="utf-8")

    print(f"   ✅ feeds/indeed.xml (Indeed, SimplyHired, Glassdoor)")
    print(f"   ✅ feeds/jooble.xml (Jooble — 68 countries)")
    print(f"   ✅ feeds/adzuna.xml (Adzuna, Trovit, Talent.com)")

    # Summary
    feed_job_count = sum(
        len([c for c in j["target_countries"] if c in COUNTRIES])
        for j in jobs
    )

    print(f"\n✅ Build complete!")
    print(f"   {page_count} HTML job pages (Google Jobs via JSON-LD)")
    print(f"   {feed_job_count} job entries across XML feeds")
    print(f"   {len(jobs)} professions × {len(COUNTRIES)} countries")
    print(f"   Landing page: {OUTPUT_DIR}/index.html")
    print(f"   Sitemap: {OUTPUT_DIR}/sitemap-jobs.xml")
    print(f"   Feeds: {feeds_dir}/")
    print(f"\n📂 Output: {OUTPUT_DIR.resolve()}")
    print(f"\n🔗 Feed URLs (once deployed to {BASE_URL}):")
    print(f"   Indeed:  {BASE_URL}/feeds/indeed.xml")
    print(f"   Jooble:  {BASE_URL}/feeds/jooble.xml")
    print(f"   Adzuna:  {BASE_URL}/feeds/adzuna.xml")
    print(f"   Sitemap: {BASE_URL}/sitemap-jobs.xml")


if __name__ == "__main__":
    main()
