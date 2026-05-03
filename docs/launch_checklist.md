# Launch checklist — RMN Hurricane Dashboard

*Target: Monday, June 1, 2026. Atlantic hurricane season opener.*

This doc is the living checklist for the soft-launch and day-of-launch
sequence. Drafted on Day 24 (April 28, 2026) — five weeks out — so the
shape can be reviewed and locked, with the date-specific details to be
filled in closer to the day.

It is deliberately opinionated: ordering, contingencies, and rollback
paths are written down now so launch day is execution, not decisions.

---

## TBD — fill these in before May 25

These are the inputs that turn the checklist into a real plan. The rest
of the doc is structured so the answers slot in cleanly.

- [ ] **Newsletter platform.** Which one? (Substack / Beehiiv / Ghost /
      RMN-house?) Affects preview-card testing and the send sequence.
- [ ] **DNS for `hurricane.riskmarketnews.com`.** Day 28 architecture
      decision: dashboard lives on a `hurricane.` subdomain so the
      apex (`riskmarketnews.com` / `www.`) stays for the existing
      Ghost-hosted RMN content site. CNAME at Hover →
      `rmn-hurricane-dashboard.onrender.com`; Render auto-issues
      the Let's Encrypt cert once DNS resolves. The OG meta tags
      hardcode `https://hurricane.riskmarketnews.com`, so the
      canonical subdomain must resolve before share-card previews
      work end-to-end.
- [ ] **Apex/`www` Ghost wiring** — out of scope for this checklist
      but worth tracking somewhere. Today (~6 weeks pre-launch) the
      apex shows the Hover parking page; before launch it should
      point at the existing Ghost site so registrants who type
      `riskmarketnews.com` get the broader RMN brand, not parking.
      Ghost has its own custom-domain wiring (separate from Render).
- [ ] **Tweet cadence.** Single launch tweet, or a 2–3 tweet thread?
      Affects what supporting copy needs drafting now vs. on the day.
- [ ] **Soft-launch audience.** 2–3 RMN-adjacent friends, ideally one
      from each of: journalist / newsroom, buy-side or insurance ops,
      operator/founder. Names and contact channels to go here:
  - [ ] Person 1 — _____
  - [ ] Person 2 — _____
  - [ ] Person 3 — _____
- [ ] **Soft-launch date.** Tuesday May 26 is the working assumption
      (Memorial Day weekend takes May 24–25 off the table). Confirm
      and put it on the calendar.

---

## Soft-launch (~1 week pre-launch)

> **See also**: [`beta_testing_plan.md`](beta_testing_plan.md) for the
> full three-phase user-validation strategy (inner-circle bug-hunt
> this week → soft-launch May 26 → webinar dress rehearsal June 8-10).
> This section is the operational checklist for soft-launch day; the
> companion doc covers archetype mix, structured asks, feedback
> capture, and triage windows in more depth.

### Purpose

Not focus-group feedback in the abstract sense — a real-world dress
rehearsal for the things that can't be simulated locally:

- Share card actually rendering on iMessage, Twitter, LinkedIn, Slack,
  Discord (each crawler caches independently and occasionally surprises
  you)
- Newsletter platform's preview matching what we intend
- Click-through paths working from mobile email clients (Gmail iOS,
  Apple Mail, Outlook mobile all handle preview cards differently)
- Render Starter plan absorbing a small burst of traffic without
  visibly tipping over (cold-start latency, memory headroom)

The 2–3 friends are doing real-user QA with permission to tell us
what's broken — not formal user research.

### The ask

Send a short note to the soft-launch list. Suggested template:

> Soft-launching a hurricane risk dashboard a week from launch. Two
> small asks:
>
> 1. **Share the link** in any thread or group where you'd plausibly
>    share something like this — a Slack DM, a group chat, a
>    Twitter/LinkedIn post, etc. I want to see whether the preview
>    card renders right across platforms.
> 2. **Try it on your phone** and tell me anything that looks broken
>    or confusing in 30 seconds of scrolling. I'm specifically not
>    asking for a thoughtful read; first-impression friction is what
>    I need.
>
> URL: https://hurricane.riskmarketnews.com
>
> Reply by [date] if you can. No worries if not.

Keeps the burden low and the signal pointed.

### What to verify based on what comes back

- Card renders cleanly on iMessage / Twitter / LinkedIn / Slack? Good
  — OG metadata is right.
- Card text-only or wrong image? Twitter Card Validator + Facebook
  Sharing Debugger will tell us which crawler is unhappy and why.
  Push a fix; force-refresh those two debuggers (they cache for 24h+).
- Mobile layout cramped or broken? The Day 19 mobile responsive pass
  set Tailwind's `sm:` breakpoint at 640px, so test on iPhone SE
  width (375px) where things are tightest.
- Page slow on cold-start? Render's free-tier-style cold start can
  take 30+ seconds. Starter ($7/mo, current plan) should be hot but
  the first request after a Render auto-deploy can be sluggish.

---

## Day-of launch — Monday June 1, 2026

Read top to bottom; rough chronological order.

### Pre-flight (the morning of)

- [ ] `pytest` clean. Full suite, no skips that aren't expected.
- [ ] `ruff check .` and `ruff format --check .` clean.
- [ ] `pre-commit run --all-files` clean (belt-and-suspenders on the
      ruff checks; runs the same hooks).
- [ ] Render service status: **Live**. Most recent deploy succeeded.
- [ ] `curl https://hurricane.riskmarketnews.com/healthz` → `{"status": "ok"}`.
- [ ] Hit the live URL in **three contexts**:
  - [ ] iPhone Safari (real device, not simulator)
  - [ ] Desktop incognito (no cached assets)
  - [ ] [Twitter Card Validator](https://cards-dev.twitter.com/validator)
        + [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/) —
        confirm the OG image renders as a `summary_large_image` card.
- [ ] Eyeball the panel layout. All seven panels render: Active storms,
      Prediction Markets, Landfall probability, Companies on the line,
      Hurricane risk capital, Historical analogs, What changed today.
- [ ] Render logs show recent successful ingest ticks (yfinance the
      cleanest signal; should be persisting ~37 rows every 15 min).
- [ ] **Plausible analytics counting events.** Visit
      `https://plausible.io/hurricane.riskmarketnews.com` — should
      show a non-zero pageview count from your own pre-flight visits.
      If "no data yet," the script tag isn't loading; check Render
      deploy logs and view-source on the live site.

### The send

- [ ] Newsletter publishes (platform TBD).
- [ ] Tweet announcing — dashboard URL as the primary card so
      `summary_large_image` is in front of every retweeter.
- [ ] LinkedIn post (if the audience overlaps).
- [ ] Send the launch note to the soft-launch list with a thank-you.

### The first hour

- [ ] **Watch Render logs live.** Any new error log is worth
      investigating immediately. The most likely failure modes:
  - Yahoo Finance endpoint format change → yfinance dep upgrade
    fixes most of these
  - Kalshi auth blip → check `KALSHI_API_KEY_ID` and the private-key
    secret file haven't drifted
  - NHC URL change (rare; happens ~yearly) → patch the URL in
    `config.py`
- [ ] **Watch Render metrics.** Request rate + memory in the dashboard's
      Metrics tab. Starter plan tips over around moderate sustained
      traffic.
- [ ] **Have the bump-to-Standard plan ready.** If you see a sustained
      spike, Render's dashboard takes ~30 seconds to upgrade
      Starter ($7/mo) → Standard ($25/mo) with no downtime. Don't
      hesitate; the cost of a bad first impression is worse than $18.
- [ ] **Reply to anyone who replies.** First-day engagement is the
      highest-leverage marketing window the dashboard will get.

### The first day

- [ ] **Don't ship code on launch day** unless something is genuinely
      broken (data missing, panel crashing, OG card busted). Resist
      the urge to fix copy or tweak colors. The audience won't notice;
      you'll just risk introducing a regression while attention is
      on you.
- [ ] **Note the questions that come up multiple times.** Those are
      the caption / copy fixes for Day 25+. A repeated "what does X
      mean?" usually means an unobvious term needs a tooltip or an
      inline gloss.
- [ ] **Save the launch screenshots and metrics** (request count,
      uniques if you have analytics, social shares). Useful as
      anchoring data for retros and future launches.

### Contingency triggers — when to roll back

Rollback is fast and cheap; use it. The criteria:

- `/healthz` returns 502 / 503 / times out for **> 5 minutes**
- Any ingest job logs an exception **every tick** (not just one transient
  blip — sustained failure)
- Render auto-restarts the service repeatedly (visible in the Events
  tab as multiple deploys in a short window)
- Any panel renders blank or as a JS error in production for **all
  visitors** (not just one platform)

### Rollback procedure

Memorize this *before* you need it.

```
# Find the last good commit (most recent commit that was confirmed
# healthy in prod — usually whatever shipped before the bad change).
git log --oneline -20

# Force-push that commit as the new main. Render will auto-deploy
# in ~3 minutes from origin/main.
git push origin <good-commit-sha>:main --force
```

Render's auto-deploy is the rollback mechanism. There is no separate
"rollback" button needed. The deploy takes ~3 minutes; in the meantime
the previous deploy keeps serving.

After rollback:

- [ ] Confirm `/healthz` is healthy on the rolled-back deploy.
- [ ] Confirm panels render.
- [ ] Open a branch off the new `main`, write the actual fix in
      isolation, push when ready (don't fix in a panic, fix in a
      branch).

---

## Post-launch (first week)

Lighter touch; mostly about absorbing what came back.

- [ ] **Triage the feedback inbox.** Anything that's a real bug → file
      and fix. Anything that's a feature request → note for the Phase
      2 backlog (CLIMADA loss-modeling Panel 7, NAIC drill-down,
      etc.) and reply with that framing.
- [ ] **Soft-launch retrospective.** What did the dress rehearsal
      surface that the day-of QA missed? Add to this doc's
      "lessons" section so the next launch is cheaper.
- [ ] **Backup verification.** Confirm Render's Postgres backup
      schedule is on. The Starter Postgres plan does daily snapshots;
      verify in the dashboard.
- [ ] **Decide on the next build sprint.** Phase 2 candidates: the
      CLIMADA loss-modeling panel (4-session estimate), KBW
      reinsurance-index synthetic basket (if KBWP-only feels thin),
      NAIC state-level insurer drill-down per Panel 2 row.

---

## Webinar — Risky Science Live, Wednesday June 11 2026, 2pm

The Risky Science Podcast Live session is a major amplification moment
for the dashboard. June 11 is 10 days into the 2026 hurricane season
and ~10 days after launch — long enough that the dashboard has real
ingest history, short enough that registrants are seeing a fresh
product. Both confirmed panelists map cleanly onto specific panels,
which is the point: we're not running a webinar *about* the dashboard,
we're running a substantive cat-risk discussion in which the dashboard
happens to be the live operating instrument.

### Confirmed panelists and panel mapping

- **Vijay Manghnani — CIO/CUO, King Ridge Capital.** ILS / reinsurance
  practitioner. His firm prices the instruments **Panel 3 ("Hurricane
  risk capital")** proxies via ILS and KBWP — he can validate or
  challenge whether the public ETFs track institutional reality.
  Secondary fit: **Panel 5 ("Historical analogs")** as a
  "this-reminds-me-of" device for any active or recent storm.
- **Patrick Brown — Interactive Brokers.** Broker/dealer perspective
  on flow and exposure. Maps onto **Panel 2 ("Companies on the line")**
  for hurricane-exposed equities and **Panel 4 ("Markets on it")** for
  the Kalshi prediction-market sentiment thermometer.
- **Visual anchor for any segment**: **Panel 1** (cone map) and
  **Panel 6** ("What changed today"). Panel 1 if there's an active
  storm in the basin on June 11 (early-June first-storm activity is
  uncommon but not rare — 2024's Beryl reached major status by
  late June, and a small percentage of seasons see a named system
  in week 2); Panel 6 otherwise as the running-commentary engine.

### Session description (revised)

The original draft has three issues: (1) it calls June 11 "the official
start" of the season when June 1 is the start; (2) panelists are
unnamed ("finance, modeling, and research professionals"); (3) no
mention of the dashboard. Revised text — drop into the registration
page in place of the original:

> Despite forecasts calling for moderate hurricane activity, the past
> several years have proven that fewer but more intense storms are
> driving growing insured and economic losses.
>
> Just over a week into the 2026 North Atlantic hurricane season,
> **Vijay Manghnani** (CIO/CUO, King Ridge Capital) and **Patrick
> Brown** (Interactive Brokers) join Risky Science Live for a working
> discussion on what the models are saying, where the markets are
> pricing risk, and what the science tells us about storm behavior in
> a changing climate.
>
> We'll work directly from the new **RMN Hurricane Dashboard**
> ([hurricane.riskmarketnews.com](https://hurricane.riskmarketnews.com))
> — live cat bond and listed-insurer pricing, real-time NHC forecasts
> and landfall probabilities, and a curated universe of hurricane-
> exposed equities — to ground the conversation in what's actually
> happening on the day.
>
> Topics: rapid intensification and compound storm events; ILS and
> reinsurance pricing signals; the gap between institutional cat-bond
> indices and the public proxies that move with them; and how a
> hurricane-season trader reads the tape when a system enters the
> basin.

Add ~30-word bios per panelist if the registration platform supports
it. Anchors credibility for registrants who don't recognize the names.

A topical note: the original draft mentions "real-time cat model
output." The dashboard intentionally does *not* run a loss model
(CLIMADA Panel 7 is Phase 2, post-launch). Don't promise modeling
that isn't shipping. The revised description quietly drops that line
and lets Vijay carry the modeling angle from his firm's perspective.

### Pre-session dashboard polish window — Mon–Wed June 8–10

Tighter than originally planned: the move from June 18 to June 11
collapses the polish window from a full week to roughly three days
(Monday June 8 through Wednesday June 11 morning), and it overlaps
with the tail end of the post-launch first week. Use this window for
low-risk UX tweaks ONLY — anything that would otherwise wait gets
deferred until after the webinar.

- [ ] Captions and tooltips that the soft-launch + first-week feedback
      flagged as ambiguous.
- [ ] Any obvious empty-state copy that reads weird with a quiet
      pre-peak-season basin.
- [ ] Verify all four ingest jobs are still healthy in Render logs;
      yfinance Yahoo-endpoint changes are the most likely silent
      breakage between launch and June 11.
- [ ] Run the share-card validators one more time
      ([Twitter](https://cards-dev.twitter.com/validator),
      [Facebook](https://developers.facebook.com/tools/debug/)) — if
      anyone has shared the URL since launch, the cards are cached and
      "Preview card" forces a refresh.
- [ ] **Hard rule: no new features this week, no exceptions.** With
      only three days between Mon-AM and Wed-PM, a regression has
      almost no time to surface before the webinar. Polish only.
      Anything bigger waits until after.

### Three-stage registration leverage

Registrations are the durable asset, not the live event. A focused
industry webinar in this demographic typically pulls 100–300 sign-ups
— a permission-based mailing list that's pre-qualified by the act of
signing up. Three stages:

**T-3 days (~Sunday June 8) — pre-event email to all registrants.**

> Looking forward to seeing you Wednesday. We'll be working live from
> the RMN Hurricane Dashboard during the session — bookmark
> [hurricane.riskmarketnews.com](https://hurricane.riskmarketnews.com)
> and take a look beforehand. Two minutes of familiarity makes the
> conversation easier to follow.

Drives traffic + warms registrants up. Bonus: surfaces dashboard bugs
from a wider audience three days before the live event, which you can
fix in the polish window.

**Day-of (~Wednesday June 11, 2pm) — choreography during the session.**

- [ ] Pre-arrange with Vijay and Patrick which panels each will riff
      off (mapping above). A soft "hey Vijay, what does Panel 3 look
      like to you right now?" anchors the visual without scripting
      practitioners into stiffness.
- [ ] Have the dashboard open in a browser tab on your screen.
      Screen-share when each panel comes up. Don't switch panels
      mid-sentence — let panelists drive the visual cadence.
- [ ] If a storm is active in the Atlantic on the day, lead with
      Panel 1 cold-open. Panel 1 + an experienced ILS practitioner is
      a documentary moment.
- [ ] Have a fallback if the live dashboard hiccups: a static
      screenshot of each panel queued in the slides, so a render
      stutter doesn't derail the segment.

**T+1 (~Thursday June 12) — follow-up email to all registrants.**

Send two things:

1. The session recording link.
2. A "what's changed on the dashboard since the session" hook
   pointing specifically at Panel 6 ("What changed today").

The session is one-shot; the dashboard is recurring. The follow-up
email is what converts a single attention moment into a habit.

**Recurring (weekly through Nov 30) — two-line nudge during hurricane
season.**

> This week on the dashboard: [most-active panel — usually Panel 1 if
> there's a storm, Panel 6 otherwise]. [Link]

Even at a 5–10% weekly click-through, this is the highest-leverage
marketing channel the dashboard will have during its first season.

### Post-webinar tasks

- [ ] **Capture the recording URL** in this doc so it lives somewhere
      durable (replace this checkbox with the link after publishing).
- [ ] **Pull registration data** into a single CSV — name, firm,
      email, signup timestamp. This is the asset; back it up.
- [ ] **Note dashboard moments** the panelists pointed at. Anything
      they questioned, validated, or improvised on becomes either
      copy/feature work or future episode hooks.
- [ ] **Update the Lessons section below** with anything the session
      surfaced about the dashboard's voice or panel mix.

### TBD — fill in before June 8

- [ ] Webinar platform (Zoom / Hopin / Streamyard / RMN-house?) —
      affects whether registration capture, recording, and follow-up
      automation are all in one place or need stitching.
- [ ] Time zone confirmation — "2pm" needs to be unambiguous on the
      registration page (presumed 2pm ET; confirm).
- [ ] Panelist bios for the registration page (~30 words each).
- [ ] Third panelist? Two is workable but three usually reads better
      on a registration page; an academic-side voice (NHC alum,
      Princeton GFDL, or similar) would round out the "science" leg.
- [ ] Pre-event email copy locked in (T-3 send) and follow-up email
      copy drafted (T+1 send) by the polish-week kickoff.

---

## Lessons (filled in post-launch)

_TBD — this section gets written the day after launch. Capture
anything that surprised you, anything that the soft-launch missed,
anything that the day-of pre-flight should have caught earlier._
