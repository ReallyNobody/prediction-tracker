# Post-Event Loss Estimates — Phase 2 Feasibility Memo

*Phase 2 scoping for an "Estimated losses" panel of the RMN Hurricane Dashboard.*
*Prepared April 29, 2026. Decision: build in mid-to-late July 2026, post-launch, ahead of peak season's first major event.*

---

## TL;DR

**The full models are gated.** RMS, Verisk Extreme Event Solutions (formerly AIR), and CoreLogic all license their catastrophe models commercially at enterprise pricing. None offer public APIs; full output sits behind six-figure annual contracts not viable for a public dashboard.

**The post-event press releases are usable.** All four major firms — Moody's RMS, Verisk Extreme Event Solutions, CoreLogic, Karen Clark & Company — publish post-landfall insured-loss range estimates as press releases on their own news rooms 1–7 days after a major event, with refinements over the following weeks. Headline numbers are facts (not copyrightable); short attributed quotes are fair use; multi-paragraph reproduction is not.

**Manual curation is the right tier for v1.** A `cat_loss_estimates.yaml` modeled on the existing `historical_analogs.yaml` lets us hand-add modeler estimates as press releases drop, surface them as a small panel, and backfill historical events trivially. Estimated build cost ~2 sessions. Scrapers earn their complexity later if curation becomes a bottleneck during a busy season.

**Editorial value lies in the gap.** The dashboard already shows pre-event *market* pricing of hurricane risk (cat bond ETF, KBWP, Kalshi). Modeler post-event estimates are the *model* pricing of the same event. Surfacing the spread between market expectations beforehand and modeler estimates afterwards is a frame no other public dashboard offers, and slots naturally next to Panel 5 (Historical analogs).

**Timing: post-launch Phase 2.** The panel would be empty or stale through the dashboard's first weeks. June 1 launch and June 11 webinar are both pre-peak-season; first majors typically don't land until late August / early September. Better to ship in July than to stretch launch scope.

---

## Full findings

### Moody's RMS — primary modeler source

Moody's RMS (the rebranded Risk Management Solutions, now under Moody's Insurance Solutions) is the most-cited catastrophe modeler in industry press. Their hurricane response cadence is consistent: a press release with insured-loss range estimates within 1–7 days of landfall, followed by refined estimates as on-the-ground data accumulates.

Recent examples drawn from RMS public communications:

- Hurricane Ian (Sept 2022): initial estimate $53–74B, refined to $50–65B
- Hurricane Helene (Sept 2024): initial estimate $9–13B insured

Distribution is via rms.com news room and occasionally Moody's Analytics blog posts. No RSS feed; HTML scraping is feasible but the markup has changed at least twice since 2023.

**Use as a primary source.** Major reputational weight in the industry; their numbers are quoted by Bloomberg, Reuters, the Wall Street Journal in the days after every major event. Attribution is straightforward and required.

### Verisk Extreme Event Solutions — secondary primary source

Verisk's catastrophe modeling business (formerly AIR Worldwide, now branded Verisk Extreme Event Solutions / Verisk Insurance Solutions) follows the same publication cadence as RMS: a press release on verisk.com's newsroom 1–7 days post-landfall with insured-loss ranges, refined over the following weeks.

For most major events Verisk and RMS publish overlapping but non-identical ranges. The spread is the point — when both firms agree, that's the consensus estimate; when they diverge, that's the story.

**Use as a primary source alongside RMS.** Same editorial weight, same attribution requirements.

### CoreLogic Catastrophe Risk Management — secondary source

CoreLogic publishes post-event loss estimates with somewhat lower industry-press penetration than RMS or Verisk, but their numbers are still cited and add a third data point to the modeler-spread picture.

Distribution via corelogic.com / Insurance Solutions news pages.

**Use as a secondary source.** Always include when available; not always available — CoreLogic's release cadence is less consistent than the top two.

### Karen Clark & Company (KCC) — fourth source worth tracking

KCC is smaller than RMS or Verisk but produces post-event estimates promptly (often within 24–48 hours of landfall) and is journalistically respected. Their numbers tend to print at the lower end of the modeler spread, which provides useful editorial context — when KCC and RMS diverge meaningfully, that itself is signal.

Distribution via karenclarkandco.com news.

**Use as a fourth source.** Always include when available.

### PCS (Property Claim Services) — industry-canonical, gated

Property Claim Services (a Verisk subsidiary) publishes "industry insured loss" numbers that serve as the canonical benchmark for cat bond triggers and reinsurance contracts. PCS is subscription-gated, but their major-event headline numbers circulate via news outlets (Insurance Insider, Reinsurance News, Artemis) within hours of release.

**Use as an indirect source.** When a PCS number lands and is quoted in a public news article, capture it with attribution to both PCS and the secondary publication. Don't scrape PCS directly; surface the numbers when they enter the public conversation.

---

## Copyright and attribution boundaries

Press release content is copyrighted; numbers in those releases are facts and not copyrightable. The line we'll walk:

- **Acceptable**: structured data — modeler name, event name, low/high range, currency, publish date, source URL. Captions like *"Moody's RMS estimates Hurricane Foo insured losses between $40B and $60B"* are factual statements with attribution.
- **Acceptable**: a single short quote (under ~15 words, in quotation marks, attributed) per modeler per event.
- **Not acceptable**: reproducing multi-paragraph chunks of release prose, even with attribution. Not acceptable: reformatting the release as a "summary" close enough to be displacive of the original.

This is the same standard applied throughout the dashboard's content — we link to sources for context, not in place of them.

---

## Three implementation tiers

### Tier 1 — Manual curation (recommended for v1, ~2 sessions)

A YAML file `data/cat_loss_estimates.yaml` modeled on `data/historical_analogs.yaml`. Per major event:

```yaml
- event_name: Hurricane Helene
  year: 2024
  estimates:
    - modeler: Moody's RMS
      low_usd_billions: 9.0
      high_usd_billions: 13.0
      issued_at: 2024-10-04
      source_url: https://...
    - modeler: Verisk
      low_usd_billions: 9.5
      high_usd_billions: 15.5
      issued_at: 2024-10-07
      source_url: https://...
    - modeler: KCC
      low_usd_billions: 6.4
      high_usd_billions: 6.4
      issued_at: 2024-10-02
      source_url: https://...
```

Drives a panel that renders, per event, a small grouped readout: each modeler's range as a horizontal bar, plus the implicit consensus and dispersion. Off-season the panel shows the most recent event with completed estimates; post-event during an active major hurricane it shows the current event's incoming estimates as they roll in.

Build cost ~2 sessions: data shape + Pydantic loader + service layer + panel UI + rendering. Backfilling 2017–2024 majors (Harvey, Irma, Maria, Florence, Michael, Laura, Ida, Ian, Idalia, Helene, Milton) is a couple of evenings of curation work.

**Pros**: zero scraping fragility, full editorial control, attribution baked into the data shape, integrates with existing Panel 5 plumbing (the `insured_loss_usd_billions` field on `historical_analogs.yaml` already does ballpark loss-aggregate display).

**Cons**: 5–10 hand-edits per active season; Chris becomes a soft dependency during a busy week.

### Tier 2 — Press-release scraping (~3–4 sessions)

A scheduled job hitting RMS / Verisk / CoreLogic / KCC news pages on a daily cadence, regex-matching hurricane names + dollar ranges, persisting to a `LossEstimate` SQLAlchemy model. Same APScheduler plumbing as Kalshi / NHC / yfinance.

**Pros**: hands-off after build.

**Cons**: scraping these news rooms is structurally brittle (each firm restructures every 1–2 years); regex extraction of dollar ranges from prose has false-positive risk (e.g., picking up unrelated numbers from elsewhere in the release); attribution and source-URL preservation needs care; you'd want a human-review step anyway.

### Tier 3 — Hybrid (~5 sessions)

Tier 2's scraper feeds an editorial review queue: *"RMS just published a release mentioning Hurricane Foo with $X–Y range — confirm and publish?"* Same authorship pattern as the SEC EDGAR discovery job already deferred to post-launch. Chris (or any RMN editor) confirms before the row goes live.

**Pros**: best of both — automation surfaces, human approves.

**Cons**: most code surface area to maintain; review queue UI is itself work.

---

## Recommendation

### Build Tier 1 in mid-to-late July 2026

Three reasons:

1. **The launch and webinar windows can't carry this.** A panel that renders "no recent major events" through the dashboard's first ~10 weeks is a feature inviting "is this thing on?" reactions. Better to ship the panel pre-loaded with backfilled historical entries (Ian, Helene, Milton, Idalia, etc.) and a clear "most recent: [event]" framing, ready to switch to live mode on the next major.

2. **The webinar is a stronger venue to *signal* than to *demo*.** Vijay Manghnani consumes RMS / Verisk output professionally. Asking him on-stage how modelers' estimates evolve post-event is a credibility-anchored framing for a panel that will land in July; demoing an empty panel against him is the opposite.

3. **Tier 1's risk profile fits Phase 2.** Two sessions of work, no scraping fragility, full editorial control, easy to upgrade later. Investing in Tier 2 or 3 first is a bet that the editorial bottleneck will bite — better to upgrade *when felt* than to pre-invest.

### Editorial framing

The panel should narrate the gap, not just the numbers. Suggested heading: **"Modeled losses"** or **"Modelers' read."** Suggested caption template:

> *"Major event in last 60 days: Hurricane Foo (Sept 2026 landfall, FL). Three modelers have published insured-loss estimates: RMS $40–60B, Verisk $35–55B, KCC $30–42B. The market priced this storm at ~3% probability of >$50B insured loss in the days before landfall (per Kalshi); the consensus modeler midpoint puts it at ~$45B. The gap between market expectations beforehand and modeler estimates afterwards is the running story of every major hurricane."*

That framing positions the panel as the dashboard's "after the storm" complement to the existing "before and during" panels, and ties it directly to the market-pricing thesis the rest of the dashboard already advances.

---

## Open questions (non-blocking)

1. **Do we want to compute a "consensus midpoint" automatically?** Probably yes — average of modeler midpoints, with a small "n=3" or "n=4" annotation to keep readers aware that the average is over only a few sources. Defer the methodology decision to build week.
2. **Backfill depth?** Recommend 2017 through current as the launch dataset (Harvey forward — about 11 majors). Earlier than 2017 the modelers' methodologies have drifted enough that direct comparison is iffy.
3. **Should we eventually pay for PCS access?** Revisit alongside the Artemis question if the dashboard generates revenue. The PCS subscription cost is in the same order of magnitude as Artemis; both become defensible content investments only if traffic and editorial reputation justify them.
4. **Panel placement?** Most natural fit is a new Panel 7 below Panel 5 (Historical analogs), or as an expansion of Panel 5 (the analogs YAML already has `insured_loss_usd_billions` for past storms — modeler estimates are the live-event analog of that field). Decide at build time.

---

*Memo status: feasibility complete; recommendation flagged; build deferred to Phase 2 (mid-to-late July 2026) per the open punch list. Revisit at the post-launch first-week retrospective to confirm the build slot.*
