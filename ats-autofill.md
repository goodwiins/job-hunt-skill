# ATS checks

Use after Jobright fills an employer form. Current page content is authoritative;
these are workflow hints, not promises about every employer configuration.

## Answer sources

Use the user's explicit answers, saved Jobright profile, and resume. Optional
local `profile.md` can supplement them. Ask only for unresolved required facts.

- Authorization and sponsorship: check the two questions separately; do not
  substitute one answer for the other.
- Education: distinguish enrollment, expected graduation, and earned degrees.
  A field labelled degree under “highest education completed” must not silently
  turn an in-progress program into a completed qualification.
- Employment and skills: check company names, dates, current-employment flags,
  and selected skill labels; fuzzy matching may choose unrelated items.
- Location, salary, start date, employer history, and contractual restrictions:
  use saved answers when applicable; do not invent defaults.
- Voluntary demographics: use the user's saved explicit choices. Otherwise
  leave optional fields blank or select decline-to-identify where available.
- Free text: review generated material against known facts. Do not transmit
  invented achievements or personal motivations as the user's statements.
- Resume: confirm the actual filename and upload success on the employer form.

## Platform behavior

**Workday:** employer-specific account followed by information, experience,
questions, disclosures, and review (steps vary). Use Jobright's green buttons
and inspect each transition. Sign-in may fail even though saved credentials
are present. Follow the authorized account flow and report the actual result.
Consent can appear at account creation and again in disclosures; inspect the
wording and apply current confirmation rules and existing approval scope.

**Greenhouse / Lever / Ashby / Workable:** commonly one form with custom
screening questions. Check uploads, required fields, and employer-specific
questions before submission; do not assume Easy Apply semantics.

**iCIMS:** multi-page forms may reformat resume data. Verify parsed values after
navigation and watch for account or verification steps.

**LinkedIn Easy Apply:** use the visible multi-step form and saved answers.
If it redirects to an employer site, inspect that site's form and requirements.

## Recovery

If autofill fails, first inspect the visible form, including a screenshot for
sensitive fields omitted from text output. Do not rerun autofill while it is
active. Use supported saved values for targeted corrections. Reload only when
necessary and safe for unsaved work, then re-read the state. Never blindly retry
account creation or submission after an uncertain result.
