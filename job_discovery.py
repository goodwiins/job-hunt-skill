#!/usr/bin/env python3
"""
job_discovery.py - discover fresh early-career software / ML / data roles.

USAGE
    python3 -I job_discovery.py --days 3
    python3 -I job_discovery.py --days 3 --profile my_profile.json
    python3 -I job_discovery.py --days 3 --since-file      # re-run: emit only URLs not yet
                                                           # present in any discovery-*-jobright.txt
    python3 -I job_discovery.py --days 5 --out-dir . --log applications-log.md --max-fetch 400
    (HN "Who is hiring" is a monthly thread, so it is scanned over --hn-days, default 31.)

WHAT IT DOES
    1. Pulls four public sources:
         - https://jobs.ypatodkar.com/api/all-jobs.json            (startup ATS feed)
         - SimplifyJobs Summer2027-Internships listings.json       (GitHub, dev branch)
         - SimplifyJobs New-Grad-Positions listings.json           (GitHub, dev branch)
         - latest Hacker News "Ask HN: Who is hiring?" thread      (hn.algolia.com)
    2. Keeps roles posted in the last --days days whose title reads as early-career
       software / ML / data work (new grad, junior, intern, entry, "Engineer I", ...),
       and drops senior/staff/principal/lead/manager, PhD-only, hardware, non-US and
       account-portal ATS roles (Workday, iCIMS, Taleo, UKG, SuccessFactors, Oracle,
       ADP, Eightfold, SmartRecruiters).
    3. For Greenhouse, Ashby, Lever and Workable roles fetches the live posting (description +
       application-form questions) through the public APIs, politely (~3 req/s, retry on
       429/5xx), and rejects postings that require 3+ years, US citizenship / clearance /
       ITAR, or say they will not sponsor a visa.  Postings that 404 or are unlisted are
       dropped, so every URL in the output was open at run time.
    4. De-duplicates within the run (by URL) and against applications-log.md
       (same company + similar title, or same URL).  Roles at a company that is already
       in the log but with a different title are kept and flagged company_in_log=true.
    5. Writes, newest first:
         discovery-<date>.md            human-readable table + per-role notes
         discovery-<date>.json          machine-readable list
         discovery-<date>-jobright.txt  ready-to-paste chat lines for the Jobright.ai agent,
                                        one line per batch of <=10 direct apply URLs, grouped
                                        by ATS.  This file is APPENDED per run (timestamped
                                        section) so --since-file can skip what was emitted.

    Requirements: Python 3.10+, `requests`.  Nothing else.  Read-only against the web.
    It never opens a browser, never submits anything and never edits applications-log.md.

FIT PROFILE (configurable)
    Defaults assume an early-career candidate (about 0-2 years of experience) who is
    open to US relocation and needs employer visa sponsorship (for example F-1 OPT).
    Roles that refuse sponsorship, require US citizenship / clearance, require more than
    2 years of experience, or are PhD-only are filtered out.

    Change any of that with a JSON profile:
        python3 -I job_discovery.py --days 3 --profile my_profile.json
        python3 -I job_discovery.py --print-profile        # show the active settings
    See profile.example.json for every key.  Unlisted keys keep their defaults.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import parse_qs, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs the `requests` package: pip install requests")

# --------------------------------------------------------------------------- constants

FEED_URL = "https://jobs.ypatodkar.com/api/all-jobs.json"
SIMPLIFY_URLS = {
    "simplify-intern": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "simplify-newgrad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
}
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
GH_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}"
ASHBY_API = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
LEVER_API = "https://api.lever.co/v0/postings/{org}/{jid}"
UA = "job-discovery/1.0 (+personal job search script; polite, ~3 req/s)"

ASHBY_QUERY = """
query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title descriptionHtml locationName workplaceType employmentType publishedDate isListed
    applicationForm { sections { title fieldEntries { isRequired field } } }
  }
}
"""

# Candidate profile.  Override with --profile my_profile.json (see profile.example.json).
DEFAULT_PROFILE = {
    "needs_sponsorship": True,             # False: do not reject "we do not sponsor" roles
    "us_citizen_or_clearance_ok": False,   # True: do not reject citizenship / clearance / ITAR roles
    "max_years_required": 2,               # reject postings whose minimum years of experience is higher
    "allow_phd_roles": False,              # True: keep PhD-titled / PhD-only roles
    "us_only": True,                       # False: do not filter by country
    "include_internships": True,           # False: drop intern / co-op roles
    "skip_account_portal_ats": True,       # True: drop Workday, iCIMS, Taleo, ... (login-gated apply flows)
    "title_include_extra": [],             # extra regex fragments that count as "my kind of role"
    "title_exclude_extra": [],             # extra regex fragments that reject a title
    "exclude_companies": [],               # company names to skip (case-insensitive substring)
}
PROFILE: dict = dict(DEFAULT_PROFILE)


def load_profile(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        user = json.load(f)
    unknown = sorted(set(user) - set(DEFAULT_PROFILE) - {"_comment"})
    if unknown:
        sys.exit(f"Unknown profile keys: {unknown}. Valid keys: {sorted(DEFAULT_PROFILE)}")
    for k, v in user.items():
        if k == "_comment":
            continue
        if type(v) is not type(DEFAULT_PROFILE[k]):
            sys.exit(f"Profile key {k!r} must be {type(DEFAULT_PROFILE[k]).__name__}, got {type(v).__name__}")
        PROFILE[k] = v


# Title must look like software / ML / data work ...
DOMAIN_RE = re.compile(
    r"software|engineer|developer|programm|machine.?learning|\bML\b|\bAI\b|artificial intelligence|"
    r"\bdata\b|research|backend|back.end|frontend|front.end|full.?stack|platform|infrastructure|"
    r"devops|\bSRE\b|site reliability|analytics|scientist|\bSDE\b|\bSWE\b|computer vision|\bNLP\b|"
    r"\bLLM\b|deep learning|robotics|cloud|quantitative dev|forward deployed|applied scientist",
    re.I,
)
# ... and read as early-career ...
EARLY_RE = re.compile(
    r"new.?grad|early.?career|entry.?level|\bjunior\b|\bjr\.?\b|\bintern(ship)?\b|co-?op\b|university|"
    r"\bgraduate\b|\bcampus\b|\bassociate\b|apprentice|rotational|\bfellow(ship)?\b|"
    r"(?:engineer|developer|scientist|analyst|sde|swe)\s*(?:I|1)\b(?!I)|\b(?:class|summer|winter|spring|fall) of 20\d\d|"
    r"\b20(?:26|27)\b.*\b(?:grad|start)|\bstudent\b",
    re.I,
)
# ... and must not be any of these.
TITLE_EXCLUDE_RE = re.compile(
    r"\bsenior\b|\bsr\.?\b|\bstaff\b|\bprincipal\b|\blead\b|\bmanager\b|\bdirector\b|\bhead of\b|\bVP\b|"
    r"\bvice president\b|\bchief\b|\bII\b|\bIII\b|\bIV\b|\bV\b(?!ision)|\b[3-9]\b(?!\s*(?:d|month|week))|"
    r"\bPhD\b|\bPh\.D\b|\bdoctoral\b|\bpostdoc|\bMBA\b|\bexecutive\b|\barchitect\b",
    re.I,
)
TITLE_OFFDOMAIN_RE = re.compile(
    r"hardware|mechanical|electrical|\bcivil\b|\bFPGA\b|\bASIC\b|\bRF\b|analog|silicon|\bchip\b|"
    r"manufactur|\bsales\b|marketing|recruit|account (?:executive|manager)|customer success|"
    r"support engineer|technical writer|\bHR\b|finance|accountant|legal|\bnurse\b|clinical|"
    r"process engineer|quality engineer|\bQA\b|test engineer|field engineer|solutions? (?:engineer|architect|consultant)|"
    r"product manager|program manager|project manager|designer|\bUX\b|\bUI designer\b|"
    r"materials|chemical|biolog|thermal|optical|photonic|power electronics|controls engineer|"
    r"supply chain|operations (?:analyst|intern|associate)|business (?:analyst|intern)|consult|"
    r"annotator|annotation|teleoperat|revops|\blinguist|translator|transcri|\brater\b|labeler|"
    r"cell engineering|battery|requirements engineering|\bpolicy\b|automation engineer|industrial|"
    r"\bIT\b (?:intern|support|analyst)|help ?desk|network engineer|systems administrator|\bGIS\b",
    re.I,
)

TITLE_PHD_RE = re.compile(r"\bPhD\b|\bPh\.D\b|\bdoctoral\b|\bpostdoc", re.I)
INTERN_RE = re.compile(r"\bintern(ship)?s?\b|co-?op\b", re.I)


def title_excluded(t: str) -> bool:
    if PROFILE["allow_phd_roles"]:
        t = TITLE_PHD_RE.sub(" ", t)
    if TITLE_EXCLUDE_RE.search(t) or TITLE_OFFDOMAIN_RE.search(t):
        return True
    return any(re.search(x, t, re.I) for x in PROFILE["title_exclude_extra"])


def domain_match(t: str) -> bool:
    return bool(DOMAIN_RE.search(t)) or any(re.search(x, t, re.I) for x in PROFILE["title_include_extra"])


# ATSs that need an account / portal login - the browser agent cannot push through these.
PORTAL_ATS_RE = re.compile(
    r"myworkdayjobs\.com|workday|icims\.com|taleo\.net|ultipro\.com|ukg\.com|successfactors|"
    r"oraclecloud\.com|oracle\.com/.*careers|adp\.com|eightfold\.ai|smartrecruiters\.com|"
    r"workdayjobs|jobs\.jobvite\.com/.*/apply|brassring|kenexa|avature|phenom|paylocity|paycom|dayforce|"
    r"careers\.(?:microsoft|google|amazon|apple)\.com|amazon\.jobs|jobs\.apple\.com",
    re.I,
)

# Location: keep US / remote, drop clearly non-US.
NON_US_RE = re.compile(
    r"\b(?:UK|U\.K\.|United Kingdom|England|London|Scotland|Ireland|Dublin|Canada|Toronto|Vancouver|Montreal|Ottawa|Waterloo|"
    r"India|Bangalore|Bengaluru|Hyderabad|Pune|Chennai|Mumbai|Gurgaon|Gurugram|Noida|Delhi|"
    r"Germany|Berlin|Munich|France|Paris|Netherlands|Amsterdam|Spain|Madrid|Barcelona|Portugal|Lisbon|"
    r"Italy|Milan|Poland|Warsaw|Krakow|Sweden|Stockholm|Denmark|Copenhagen|Norway|Oslo|Finland|Helsinki|"
    r"Switzerland|Zurich|Zürich|Austria|Vienna|Belgium|Brussels|Czech|Prague|Hungary|Budapest|Romania|Bucharest|"
    r"Israel|Tel Aviv|UAE|Dubai|Singapore|Japan|Tokyo|Korea|Seoul|China|Shanghai|Beijing|Shenzhen|Hong Kong|Taiwan|Taipei|"
    r"Australia|Sydney|Melbourne|New Zealand|Auckland|Brazil|São Paulo|Sao Paulo|Mexico|Argentina|Buenos Aires|Colombia|"
    r"Chile|Philippines|Manila|Vietnam|Indonesia|Jakarta|Malaysia|Thailand|Bangkok|Pakistan|Nigeria|Kenya|South Africa|"
    r"Egypt|Turkey|Istanbul|Greece|Athens|Serbia|Ukraine|Estonia|Tallinn|Latvia|Lithuania|Luxembourg|Cyprus|"
    r"EMEA|APAC|LATAM|Europe)\b",
    re.I,
)
US_HINT_RE = re.compile(
    r"\b(?:USA?|U\.S\.A?\.?|United States|Remote|NYC|New York|Bay Area|San Francisco|SF|Seattle|Austin|Boston|Chicago|"
    r"Los Angeles|LA|Denver|Atlanta|Washington|DC|Mountain View|Palo Alto|Menlo Park|Sunnyvale|San Jose|San Mateo|"
    r"Redwood City|Oakland|Santa Clara|Cambridge, MA|Pittsburgh|Philadelphia|Miami|Dallas|Houston|Phoenix|Portland|"
    r"Salt Lake|Minneapolis|Raleigh|Durham|Charlotte|Nashville|Detroit|Ann Arbor|Madison|Boulder|Irvine|San Diego|"
    r"[A-Z]{2}\s*\d{5}|,\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|"
    r"NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b)",
)

STRONG_US_RE = re.compile(
    r"\b(?:USA?|U\.S\.A?\.?|United States|Remote in USA|US-Remote|Remote - US)\b|"
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|"
    r"NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|D\.?C\.?)\b(?!\s*,\s*(?:Canada|Australia))|"
    r",\s*(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
    r"Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|"
    r"Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|"
    r"Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|"
    r"Washington|West Virginia|Wisconsin|Wyoming)\b",
)

# Description screens.
YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|or more|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b[^.\n]{0,60}?"
    r"(?:experience|exp\b|working|industry|professional|in\s+\w+)",
    re.I,
)
CITIZEN_RE = re.compile(
    r"(?:must|required?|only)[^.\n]{0,60}\bU\.?S\.? (?:citizen|person|national)|"
    r"\bU\.?S\.? (?:citizen(?:ship)?|persons?)\b[^.\n]{0,40}\b(?:required|only|is a requirement|mandatory)|"
    r"\b(?:active|current|existing|secret|top.secret|TS/SCI|DoD|government)\b[^.\n]{0,30}\bclearance\b|"
    r"\bsecurity clearance\b[^.\n]{0,40}\b(?:required|is required|must)|\bITAR\b|\bexport control",
    re.I,
)
NO_SPONSOR_RE = re.compile(
    r"without (?:the need for|requiring|need of) (?:visa |employer |immigration )?sponsorship|"
    r"(?:unable|not able|cannot|can't|do(?:es)? not|will not|won't|not in a position|not currently able|not currently|"
    r"unable at this time) to (?:offer |provide |consider )?(?:visa |employment |immigration |work )?(?:sponsor|sponsorship)|"
    r"(?:no|not offer(?:ing)?|not provide|not providing|does not offer|not available for|not eligible for) (?:visa |work |employment |immigration )?sponsorship|"
    r"sponsorship (?:is|will) not (?:be )?(?:available|offered|provided|possible)|"
    r"not (?:currently )?sponsor(?:ing)? (?:work )?visas|cannot sponsor|no sponsorship|"
    r"(?:must|required to) (?:be )?(?:currently )?(?:authorized|eligible) to work in the (?:U\.?S\.?|United States)[^.\n]{0,60}(?:without|not require)",
    re.I,
)
PHD_ONLY_RE = re.compile(
    r"\b(?:PhD|Ph\.D\.?|doctoral)\b[^.\n]{0,40}\b(?:required|candidates? only|is a must|is required)|"
    r"(?:pursuing|enrolled in|obtaining|in the process of obtaining|working towards?|currently has)"
    r"(?:(?!\b(?:BS|MS|BA|B\.S|M\.S|B\.A|Bachelor|Master|undergrad|graduate degree)\b)[^.\n]){0,40}\b(?:PhD|Ph\.D|doctoral)\b"
    r"(?![^.\n]{0,30}\b(?:or|,)\s*(?:BS|MS|B\.S|M\.S|Bachelor|Master)\b)|"
    r"\b(?:PhD|Ph\.D\.?) (?:student|candidate)s?\b[^.\n]{0,30}\b(?:only|required|must)|\bmust be (?:a )?(?:PhD|Ph\.D)",
    re.I,
)
ONSITE_RE = re.compile(r"\b(?:5|five) days? (?:a|per) week\b|fully (?:on-?site|in[- ]office)|100% on-?site", re.I)
GPA_RE = re.compile(r"\bGPA\b", re.I)

# --------------------------------------------------------------------------- helpers

NOW = dt.datetime.now(dt.timezone.utc)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def parse_dt(value) -> dt.datetime | None:
    """Accept ISO strings, epoch seconds / millis, or YYYY-MM-DD."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            if value > 1e12:
                value /= 1000.0
            return dt.datetime.fromtimestamp(value, dt.timezone.utc)
        s = str(value).strip().replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        return None


def age_days(d: dt.datetime | None) -> float:
    return (NOW - d).total_seconds() / 86400.0 if d else 1e9


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    if "&lt;" in s:  # Greenhouse double-escapes
        s = html.unescape(s)
    s = re.sub(r"<(br|/p|/li|/div|/h\d|/tr)\b[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\xa0]+", " ", s).strip()


def norm_company(name: str) -> str:
    n = (name or "").lower()
    n = re.sub(r"\(.*?\)", "", n)
    n = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs|ai|the)\b\.?", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


def norm_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", "", t)
    t = re.sub(r"\b(summer|winter|spring|fall|20\d\d|intern(ship)?|new grad(uate)?|the|a|an|and|of|for|-|–|,)\b", " ", t)
    return " ".join(re.sub(r"[^a-z0-9 ]", "", t).split())


def norm_url(u: str) -> str:
    p = urlparse(u.strip())
    path = re.sub(r"/(apply|application)/?$", "", p.path).rstrip("/")
    q = parse_qs(p.query)
    keep = {k: v for k, v in q.items() if k in ("gh_jid", "token", "for", "jobId", "id")}
    qs = "&".join(f"{k}={v[0]}" for k, v in sorted(keep.items()))
    return f"{p.netloc.lower().removeprefix('www.')}{path.lower()}" + (f"?{qs}" if qs else "")


# --------------------------------------------------------------------------- polite HTTP


class Http:
    """Token-bucket limited session: ~`rps` requests/second, retries on 429/5xx/timeouts."""

    def __init__(self, rps: float = 3.0, retries: int = 4):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json, text/html;q=0.8, */*;q=0.5"})
        self.min_gap = 1.0 / rps
        self.retries = retries
        self.last = 0.0
        self.count = 0

    def _wait(self) -> None:
        gap = time.monotonic() - self.last
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        self.last = time.monotonic()

    def request(self, method: str, url: str, **kw) -> requests.Response | None:
        kw.setdefault("timeout", 30)
        backoff = 1.5
        for attempt in range(self.retries):
            self._wait()
            self.count += 1
            try:
                r = self.s.request(method, url, **kw)
            except requests.RequestException as e:
                log(f"    ! {method} {url[:80]} -> {type(e).__name__}; retry {attempt + 1}/{self.retries}")
                time.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 429 or 500 <= r.status_code < 600:
                ra = r.headers.get("Retry-After")
                sleep = float(ra) if ra and ra.isdigit() else backoff
                log(f"    ! {r.status_code} on {url[:80]}; sleeping {sleep:.1f}s")
                time.sleep(min(sleep, 60))
                backoff *= 2
                continue
            return r
        return None

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)


# --------------------------------------------------------------------------- ATS detection


def detect_ats(url: str) -> tuple[str, dict]:
    """Return (ats_name, ids).  ids holds what the fetchers need."""
    p = urlparse(url)
    host, path, q = p.netloc.lower(), p.path, parse_qs(p.query)
    if "greenhouse.io" in host:
        m = re.search(r"^/(?:embed/job_app)?/?([^/]+)/jobs/(\d+)", path)
        if m:
            return "greenhouse", {"board": m.group(1), "jid": m.group(2)}
        if q.get("for") and q.get("token"):
            return "greenhouse", {"board": q["for"][0], "jid": q["token"][0]}
        m = re.search(r"^/([^/]+)/jobs/(\d+)", path)
        if m:
            return "greenhouse", {"board": m.group(1), "jid": m.group(2)}
    if q.get("gh_jid"):
        return "greenhouse", {"board": None, "jid": q["gh_jid"][0]}
    if "ashbyhq.com" in host:
        m = re.search(r"^/([^/]+)/([0-9a-f-]{36})", path)
        if m:
            return "ashby", {"org": m.group(1), "jid": m.group(2)}
    if "lever.co" in host:
        m = re.search(r"^/([^/]+)/([0-9a-f-]{36})", path)
        if m:
            return "lever", {"org": m.group(1), "jid": m.group(2)}
    if PORTAL_ATS_RE.search(url):
        return "portal", {}
    if "workable.com" in host:
        m = re.search(r"^/([^/]+)/j/([A-Za-z0-9]+)", path)
        return "workable", ({"acct": m.group(1), "code": m.group(2)} if m else {})
    if "rippling.com" in host:
        return "rippling", {}
    if "bamboohr.com" in host:
        return "bamboohr", {}
    if "jobvite.com" in host:
        return "jobvite", {}
    if "workatastartup.com" in host:
        return "workatastartup", {}
    if "linkedin.com" in host:
        return "linkedin", {}
    return "other", {}


# --------------------------------------------------------------------------- sources


def load_feed(http: Http, days: int) -> list[dict]:
    log("[feed] downloading jobs.ypatodkar.com ...")
    r = http.get(FEED_URL, timeout=120)
    if not r or not r.ok:
        log(f"[feed] FAILED ({r.status_code if r else 'no response'})")
        return []
    data = r.json()
    comps = data.get("companies", [])
    out = []
    for j in data.get("jobs", []):
        posted = parse_dt(j.get("posted"))
        if age_days(posted) > days:
            continue
        try:
            company = comps[j["c"]]["n"]
        except Exception:
            company = str(j.get("c"))
        out.append({
            "source": "feed",
            "company": company,
            "title": j.get("title", ""),
            "location": j.get("city") or ("Remote" if j.get("remote") else ""),
            "url": j.get("url", ""),
            "posted": posted,
            "seniority": j.get("seniority"),
            "roles": j.get("roles") or [],
            "career": j.get("career"),
            "remote": bool(j.get("remote")),
            "sponsorship_hint": None,
            "degrees": [],
            "category": None,
        })
    log(f"[feed] {len(out)} roles posted in last {days} days")
    return out


def load_simplify(http: Http, days: int) -> list[dict]:
    out = []
    for name, url in SIMPLIFY_URLS.items():
        if name.endswith("intern") and not PROFILE["include_internships"]:
            continue
        log(f"[{name}] downloading ...")
        r = http.get(url, timeout=120)
        if not r or not r.ok:
            log(f"[{name}] FAILED ({r.status_code if r else 'no response'})")
            continue
        n = 0
        for x in r.json():
            if not (x.get("active") and x.get("is_visible", True)):
                continue
            posted = parse_dt(x.get("date_posted"))
            if age_days(posted) > days:
                continue
            n += 1
            out.append({
                "source": name,
                "company": x.get("company_name", ""),
                "title": x.get("title", ""),
                "location": "; ".join(x.get("locations") or []),
                "url": x.get("url", ""),
                "posted": posted,
                "seniority": "intern" if name.endswith("intern") else "junior",
                "roles": [],
                "career": "early",
                "remote": any("remote" in (l or "").lower() for l in x.get("locations") or []),
                "sponsorship_hint": x.get("sponsorship"),
                "degrees": x.get("degrees") or [],
                "category": x.get("category"),
                "terms": x.get("terms") or [],
            })
        log(f"[{name}] {n} active roles posted in last {days} days")
    return out


HN_HEADER_RE = re.compile(r"^\s*([^|]{2,60})\|([^|]{2,120})\|([^|]{0,80})")


def load_hn(http: Http, days: int) -> list[dict]:
    log("[hn] locating latest 'Who is hiring?' thread ...")
    r = http.get(HN_SEARCH, params={"query": "Ask HN: Who is hiring?", "tags": "story,author_whoishiring", "hitsPerPage": 3})
    if not r or not r.ok:
        log("[hn] FAILED to find thread")
        return []
    hits = [h for h in r.json().get("hits", []) if "who is hiring" in (h.get("title") or "").lower()]
    if not hits:
        return []
    story = hits[0]
    sid = story["objectID"]
    log(f"[hn] thread {sid}: {story.get('title')}")
    since = int(time.time()) - days * 86400
    out, page = [], 0
    while True:
        r = http.get(HN_SEARCH, params={"tags": f"comment,story_{sid}", "hitsPerPage": 1000,
                                        "numericFilters": f"created_at_i>{since}", "page": page})
        if not r or not r.ok:
            break
        j = r.json()
        for c in j.get("hits", []):
            if str(c.get("parent_id")) != str(sid):
                continue  # replies, not job posts
            text = strip_html(c.get("comment_text") or "")
            first = text.split("\n", 1)[0]
            m = HN_HEADER_RE.match(first)
            company, title, location = (m.group(1).strip(), m.group(2).strip(), m.group(3).strip()) if m else (first[:60], first[:120], "")
            urls = re.findall(r"https?://[^\s<>\"')\]]+", html.unescape(c.get("comment_text") or ""))
            urls = [u for u in urls if "news.ycombinator.com" not in u and "ycombinator.com/item" not in u]
            out.append({
                "source": "hn",
                "company": company,
                "title": title,
                "location": location,
                "url": urls[0] if urls else f"https://news.ycombinator.com/item?id={c['objectID']}",
                "hn_url": f"https://news.ycombinator.com/item?id={c['objectID']}",
                "posted": parse_dt(c.get("created_at")),
                "seniority": None,
                "roles": [],
                "career": None,
                "remote": bool(re.search(r"\bremote\b", text, re.I)),
                "sponsorship_hint": None,
                "degrees": [],
                "category": None,
                "hn_text": text,
            })
        page += 1
        if page >= j.get("nbPages", 1):
            break
    log(f"[hn] {len(out)} top-level posts in last {days} days")
    return out


# --------------------------------------------------------------------------- title / location screen


def title_screen(job: dict) -> tuple[bool, str]:
    t = job["title"]
    if any(c.lower() in (job.get("company") or "").lower() for c in PROFILE["exclude_companies"] if c):
        return False, "company excluded by profile"
    if TITLE_EXCLUDE_RE.search(TITLE_PHD_RE.sub(" ", t) if PROFILE["allow_phd_roles"] else t):
        return False, "title: senior/lead/PhD/level>=3"
    if title_excluded(t):
        return False, "title: off-domain"
    if not PROFILE["include_internships"] and (INTERN_RE.search(t) or job.get("seniority") == "intern"):
        return False, "title: internship (profile excludes)"
    src = job["source"]
    if src == "feed":
        if not domain_match(t) and not (set(job.get("roles") or []) & {"swe", "ai", "data", "backend", "frontend", "fullstack", "infra", "ml"}):
            return False, "title: not software/ML/data"
        early = job.get("seniority") in ("junior", "intern") or bool(EARLY_RE.search(t))
        if not early:
            return False, "title: not early-career"
        return True, f"feed seniority={job.get('seniority')}, title matches early-career"
    if src.startswith("simplify"):
        cat = (job.get("category") or "")
        if not domain_match(t):
            return False, "title: not software/ML/data"
        degs = [d.lower() for d in job.get("degrees") or []]
        if degs and all("phd" in d for d in degs) and not PROFILE["allow_phd_roles"]:
            return False, "PhD-only listing"
        hint = job.get("sponsorship_hint") or ""
        if hint == "Does Not Offer Sponsorship" and PROFILE["needs_sponsorship"]:
            return False, f"simplify flag: {hint}"
        if hint == "U.S. Citizenship is Required" and not PROFILE["us_citizen_or_clearance_ok"]:
            return False, f"simplify flag: {hint}"
        return True, f"{src} list ({cat or 'n/a'}), sponsorship={hint or 'n/a'}"
    if src == "hn":
        blob = job["title"] + " " + job.get("hn_text", "")[:600]
        if not domain_match(job["title"]):
            return False, "title: not software/ML/data"
        if not EARLY_RE.search(blob):
            return False, "hn: not early-career"
        return True, "hn post mentions early-career"
    return True, "unknown source"


def location_screen(job: dict) -> tuple[bool, str]:
    """Evaluate each listed location; keep if any one is US/remote, drop if all are clearly non-US."""
    loc = job.get("location") or ""
    if not PROFILE["us_only"]:
        return True, "any location (profile)"
    if not loc:
        return True, "remote" if job.get("remote") else "location unknown"
    segs = [x.strip() for x in re.split(r"[;|/]| or |\n", loc) if x.strip()] or [loc]
    us, non_us, unclear = [], [], []
    for seg in segs:
        if STRONG_US_RE.search(seg):
            us.append(seg)
        elif NON_US_RE.search(seg):
            non_us.append(seg)
        elif US_HINT_RE.search(seg) or re.search(r"\bremote\b", seg, re.I):
            us.append(seg)
        else:
            unclear.append(seg)
    if us:
        return True, "US: " + "; ".join(us)[:60]
    if non_us and not unclear:
        return False, f"location non-US: {loc[:60]}"
    if job.get("remote") and not non_us:
        return True, "remote"
    if non_us:
        return False, f"location non-US: {loc[:60]}"
    return True, f"location unclear: {loc[:40]}"


# --------------------------------------------------------------------------- live posting fetchers

GH_BOARD_IN_HTML = re.compile(r"greenhouse\.io/(?:embed/job_(?:board|app)\?for=|v1/boards/|)([A-Za-z0-9_-]+)")


def greenhouse_board_guess(http: Http, job: dict, ids: dict) -> str | None:
    """gh_jid links on company sites don't name the board; guess from the company, else scrape the page."""
    cands = []
    n = norm_company(job["company"])
    raw = re.sub(r"[^a-z0-9]", "", job["company"].lower())
    for c in (raw, n, raw.replace("ai", ""), n + "ai"):
        if c and c not in cands:
            cands.append(c)
    for board in cands[:3]:
        r = http.get(GH_API.format(board=board, jid=ids["jid"]))
        if r is not None and r.ok:
            return board
    r = http.get(job["url"], timeout=20)
    if r is not None and r.ok:
        boards = [b for b in GH_BOARD_IN_HTML.findall(r.text) if b not in ("embed", "v1", "boards")]
        if boards:
            return Counter(boards).most_common(1)[0][0]
    return None


def fetch_greenhouse(http: Http, job: dict, ids: dict) -> dict | None:
    board = ids.get("board") or greenhouse_board_guess(http, job, ids)
    if not board:
        return {"error": "board slug not found"}
    r = http.get(GH_API.format(board=board, jid=ids["jid"]), params={"questions": "true"})
    if r is None:
        return {"error": "no response"}
    if r.status_code == 404:
        return {"closed": True}
    if not r.ok:
        return {"error": f"HTTP {r.status_code}"}
    j = r.json()
    req = [q.get("label", "").strip() for q in j.get("questions", []) if q.get("required")]
    req += [q.get("label", "").strip() for q in j.get("location_questions", []) if q.get("required")]
    if j.get("demographic_questions"):
        req.append("[EEO/demographic section present]")
    return {
        "title": j.get("title") or job["title"],
        "location": (j.get("location") or {}).get("name") or job["location"],
        "text": strip_html(j.get("content")),
        "posted": parse_dt(j.get("first_published")) or job["posted"],
        "required_questions": req,
        "apply_url": f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={ids['jid']}",
        "canonical_url": j.get("absolute_url") or job["url"],
    }


def fetch_ashby(http: Http, job: dict, ids: dict) -> dict | None:
    body = {"operationName": "ApiJobPosting", "query": ASHBY_QUERY,
            "variables": {"organizationHostedJobsPageName": ids["org"], "jobPostingId": ids["jid"]}}
    r = http.post(ASHBY_API, json=body, headers={"Content-Type": "application/json"})
    if r is None:
        return {"error": "no response"}
    if not r.ok:
        return {"error": f"HTTP {r.status_code}"}
    try:
        jp = (r.json().get("data") or {}).get("jobPosting")
    except ValueError:
        return {"error": "bad JSON"}
    if not jp or jp.get("isListed") is False:
        return {"closed": True}
    req = []
    for sec in (jp.get("applicationForm") or {}).get("sections", []) or []:
        for fe in sec.get("fieldEntries", []) or []:
            if fe.get("isRequired"):
                f = fe.get("field") or {}
                req.append(str(f.get("title") or f.get("path") or "?").strip())
    return {
        "title": jp.get("title") or job["title"],
        "location": jp.get("locationName") or job["location"],
        "workplace": jp.get("workplaceType"),
        "employment": jp.get("employmentType"),
        "text": strip_html(jp.get("descriptionHtml")),
        "posted": parse_dt(jp.get("publishedDate")) or job["posted"],
        "required_questions": req,
        "apply_url": f"https://jobs.ashbyhq.com/{ids['org']}/{ids['jid']}/application",
        "canonical_url": f"https://jobs.ashbyhq.com/{ids['org']}/{ids['jid']}",
    }


def fetch_lever(http: Http, job: dict, ids: dict) -> dict | None:
    r = http.get(LEVER_API.format(org=ids["org"], jid=ids["jid"]))
    if r is None:
        return {"error": "no response"}
    if r.status_code == 404:
        return {"closed": True}
    if not r.ok:
        return {"error": f"HTTP {r.status_code}"}
    j = r.json()
    cats = j.get("categories") or {}
    text = " ".join(filter(None, [j.get("descriptionPlain"), " ".join(
        f"{l.get('text','')}: {strip_html(l.get('content'))}" for l in j.get("lists") or []), j.get("additionalPlain")]))
    return {
        "title": j.get("text") or job["title"],
        "location": cats.get("location") or job["location"],
        "employment": cats.get("commitment"),
        "text": text,
        "posted": parse_dt(j.get("createdAt")) or job["posted"],
        "required_questions": [],  # Lever's public postings API does not expose the form
        "apply_url": j.get("applyUrl") or f"https://jobs.lever.co/{ids['org']}/{ids['jid']}/apply",
        "canonical_url": j.get("hostedUrl") or job["url"],
    }


WORKABLE_API = "https://apply.workable.com/api/v2/accounts/{acct}/jobs/{code}"


def fetch_workable(http: Http, job: dict, ids: dict) -> dict | None:
    if not ids:
        return fetch_generic(http, job)
    r = http.get(WORKABLE_API.format(**ids))
    if r is None:
        return {"error": "no response"}
    if r.status_code == 404:
        return {"closed": True}
    if not r.ok:
        return {"error": f"HTTP {r.status_code}"}
    j = r.json()
    if j.get("state") and j["state"] not in ("published", "active"):
        return {"closed": True}
    loc = j.get("location") or {}
    loc_s = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")])) or job["location"]
    if j.get("remote") and "remote" not in loc_s.lower():
        loc_s = f"Remote - {loc_s}" if loc_s else "Remote"
    text = " ".join(strip_html(j.get(k)) for k in ("description", "requirements", "benefits"))
    return {
        "title": j.get("title") or job["title"],
        "location": loc_s,
        "workplace": j.get("workplace"),
        "text": text,
        "posted": parse_dt(j.get("published")) or job["posted"],
        "required_questions": [],  # Workable's public API does not expose the form
        "apply_url": f"https://apply.workable.com/{ids['acct']}/j/{ids['code']}/apply",
        "canonical_url": f"https://apply.workable.com/{ids['acct']}/j/{ids['code']}/",
    }


def fetch_generic(http: Http, job: dict) -> dict | None:
    """No API: confirm the page is reachable and screen whatever text is visible."""
    try:
        r = http.get(job["url"], timeout=20, allow_redirects=True)
    except Exception:
        return {"error": "fetch failed"}
    if r is None:
        return {"error": "no response"}
    if r.status_code in (404, 410):
        return {"closed": True}
    if not r.ok:
        return {"error": f"HTTP {r.status_code} (page kept, unverified)"}
    ld = extract_jobposting_ld(r.text)
    if ld:
        text = strip_html(ld.get("description") or "")
        title = ld.get("title") or job["title"]
        loc = ld_location(ld) or job["location"]
        posted = parse_dt(ld.get("datePosted")) or job["posted"]
        return {"text": text, "title": title, "location": loc, "posted": posted, "ld": True,
                "required_questions": [], "apply_url": job["url"], "canonical_url": r.url}
    text = strip_html(re.sub(r"<(script|style)\b.*?</\1>", " ", r.text, flags=re.I | re.S))
    if re.search(r"(?:job|position|posting|role) (?:is )?(?:no longer|not) (?:available|accepting|active|open)|has been filled|job closed|this job has expired", text[:20000], re.I):
        return {"closed": True}
    info = {"text": text[:40000], "required_questions": [], "apply_url": job["url"], "canonical_url": r.url}
    if len(text) < 800:
        info["error"] = "page text thin (JS-rendered?); screen unreliable"
    return info


def extract_jobposting_ld(page: str) -> dict | None:
    """Many career sites (Meta, Salesforce, Workable...) embed a schema.org JobPosting block."""
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        try:
            data = json.loads(html.unescape(m.group(1)).strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                if str(d.get("@type", "")).lower() == "jobposting":
                    return d
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
            elif isinstance(d, list):
                stack.extend(d)
    return None


def ld_location(ld: dict) -> str:
    locs = ld.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    parts = []
    for l in locs:
        a = (l or {}).get("address") or {}
        if isinstance(a, str):
            parts.append(a)
        else:
            parts.append(", ".join(filter(None, [a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry") if isinstance(a.get("addressCountry"), str) else None])))
    return "; ".join(p for p in parts if p)


def description_screen(text: str) -> tuple[bool, str, list[str]]:
    """Return (ok, reason, notes)."""
    notes: list[str] = []
    if not text:
        return True, "no description available (title-only screen)", notes
    m = NO_SPONSOR_RE.search(text) if PROFILE["needs_sponsorship"] else None
    if m:
        return False, f"no sponsorship: \"{text[max(0, m.start()-40):m.end()+20].strip()}\"", notes
    m = CITIZEN_RE.search(text) if not PROFILE["us_citizen_or_clearance_ok"] else None
    if m:
        return False, f"citizenship/clearance: \"{text[max(0, m.start()-20):m.end()+10].strip()}\"", notes
    if PHD_ONLY_RE.search(text) and not PROFILE["allow_phd_roles"]:
        return False, "PhD required", notes
    min_years = None
    for m in YEARS_RE.finditer(text):
        y = int(m.group(1))
        if 0 < y <= 15:
            min_years = y if min_years is None else min(min_years, y)
    max_y = PROFILE["max_years_required"]
    if min_years is not None and min_years > max_y:
        return False, f"requires {min_years}+ years", notes
    if min_years is not None and min_years == max_y and max_y > 0:
        notes.append(f"mentions {min_years}+ years (borderline)")
    if ONSITE_RE.search(text):
        notes.append("fully on-site")
    if GPA_RE.search(text):
        notes.append("mentions GPA")
    parts = ["description screened"]
    if min_years is not None:
        parts.append(f"min years mentioned={min_years}")
    return True, ", ".join(parts), notes


# --------------------------------------------------------------------------- de-dup against the log


def load_log(path: str) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    companies, pairs, urls = set(), set(), set()
    if not os.path.exists(path):
        log(f"[log] {path} not found; skipping de-dup against the log")
        return companies, pairs, urls
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            for u in re.findall(r"https?://[^\s|)>\]]+", line):
                urls.add(norm_url(u))
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3 or cells[0].lower() in ("date", "---") or set(cells[0]) <= {"-", ":"}:
                continue
            comp, title = cells[1], cells[2]
            if comp and comp != "–":
                companies.add(norm_company(comp))
                pairs.add((norm_company(comp), norm_title(title)))
    log(f"[log] {len(companies)} companies, {len(pairs)} company+title pairs, {len(urls)} URLs in {path}")
    return companies, pairs, urls


def load_since(out_dir: str) -> set[str]:
    seen = set()
    for p in glob.glob(os.path.join(out_dir, "discovery-*-jobright.txt")):
        with open(p, encoding="utf-8") as f:
            for u in re.findall(r"https?://[^\s,]+", f.read()):
                seen.add(norm_url(u))
    log(f"[since] {len(seen)} URLs already emitted in earlier discovery-*-jobright.txt files")
    return seen


# --------------------------------------------------------------------------- output


def write_outputs(roles: list[dict], out_dir: str, date: str, since_mode: bool, stats: dict, args) -> None:
    md_path = os.path.join(out_dir, f"discovery-{date}.md")
    json_path = os.path.join(out_dir, f"discovery-{date}.json")
    jr_path = os.path.join(out_dir, f"discovery-{date}-jobright.txt")

    def ser(r):
        o = dict(r)
        o["posted"] = r["posted"].isoformat() if r.get("posted") else None
        o["live_posted"] = r["live_posted"].isoformat() if r.get("live_posted") else None
        return o

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"generated": NOW.isoformat(), "days": args.days, "count": len(roles), "stats": stats,
                   "roles": [ser(r) for r in roles]}, f, indent=2, ensure_ascii=False)

    lines = [f"# Job discovery {date}", "",
             f"Generated {NOW.strftime('%Y-%m-%d %H:%M UTC')} - roles posted in the last {args.days} days, "
             f"early-career software/ML/data, screened for sponsorship / citizenship / years, de-duped against "
             f"`{os.path.basename(args.log)}`. {len(roles)} roles. Newest first.", "",
             "Funnel: " + ", ".join(f"{k}={v}" for k, v in stats.items()), "",
             "| # | Posted | Company | Title | Location | ATS | Apply | New | Pass reason |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(roles, 1):
        posted = r["posted"].strftime("%Y-%m-%d") if r.get("posted") else "?"
        flag = ("new" if r.get("new", True) else "seen") + (" / co. in log" if r.get("company_in_log") else "")
        lines.append(f"| {i} | {posted} | {r['company']} | {r['title']} | {r['location'] or '-'} | {r['ats']} | "
                     f"[apply]({r['apply_url']}) | {flag} | {r['pass_reason']} |")
    lines += ["", "## Details", ""]
    for i, r in enumerate(roles, 1):
        lines.append(f"### {i}. {r['company']} - {r['title']}")
        lines.append(f"- Posting: {r['url']}")
        if r["apply_url"] != r["url"]:
            lines.append(f"- Apply: {r['apply_url']}")
        lines.append(f"- Source: {r['source']} | ATS: {r['ats']} | Posted: {r['posted'].isoformat() if r.get('posted') else '?'}")
        if r.get("notes"):
            lines.append(f"- Notes: {'; '.join(r['notes'])}")
        if r.get("required_questions"):
            lines.append(f"- Required questions ({len(r['required_questions'])}): " + "; ".join(r["required_questions"]))
        lines.append("")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Jobright paste file: APPEND a timestamped section, batches of <=10 per ATS, newest first.
    new_roles = [r for r in roles if r.get("new", True)
                 and not (r["source"] == "hn" and r["ats"] == "other")
                 and "news.ycombinator.com" not in r["apply_url"]]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in new_roles:
        groups[r["ats"]].append(r)
    order = ["greenhouse", "ashby", "lever"] + sorted(k for k in groups if k not in ("greenhouse", "ashby", "lever"))
    block = [f"# run {NOW.strftime('%Y-%m-%d %H:%M UTC')} - {len(new_roles)} new pasteable roles (HN posts without a direct ATS link are only in the .md/.json)"
             + (" (since-file mode: only URLs not emitted before)" if since_mode else "")]
    for ats in order:
        rs = groups.get(ats) or []
        if not rs:
            continue
        block.append(f"## {ats} ({len(rs)})")
        for k in range(0, len(rs), 10):
            batch = rs[k:k + 10]
            block.append("Add these jobs to my application list: " + ", ".join(r["apply_url"] for r in batch))
    block.append("")
    with open(jr_path, "a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")
    log(f"[out] wrote {md_path}\n[out] wrote {json_path}\n[out] appended {jr_path} ({len(new_roles)} new URLs)")


# --------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("WHAT IT DOES")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=3, help="keep roles posted within this many days (default 3)")
    ap.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)), help="where discovery-* files go")
    ap.add_argument("--log", default=None, help="applications log markdown (default: <out-dir>/applications-log.md)")
    ap.add_argument("--since-file", action="store_true",
                    help="skip URLs already present in earlier discovery-*-jobright.txt files (re-run mode)")
    ap.add_argument("--hn-days", type=int, default=31,
                    help="window for the HN thread (default 31: the thread is monthly and posts cluster in its first days)")
    ap.add_argument("--max-fetch", type=int, default=600, help="cap on live posting fetches (safety)")
    ap.add_argument("--rps", type=float, default=3.0, help="requests per second (default 3)")
    ap.add_argument("--no-generic-fetch", action="store_true", help="skip reachability check for non-API ATSs")
    ap.add_argument("--skip-seen-companies", action="store_true",
                    help="also drop every company already present in the log (default keeps them, flagged)")
    ap.add_argument("--profile", help="JSON file overriding the candidate profile (see profile.example.json)")
    ap.add_argument("--print-profile", action="store_true", help="print the active profile and exit")
    args = ap.parse_args()
    if args.profile:
        load_profile(args.profile)
    if args.print_profile:
        print(json.dumps(PROFILE, indent=2))
        return 0
    args.log = args.log or os.path.join(args.out_dir, "applications-log.md")
    date = NOW.astimezone().strftime("%Y-%m-%d")
    http = Http(rps=args.rps)
    stats: dict[str, int] = {}

    # 1. gather
    jobs = load_feed(http, args.days) + load_simplify(http, args.days) + load_hn(http, max(args.days, args.hn_days))
    stats["pulled"] = len(jobs)

    # 2. title / location / ATS screen + in-run de-dup
    log_companies, log_pairs, log_urls = load_log(args.log)
    since = load_since(args.out_dir) if args.since_file else set()
    seen_urls: set[str] = set()
    cands: list[dict] = []
    rejects: Counter = Counter()
    for j in jobs:
        if not j.get("url"):
            rejects["no url"] += 1
            continue
        ok, why = title_screen(j)
        if not ok:
            rejects[why] += 1
            continue
        ok, lwhy = location_screen(j)
        if not ok:
            rejects["location non-US"] += 1
            continue
        ats, ids = detect_ats(j["url"])
        if ats == "portal":
            if PROFILE["skip_account_portal_ats"]:
                rejects["portal ATS"] += 1
                continue
            ats = "other"
        nu = norm_url(j["url"])
        if nu in seen_urls:
            rejects["dup in run"] += 1
            continue
        seen_urls.add(nu)
        if nu in log_urls or (norm_company(j["company"]), norm_title(j["title"])) in log_pairs:
            rejects["already in log"] += 1
            continue
        if args.skip_seen_companies and norm_company(j["company"]) in log_companies:
            rejects["company in log"] += 1
            continue
        j.update(ats=ats, ids=ids, title_reason=why, loc_reason=lwhy,
                 company_in_log=norm_company(j["company"]) in log_companies)
        cands.append(j)
    stats["after_title_screen"] = len(cands)
    log(f"[screen] {len(cands)} candidates after title/location/ATS/log screen; rejects: {dict(rejects)}")

    # 3. live fetch + description screen
    cands.sort(key=lambda j: j["posted"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    survivors: list[dict] = []
    fetched = 0
    drej: Counter = Counter()
    for j in cands:
        info: dict | None
        if fetched >= args.max_fetch:
            info = {"text": "", "required_questions": [], "apply_url": j["url"], "canonical_url": j["url"], "error": "fetch cap"}
        elif j["ats"] == "greenhouse":
            fetched += 1
            info = fetch_greenhouse(http, j, j["ids"])
        elif j["ats"] == "ashby":
            fetched += 1
            info = fetch_ashby(http, j, j["ids"])
        elif j["ats"] == "lever":
            fetched += 1
            info = fetch_lever(http, j, j["ids"])
        elif j["ats"] == "workable":
            fetched += 1
            info = fetch_workable(http, j, j["ids"])
        elif args.no_generic_fetch:
            info = {"text": "", "required_questions": [], "apply_url": j["url"], "canonical_url": j["url"]}
        else:
            fetched += 1
            info = fetch_generic(http, j) or {}
        if info.get("closed"):
            drej["closed/unlisted"] += 1
            continue
        if info.get("error") and "text" not in info:
            if j["ats"] in ("greenhouse", "ashby", "lever", "workable"):
                drej["api error"] += 1
                log(f"    ? {j['company']} / {j['title'][:50]}: {info['error']}")
                continue
            info.setdefault("text", "")
        text = info.get("text", "")
        for sq in info.get("required_questions") or []:
            text += "\n" + sq  # screen question labels too ("Are you a US citizen?" etc.)
        title_live = info.get("title") or j["title"]
        if title_live != j["title"] and (title_excluded(title_live) or TITLE_EXCLUDE_RE.search(TITLE_PHD_RE.sub(" ", title_live) if PROFILE["allow_phd_roles"] else title_live)):
            drej["live title excluded"] += 1
            log(f"    - {j['company']} / {title_live[:60]}: live title excluded")
            continue
        if info.get("location") and info["location"] != j["location"]:
            ok_loc, lwhy2 = location_screen({"location": info["location"], "remote": j.get("remote")})
            if not ok_loc:
                drej["live location non-US"] += 1
                log(f"    - {j['company']} / {title_live[:60]}: {lwhy2}")
                continue
            j["loc_reason"] = lwhy2
        ok, dwhy, notes = description_screen(text)
        if not ok:
            drej[dwhy.split(":")[0].split(" ")[0]] += 1
            log(f"    - {j['company']} / {title_live[:50]}: {dwhy[:100]}")
            continue
        if any(re.search(r"citizen|clearance", q, re.I) for q in info.get("required_questions") or []):
            notes.append("form asks about citizenship/clearance")
        if info.get("workplace") and info["workplace"].lower() == "onsite":
            notes.append("Ashby workplaceType=OnSite")
        if info.get("error"):
            notes.append(f"page not verified: {info['error']}")
        if j.get("company_in_log"):
            notes.append("company already in applications log (different title)")
        live_posted = info.get("posted")
        if live_posted and j["posted"] and (j["posted"] - live_posted).days > args.days:
            notes.append(f"ATS says first published {live_posted.date()} (re-listed)")
        if re.search(r"\b(?:Northeastern|Drexel|Waterloo|RIT|Purdue|Georgia Tech|Cal Poly)\b", title_live) and re.search(r"co-?op|university", title_live, re.I):
            notes.append("university-specific co-op program")
        apply_url = info.get("apply_url") or j["url"]
        survivors.append({
            "company": j["company"],
            "title": title_live,
            "location": info.get("location") or j["location"],
            "ats": j["ats"],
            "url": info.get("canonical_url") or j["url"],
            "apply_url": apply_url,
            "posted": j["posted"] or info.get("posted"),
            "live_posted": info.get("posted"),
            "source": j["source"],
            "pass_reason": f"{j['title_reason']}; {j['loc_reason']}; {dwhy}",
            "required_questions": info.get("required_questions") or [],
            "notes": notes,
            "company_in_log": j.get("company_in_log", False),
            "new": not (norm_url(apply_url) in since or norm_url(j["url"]) in since),
            **({"hn_url": j["hn_url"]} if j.get("hn_url") else {}),
        })
    stats["fetched"] = fetched
    stats["survivors"] = len(survivors)
    stats["new"] = sum(1 for r in survivors if r["new"])
    log(f"[desc] {len(survivors)} survivors; description rejects: {dict(drej)}; HTTP requests: {http.count}")

    survivors.sort(key=lambda r: r["posted"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    write_outputs(survivors, args.out_dir, date, args.since_file, stats, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
