---
name: job-hunt
description: >-
  Find fresh early-career software/ML/data jobs from public sources, verify each
  against the employer's live job-board API, filter by the user's own profile,
  and apply through the Jobright.ai agent and Chrome autofill extension with a
  review-before-submit policy. Use for "find me jobs", "run my job search",
  "apply through Jobright", or "finish the queue".
---

# Job hunt: discover, screen, apply

Two stages. Stage 1 builds a list of open roles that fit the user. Stage 2 applies to the ones the user approves. **Nothing is submitted to an employer until the user has seen the filled answers for that job**, unless they say in words that unreviewed submission is accepted for this run. A wait costs nothing; a submission cannot be undone.

## Setup (once per user)

1. Copy `applicant-profile-template.md` to `profile.md` in the working folder and fill in what Jobright's saved profile does not already hold. Never store passwords or verification codes in it.
2. Copy `profile.example.json` to `my_profile.json` and set the fit rules (sponsorship, citizenship or clearance, max years, PhD roles, internships, US only). Run `python3 -I job_discovery.py --print-profile --profile my_profile.json` to confirm.
3. Keep an `applications-log.md` markdown table (`Date | Company | Title | Match | ATS | Outcome | Notes/URL`) in the working folder. Discovery reads it to avoid duplicates.

## Stage 1: Discover

```bash
python3 -I job_discovery.py --days 3 --profile my_profile.json --since-file
```

- Pulls a startup job feed, the SimplifyJobs new-grad and internship lists, and the latest Hacker News "Who is hiring?" thread.
- Checks Greenhouse, Ashby, Lever and Workable postings live (see `ats-api-quirks.md`), so closed roles are dropped.
- Writes `discovery-<date>.md` and `.json` (newest first, with why each role passed and its required form questions) and `discovery-<date>-jobright.txt`.
- The `-jobright.txt` file has one ready-to-paste line per batch of up to 10 apply URLs: `Add these jobs to my application list: <url1>, <url2>, ...`. `--since-file` makes each re-run list only new URLs.
- The filters are heuristics. Read the posting before applying.

## Stage 2: Apply through Jobright

Use the user's connected Chrome with the Jobright extension. Reuse an existing employer tab. For a supplied job URL, work on that job and do not start an unrelated search.

### Fit gate (before Approve or Submit)

Read the posting, not the tile. A match score is a ranking signal, not eligibility. Skip and log when:

- The posting says it will not sponsor, or requires citizenship, "US person" status or a clearance, and the user needs sponsorship.
- It is an internship and the user wants full-time, or it needs more years than the resume supports. Ask, do not assume.
- Any autofilled value is not in the user's saved profile or resume. "11/11 filled" means non-empty, not correct. Seen in practice: a salary of "120", a skill matched to the wrong item, and demographic answers set that the user never chose.

### On jobright.ai/agent, Approve is Submit

The agent card runs: Generate Custom Resume, then **Confirm Custom Resume [Approve]**, Analyze Site, Fill Form, Submit. After Approve there is no review gate, and the agent has sent wrong answers before. So Approve needs what Submit needs: the job passes the fit gate and the user OK'd this job.

| Situation | Do |
|---|---|
| "Approve each resume and keep going" | Run each job through the fit gate. If it passes, leave the card at Action Required and ask. This is resume approval, not blanket submit authority. |
| "Approve X, I accept it will be sent" | Approve X. |
| Fit gate fails | Click **Skip**. Skip cancels that job (it can be re-added), so never use it as "wait". |
| User away, job passes the gate | Wait. A stalled queue is recoverable. A wrong submission is not. |

Never click **Pause**. It halts the whole queue.

### Operating the autofill extension

1. Open the employer application. On Workday, **Apply Manually** opens the form Jobright fills.
2. Run the Jobright panel's control (**Autofill**, **Sign In & Autofill** or **Account Creation & Autofill**). Let it finish. Skills can keep filling after 100%, so wait until the action button is enabled.
3. Verify the real form. Correct demonstrable mapping errors (for example the Go language becoming "Go-Carts"). Ask about unresolved factual ambiguity instead of guessing. See `ats-autofill.md` for per-platform checks.
4. Browser accessibility views can hide sensitive values. An empty-looking email, phone or password box may be filled. Check a screenshot before reporting it missing, and never reveal a masked password.
5. Show the user the filled answers and any resume edits. Submit only after a clear yes.
6. After Submit, wait for the employer's own confirmation. Jobright's 100% only means autofill progress. If the outcome is uncertain, check candidate status before retrying. Search the user's email for an earlier confirmation from the company before submitting.

### Accounts

An account screen alone is not a blocker. Use credentials the user saved in Jobright. A rejected sign-in is a real blocker: do not retry the same credentials in a loop. Never write passwords, tokens or codes into files, logs or screenshots.

## Logging

Record every outcome in `applications-log.md`: date, company, title, match %, ATS, and one of **Submitted (reviewed)**, **Submitted by agent (unreviewed)**, **Flagged + why**, **Skipped + why**. Never label an agent-submitted application as reviewed. Record verified state, not an attempted action.

## Red flags: stop, you are rationalizing

- "The instruction said approve, so submit." Approve submits. The instruction did not say that.
- "Waiting stalls the queue." Yes. That is recoverable.
- "I will skip it for now." Skip cancels the job. Wait instead.
- "I will log it as not reviewed." Logging a wrong submission does not unsend it.
