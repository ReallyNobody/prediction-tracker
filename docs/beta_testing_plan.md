# Beta Testing Plan — RMN Hurricane Dashboard

*Pre-launch user-validation strategy.*
*Prepared April 30, 2026. Three phases run from now through June 10, 2026 (one day before the webinar).*

---

## TL;DR

Beta testing for a public data dashboard isn't really feature-validation
— the dashboard either renders or doesn't, and the features have shipped.
What we're actually testing:

  1. **Editorial clarity** — does the translation-for-non-specialists work?
     The whole RMN thesis depends on this and it's the hardest thing to
     evaluate from inside the codebase.
  2. **Cross-platform integrity** — share cards on iMessage / Slack /
     Twitter / LinkedIn / Discord; mobile Safari / Chrome / Firefox;
     embeds in newsletters. These break in different ways and there's
     no way to test them without real-world distribution.
  3. **The "what's missing?" signal** — questions that come up across
     multiple testers usually point at a missing caption, a missing
     panel, or a feature implication. This is the highest-leverage
     feedback we'll get.

**Three phases**, oldest-to-newest in time:

  * **Phase A — Inner-circle technical sanity** (this week, ~3-5 days).
    2–3 people who'll tell you the truth, hunting for bugs.
  * **Phase B — Soft-launch target-audience taste test** (Tuesday May 26
    → Friday May 29). 5–10 RMN-adjacent professionals across mixed
    archetypes. Real signal-gathering.
  * **Phase C — Webinar dress rehearsal** (June 8–10). 1:1 with Vijay
    Manghnani and Patrick Brown, walking the dashboard ahead of the
    live event.

Triage windows scheduled between phases so feedback actually shapes the
product before the next group sees it.

---

## Phase A — Inner-circle technical sanity

**When**: This week, 3-5 days max.
**Audience**: 2-3 people you'd bet money will tell you the truth, no
matter how senior they are. Doesn't have to be target-audience-aligned;
can be tech-savvy friends who'll actually open the URL on their phone
and report what's broken.
**Goal**: Catch the embarrassing bugs before anyone outside your trust
circle sees them — mobile rendering edge cases, share-card failures on
specific platforms, panels that crash on a specific browser.

### What to ask

Nothing structured. The pitch is short and informal:

> *"I'm soft-launching a hurricane risk dashboard in a few weeks and
> need a sanity check. Can you open this on your phone (and maybe
> also your laptop), spend 60 seconds scrolling, and tell me anything
> that looks visually broken? Don't worry about whether it's useful —
> just whether it's broken. Reply when you can."*
>
> *URL: https://hurricane.riskmarketnews.com*

### What to watch for

- Layout breaks at iPhone-SE width (375px) — the narrowest realistic
  mobile target.
- Share-card render on iMessage when one tester sends the URL to
  another. iMessage cards have a distinctive failure mode where they
  fall back to text-only when OG meta is malformed.
- Browser-specific issues: Firefox handles CSS gradient stops slightly
  differently from Chromium; Safari has its own quirks with sticky
  positioning.
- Slow first paint on cold-load: Render Starter plan can take 5-15s on
  a cold container. If a tester reports "took forever to load," that's
  Render-cold-start, not your code; non-blocking unless egregious.

### Bug-fix budget

Up to 4 hours of fix work between Phase A and Phase B. Anything bigger
than that and you defer to a Phase 2 follow-up rather than risk
introducing regressions before Phase B.

---

## Phase B — Soft-launch target-audience taste test

**When**: Tuesday May 26 (avoiding Memorial Day weekend May 24-25) →
Friday May 29 reply deadline.
**Audience**: 5-10 RMN-adjacent professionals in the target demographic.
Aim for archetype diversity — the same panel reads differently to a
journalist than to a buy-side analyst, and you want both signals.

### Recommended archetype mix

Try to cover at least four of these six archetypes among 5-10 testers:

  * **Journalist covering insurance / catastrophe risk** — catches
    factual errors and unclear framing; also has an instinct for
    "what's the lede here?" that surfaces missing emphasis.
  * **Buy-side analyst** (insurance equities, ILS funds) — validates
    whether Panel 3 (Hurricane risk capital) is editorially honest
    relative to how their firm thinks about the underlying.
  * **Insurance operator / claims professional** — surfaces jargon
    that reads cleanly to insurance people but opaque to others.
    Often the most useful corrective on caption copy.
  * **Emergency manager / planner** — validates Panel 1 (cone map) and
    Panel 4 (landfall probability) against how they read NHC products
    professionally.
  * **Quantitatively-fluent generalist** — your "smart skeptic"
    audience proxy. If they don't get it, you have a translation
    problem.
  * **Non-specialist** (writer, designer, anyone outside the
    insurance/finance world) — catches every piece of jargon you
    didn't realize was jargon.

You don't need all six. Four with overlap is fine. Two or three good
testers per archetype is better than spreading thin.

### The structured ask

Open-ended "what do you think?" generates either silence or polite
"looks great." Three concrete asks generate actionable reactions:

> *"Soft-launching a hurricane risk dashboard a week before our
> June 1 launch. Three small asks:*
>
> *1. Try it on your phone and tell me anything confusing in 30
> seconds of scrolling.*
>
> *2. Share the URL once in a Slack DM or group chat where you'd
> plausibly share something like this. I want to see whether the
> preview card renders right.*
>
> *3. Read every panel caption and tell me where I'm using jargon
> you don't expect a thoughtful generalist to know.*
>
> *URL: https://hurricane.riskmarketnews.com*
> *Reply by Friday May 29 if you can. No worries if not."*

Why the structure works:

  * **Ask 1 (mobile, fast)** captures first-impression friction — the
    only signal that matters for whether someone bookmarks vs. closes.
  * **Ask 2 (share)** is real-world QA for the OG card across whatever
    platforms your testers actually use. Free distributed testing.
  * **Ask 3 (caption read)** is the editorial-clarity check. You're
    not asking "is it good?" — you're asking "where is it bad?" That
    flips the response cost; criticism is easier than praise.

### Feedback capture

A short Google Form is worth the 10 minutes to set up. Not because the
form is better than reply emails — it isn't — but because it
**concentrates the responses in one place** rather than scattered across
your inbox. Suggested fields:

  * Free text: "Most confusing thing in 30 seconds of scrolling"
  * Free text: "Did you share the URL? If so, where, and did the
    preview card look right?"
  * Free text: "Any jargon you didn't expect a generalist to know?"
  * Optional: "Anything else?"
  * Optional: name + role (helps you weight the feedback by
    archetype)

Send the form link in the same email as the structured ask, with a
"reply directly is also fine if easier" fallback so you don't lose
testers who hate forms.

### Triage window: Friday May 29 → Sunday May 31

Sit with the feedback Friday afternoon. Triage by Sunday end-of-day:

  * **Real bugs** → fix Sunday. Polish-pass-only; no new features.
  * **Jargon flags** → caption rewrites Sunday. Same constraint.
  * **Feature requests** → note for Phase 2 backlog and reply to the
    sender with that framing ("noted for post-launch — would you mind
    if I follow up when it ships?"). Builds a soft beta-tester loop
    for after launch.

Hard rule: **no new features ship between Phase B feedback and June 1
launch**. Polish only.

---

## Phase C — Webinar dress rehearsal

**When**: Monday June 8 — Tuesday June 9 (or Wednesday morning June 10
if scheduling forces it).
**Audience**: Vijay Manghnani and Patrick Brown, separately.
Each gets ~30 minutes of your time.
**Goal**: They walk through the dashboard out loud while you watch.
Surfaces any panel that renders weird on their setup, any data they
expect to see that's missing, any moment where they hesitate or look
confused.

### What to ask

> *"We're 2-3 days from going live with the webinar. Mind if I walk
> you through the dashboard for 30 minutes so you're not seeing it
> for the first time on camera? I'd like to know which panels you'd
> naturally point at, anything that looks weird on your setup, and
> anything you expect to see that isn't there."*

The ask is generous — you're saving them prep time, not asking for a
favor.

### During the call

- Share screen, pull up the dashboard.
- Ask them: **"If you were narrating this for an audience, where
  would your eye go first? What would you want to point at?"** Then
  watch the panels they hesitate on or look confused at — those are
  the panels that need a tooltip or caption tweak before the live
  event.
- Don't pre-script their commentary. The point of the rehearsal is
  authenticity; over-rehearsed practitioners read stiff on camera.
- Take notes silently. Address fixes in your own time, not theirs.

### Specific things to watch for

  * **Vijay (King Ridge Capital, ILS practitioner)**: Is the cat bond
    proxy on Panel 3 (ILS) tracking what his firm is seeing
    institutionally? Is there a moment where he wants to compare to a
    benchmark we don't surface? Note it for Phase 2 backlog if so.
  * **Patrick (Interactive Brokers)**: Is the equity universe on
    Panel 2 covering the names that actually trade with hurricane
    flow? Does the Kalshi readout on Panel 4 (Prediction Markets,
    after Day 31 rename) match what he sees in retail flow? Are the
    OI and Yes-price fields legible to him without the explainer
    caption, or does the caption add real signal?

### Triage window: same evening as each rehearsal

Anything they surface that's a copy / caption fix → ship it the next
morning. Anything that's a feature gap → Phase 2 backlog. By
Wednesday morning (June 10) the polish-week window closes; June 11
is the webinar.

---

## Cross-references

  * **launch_checklist.md** has a soft-launch section that overlaps
    with Phase B; treat that section as the operational checklist for
    the email send + day-of monitoring, and this doc as the deeper
    how-and-why. The launch checklist also captures the day-of-launch
    pre-flight, contingency triggers, and rollback procedure for
    June 1, which are out of scope here.
  * **post_event_loss_estimates_memo.md** captures the Phase 2 panel
    proposal that Vijay's role in Phase C is most likely to surface
    interest in. Worth re-reading the day before his rehearsal so
    you can speak to it if he asks.

---

## Phase metrics — what counts as "good enough" to advance

Defining a stopping condition matters because beta testing without one
becomes infinite re-iteration and delayed launch.

### Phase A → Phase B advance criteria

  * No reproducible visual bug on iPhone Safari.
  * Share card renders correctly on at least one of: iMessage, Slack,
    Twitter (any other failure is platform-cache / hard to debug from
    inside).
  * No 500 errors observed.

### Phase B → Phase C advance criteria

  * At least 5 testers responded with substantive feedback.
  * Critical jargon flags caught in 3+ tester responses are captioned
    or rewritten.
  * Mobile rendering works on phones tested by at least 3 separate
    testers.

### Phase C → Launch advance criteria

  * Both panelists walked through the dashboard and surfaced no
    blocking confusion.
  * Any same-day-fixable issues are fixed by EOD Tuesday June 9.
  * No panel renders broken on the panelists' setups.

If any of these criteria fail, the question becomes "ship anyway and
fix forward, or delay?" — that decision is contextual, but the bias
should always be toward shipping. June 1 launch is the editorial
anchor for the season; missing it forfeits the editorial logic of
"hurricane dashboard at hurricane season opener."

---

## Open questions

  1. **Feedback form choice** — Google Forms is the default; Tally /
     Typeform are nicer-looking but more setup. Lowest-friction wins
     for Phase B.
  2. **How to thank testers** — a follow-up email after launch
     thanking each Phase B tester individually and noting a piece of
     feedback you actually acted on is high-signal for whether
     they'll soft-launch the next thing for you. Worth doing.
  3. **Phase A names** — TBD. Three close friends who'll tell you the
     truth.
  4. **Phase B archetype slots** — TBD. Aim for 5-10 names across
     4+ archetypes.

---

*Plan status: phase structure complete; specific tester lists TBD.
Revisit the Open Questions section by Tuesday May 19 (one week before
Phase B kickoff).*
