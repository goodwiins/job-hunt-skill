# Job-board ATS public API quirks

Findings from writing `~/development/job_discovery.py`, a stdlib+requests script that pulls early-career roles from public feeds and verifies each posting against the ATS's public API. All confirmed by live HTTP calls on 2026-10-21.

## Ashby GraphQL: `field` is a JSON scalar

**Behavior:** `POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting` returns `GRAPHQL_VALIDATION_FAILED: Field "field" must not have a selection since type "JSON!" has no subfields` when selecting sub-fields of `applicationForm.sections.fieldEntries.field`.

**Workaround / Pattern:** Select `fieldEntries { isRequired field }` with no sub-selection; `field` comes back as a dict with `title`, `type`, `path`, `isNullable`, `selectableValues`. Variables are `organizationHostedJobsPageName` (URL org slug) and `jobPostingId` (UUID). An unlisted or deleted posting returns HTTP 200 with `data.jobPosting = null` (not a 404). Direct apply URL is `https://jobs.ashbyhq.com/<org>/<uuid>/application`.

**Confirmed by:** live calls against skydio, rhoda-ai, openai boards.

## Greenhouse: universal direct-apply form and board-slug discovery

**Behavior:** `GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true` returns `content` (HTML, *double*-escaped — unescape twice), `questions[]` with `required`, `compliance`, `demographic_questions`, `first_published`, `location.name`, `absolute_url`. 404 means closed. The form at `https://job-boards.greenhouse.io/embed/job_app?for=<board>&token=<id>` works for every board regardless of the company's own careers domain.

**Explanation:** Many company sites embed Greenhouse via `?gh_jid=<id>` and do not expose the board slug in the URL. Guessing the slug from the company name (lowercase alphanumerics, e.g. `waymo`, `databricks`) works most of the time; otherwise the page HTML contains `greenhouse.io/embed/job_board?for=<slug>`.

**Confirmed by:** 200 responses for drivewealth, shift5, atoms, waymo, scaleai, storable boards.

## SimplifyJobs `category` is the company's sector, not the role's

**Behavior:** In `New-Grad-Positions` and `Summer2027-Internships` `listings.json`, `category: "Hardware"` rows include "Software Engineer Intern" at RTX, L3Harris, Ciena, Keysight. Filtering on category silently drops real software roles.

**Workaround / Pattern:** Classify by `title` regex only; use `category` as informational. Useful fields that *are* role-level: `sponsorship` ("Does Not Offer Sponsorship" / "U.S. Citizenship is Required" / "Offers Sponsorship" / "Other"), `degrees` (PhD-only lists are reliably PhD-only), `active`, `is_visible`, `date_posted` (epoch seconds; recent re-listings of old postings happen - Greenhouse `first_published` can be years older).

**Confirmed by:** audit of 3-day window (378 rows), 37 "Hardware" rejects re-inspected.

## Workable has a public JSON API

**Behavior:** `apply.workable.com/<acct>/j/<SHORTCODE>/` pages are JS-rendered (thin HTML), but `GET https://apply.workable.com/api/v2/accounts/<acct>/jobs/<SHORTCODE>` returns `title`, `description`, `requirements`, `benefits`, `location{city,region,country}`, `remote`, `workplace`, `published`, `state`. 404 = closed. Form questions are not exposed. Apply URL: `.../j/<SHORTCODE>/apply`.

**Confirmed by:** funnel-leasing and node accounts.

## Lever public postings API

**Behavior:** `GET https://api.lever.co/v0/postings/<org>/<uuid>` returns `text` (title), `descriptionPlain`, `lists[]` (requirement bullets as HTML in `content`), `additionalPlain`, `categories{commitment,location,team}`, `createdAt` (epoch ms), `applyUrl`, `hostedUrl`. No form questions. 404 = closed.

## Meta careers pages: PhD gating hides in JSON-LD

**Behavior:** `metacareers.com/jobs/<id>` is JS-rendered, but ships a `<script type="application/ld+json">` schema.org `JobPosting` block whose `title` carries the suffix the listing aggregators drop, e.g. "Research Scientist Intern, PhD - Meta Superintelligence Labs". SimplifyJobs shows the same role as plain "Research Scientist Intern".

**Workaround / Pattern:** Parse JSON-LD first on any unknown careers site; re-run title exclusions on the live title. ^[inferred] Generalises to other JS career sites (Salesforce, Workable) which also ship JobPosting JSON-LD.

## Regex gotcha: "pursuing a BS, MS, or PhD" is not PhD-only

**Problem:** A rule like `(pursuing|enrolled in)[^.]{0,40}PhD` rejected Rhoda AI and Scale AI internships whose text reads "Currently pursuing a BS, MS, or PhD in Computer Science".

**Fix:** Negative lookahead for `BS|MS|Bachelor|Master` between the verb and "PhD", plus a trailing lookahead for "or MS". "Currently has, or is in the process of obtaining, a PhD" (Meta wording) must still match.

**Confirmed by:** unit check on seven phrasings; false positives gone on re-run.

## Location string collisions

**Behavior:** City-name lists for non-US detection misfire on "Vienna, VA", "Paris, TX", "Cambridge, MA"; US-city lists misfire on "Cambridge, UK".

**Workaround / Pattern:** Split multi-location strings on `;`/`|`/" or ", and per segment check a *strong* US marker first (", <state abbrev>", "United States", "USA", "Remote in USA"), then non-US names, then weak US city hints. Keep the role if any segment is US.

**Notes:** HN "Who is hiring" (`hn.algolia.com`, `tags=comment,story_<id>`, keep `parent_id == story id` for top-level posts) is monthly, so a 3-day window is almost always empty; scan ~31 days and de-dup by URL instead. The `jobs.ypatodkar.com/api/all-jobs.json` feed (~12k rows, 5.9 MB) has `seniority` in {intern, junior, mid, senior, staff, manager, exec} and `career` in {early, experienced, unclear}; `c` indexes into `companies[]`.
