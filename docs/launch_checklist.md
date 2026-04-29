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
- [ ] **DNS for `riskmarketnews.com`.** Already pointing at Render, or
      still on the launch-week TODO list? The OG meta tags hardcode
      `https://riskmarketnews.com`, so the canonical domain has to
      resolve before share-card previews work end-to-end.
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
> URL: https://riskmarketnews.com
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
- [ ] `curl https://riskmarketnews.com/healthz` → `{"status": "ok"}`.
- [ ] Hit the live URL in **three contexts**:
  - [ ] iPhone Safari (real device, not simulator)
  - [ ] Desktop incognito (no cached assets)
  - [ ] [Twitter Card Validator](https://cards-dev.twitter.com/validator)
        + [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/) —
        confirm the OG image renders as a `summary_large_image` card.
- [ ] Eyeball the panel layout. All seven panels render: Active storms,
      Markets on it, Landfall probability, Companies on the line,
      Hurricane risk capital, Historical analogs, What changed today.
- [ ] Render logs show recent successful ingest ticks (yfinance the
      cleanest signal; should be persisting ~37 rows every 15 min).

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

## Lessons (filled in post-launch)

_TBD — this section gets written the day after launch. Capture
anything that surprised you, anything that the soft-launch missed,
anything that the day-of pre-flight should have caught earlier._
