# job-hunt: a Claude Code skill for finding and applying to early-career jobs

Two stages in one skill:

1. **Discover.** `job_discovery.py` finds fresh early-career software, ML and data jobs from public sources, confirms each is still open through the employer's own job-board API, and filters them by *your* profile.
2. **Apply.** `SKILL.md` tells Claude how to queue those jobs into the Jobright.ai agent and Chrome autofill extension, with a review-before-submit policy so nothing is sent to an employer unseen.

## Install

```bash
pip install requests
git clone https://github.com/goodwiins/job-hunt-skill ~/.claude/skills/job-hunt
```

Restart Claude Code, then ask for "find me jobs and queue them in Jobright" or run `/job-hunt`. Update later with `git pull` inside the folder.

You do not need Claude to use the discovery script on its own:

```bash
cd ~/.claude/skills/job-hunt
python3 -I job_discovery.py --days 3
```

## Make it fit you

Copy `profile.example.json` to `my_profile.json`, edit it, and pass `--profile my_profile.json`. Copy `applicant-profile-template.md` to `profile.md` for answers that Jobright's saved profile does not hold.

| Key | Default | Meaning |
|---|---|---|
| `needs_sponsorship` | `true` | Reject postings that say they will not sponsor a visa. Set `false` if you do not need sponsorship. |
| `us_citizen_or_clearance_ok` | `false` | Keep postings that require US citizenship, a clearance or ITAR. Set `true` if you qualify. |
| `max_years_required` | `2` | Reject postings whose minimum years of experience is higher. |
| `allow_phd_roles` | `false` | Keep PhD-titled and PhD-only roles. |
| `us_only` | `true` | Drop roles located outside the US. |
| `include_internships` | `true` | Set `false` to drop intern and co-op roles. |
| `skip_account_portal_ats` | `true` | Drop Workday, iCIMS, Taleo, SmartRecruiters and similar sites that need an account first. |
| `title_include_extra` | `[]` | Extra regex fragments that count as your kind of role, for example `"security"`. |
| `title_exclude_extra` | `[]` | Extra regex fragments that reject a title. |
| `exclude_companies` | `[]` | Company names to skip. |

## Outputs

| File | Contents |
|---|---|
| `discovery-<date>.md` | Table plus per-role details and required form questions |
| `discovery-<date>.json` | Same roles, machine-readable |
| `discovery-<date>-jobright.txt` | Paste-ready "Add these jobs to my application list: url, url, ..." lines, up to 10 URLs each |

`--since-file` skips URLs already written to earlier `-jobright.txt` files. `--log applications-log.md` skips companies and titles you already applied to. `--days N` sets the posting window and `--hn-days` the Hacker News window.

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | The skill: setup, discovery, fit gate, Jobright rules, logging |
| `job_discovery.py` | The discovery script (Python 3.10+, `requests`) |
| `profile.example.json` | Fit-rule settings for the script |
| `applicant-profile-template.md` | Optional personal answers for application forms |
| `ats-autofill.md` | Per-platform checks after autofill |
| `ats-api-quirks.md` | Notes on the public job-board APIs the script uses |

## Things to know

- The script only reads public data. It never logs in or submits anything.
- The filters are heuristics. Read each posting before you apply.
- Lever, Workable and company-site postings do not expose form questions through public APIs.
- The skill assumes Jobright.ai with its Chrome extension. Without Jobright, use Stage 1 alone.
