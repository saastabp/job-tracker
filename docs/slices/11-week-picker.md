# Slice 11 — Week picker for dashboard + outreach history (PLAN)

Status: planned, forks decided 2026-05-10, not yet started.
Branch: `slice/11-week-picker` (TBD).
Depends on slice 10 (`docs/slices/10-contact-email.md`) shipped.

Sequence note: originally next after slice 10 was to be slice 12 (AI
email draft). Slice 12 is shelved indefinitely; slice 11 now runs
ahead of slice 13 (tailored-PDF generation) — both will land on
develop in that order.

## Why this slice

The dashboard hardcodes `YEARWEEK(_, 1) = YEARWEEK(CURDATE(), 1)` for
all week-scoped metrics — there is no way to look at last week, or any
historical week, ever. The user surfaced the gap mid-slice-10
("I don't see a place to select what week I am looking at for
targets/outreach history") and explicitly deferred it. This slice
fills the gap.

Two related but separable surfaces:

1. **Week-scoped dashboard view.** Pick a week; all "today" / "this
   week" widgets recompute against that week's date range. "Today"
   only makes sense for the current week — when the user navigates to
   a past week, the today tiles flip to a "no day-of-the-week
   selected" mode (or just hide).

2. **Outreach history, weekly grouped.** A view of outreach events
   bucketed by week, scrollable backward/forward. Slice-10 added
   per-contact timelines but there is no aggregated cross-contact
   history view. This is what the user said they were looking for.

The first is a parameterization of an existing endpoint. The second
is a new view (probably its own page or a tab on the dashboard).
Slice can ship either separately or together; recommend together
because they share the same week-picker primitive.

## Locked decisions to carry in

- **Single source of truth for "the selected week"**: a query
  parameter (`?week_start=YYYY-MM-DD`) on whichever pages use it.
  URL-driven means refreshes / sharable links work, no need for
  client-side state to survive navigation.
- **Week starts Monday**, matching the existing `YEARWEEK(_, 1)`
  convention. No locale toggle.
- **UTC week boundaries**, matching the existing dashboard timezone
  posture. User-configurable timezone is still deferred; a week
  starts at UTC Monday 00:00.
- **Default = current week** when the param is absent.

## Forks (decided 2026-05-10)

Summary of decisions, in order:

| Fork | Decision |
|---|---|
| 1. Where the week-picker UI lives | **Dashboard + new "Outreach history" page** |
| 2. "Today" tile on past weeks | **Hide entirely** |
| 3. Submissions vs. outreach in history view | **Outreach only for v1** |
| 4. Pagination strategy | **One week at a time** |
| 5. Dashboard counter headers on past weeks | **Re-label dynamically** |

Detail and rationale on each below.

### Fork 1 — Where the week-picker UI lives

- *Dashboard only* — the navigation chevrons + date input live above
  the existing tiles. Selecting a week reloads the dashboard scoped
  to that range. No separate history page.
- *Dashboard + a new "Outreach history" page* — dashboard handles
  per-week metrics; a dedicated page lists outreach events bucketed
  by week with the same picker. Two surfaces, shared component.
- **Recommend Dashboard + new page**. The user asked specifically
  about an "outreach history" view, which the dashboard's
  metric-tile shape can't really render (counts, not events). A
  separate page can show a list of events with sender/contact/role
  per row, while the dashboard keeps its summary widget posture.

### Fork 2 — What the "Today" tile shows on past weeks

- *Hide it entirely* — when the selected week isn't the current week,
  collapse the "Today (date)" header + tile row.
- *Re-label as "First day of week"* — show the Monday's count;
  semantically dubious because the goals are daily and "first day"
  isn't a goal-relevant slice.
- *Show end-of-week summary* — replace the today tiles with the
  week's daily goal × 7 utilization or similar summary.
- **Recommend Hide it entirely**. Avoids semantic confusion;
  "today" only makes sense for the current week.

### Fork 3 — Submissions vs. outreach in the history view

- *Outreach only* (new page is "Outreach history") — events from
  `contact_outreach`, both directions.
- *All activity* (new page is "Activity history") — interleaved with
  submissions, follow-ups, responses.
- *Outreach + submissions, two columns* — keeps them separate but
  side-by-side in week buckets.
- **Recommend Outreach only for v1.** Matches the user's literal ask;
  smaller scope; submissions already have their own list view. If
  the user wants an interleaved feed later, that's a slice 11.5.

### Fork 4 — Pagination strategy

- *One week at a time* — picker chooses a week; page shows that
  week's events. Simple; obvious; chevrons advance/rewind by 1 week.
- *Infinite scroll backward* — week buckets render top-to-bottom,
  newest first; loading more weeks as the user scrolls.
- **Recommend One week at a time.** Matches the existing dashboard's
  visual rhythm. Infinite scroll is more code without much benefit
  at the user's data volume.

### Fork 5 — How "today" / "week" counters in the dashboard handle a past week

When the user picks a past week, the dashboard's submissions /
outreach widgets need to say "X submissions during week of
2026-04-13" rather than "X submissions today / this week."

- *Re-label headers dynamically* — "This week" → "Week of YYYY-MM-DD"
  when off-current-week. Today header hidden per Fork 2.
- *Always say "the selected week"* — even on the current week, the
  header reads "Week of …". Less context-sensitive copy but cleaner
  code.
- **Recommend Re-label dynamically.** Familiar UX.

## Proposed schema

No schema changes. The data already lives in `submissions`,
`contact_outreach`, `follow_ups`. The outreach history view is a
SELECT against existing tables.

## Proposed routes

| Method | Path | Change |
|---|---|---|
| `GET` | `/dashboard/today` | Accepts `?week_start=YYYY-MM-DD` (optional). Defaults to current week's Monday. Date math in SQL becomes a range against the param instead of `YEARWEEK = YEARWEEK(CURDATE())`. |
| `GET` | `/outreach/history` | **NEW**. Query: `?week_start=YYYY-MM-DD` (required, default current week). Returns: `{ week_start, events: [{ id, contact_id, contact_name, contact_kind, direction, method, outreach_at, subject?, body_text?, gmail_message_id?, role_title?, company_name? }, ...] }`. Joins `contact_outreach` → `contacts` → optional `submissions` (via thread). |

Naming: `/outreach/history` is consistent with the existing
`/dashboard/today` (resource/action pattern).

## Backend behavior

### `dashboard.py` — week-scoped date math

1. Read `week_start` from query string. Validate format
   `YYYY-MM-DD`; if absent, compute current week's Monday in UTC.
   400 on parse failure.
2. Compute `week_end = week_start + 6 days`.
3. Submissions count: replace
   `SUM(CASE WHEN YEARWEEK(submitted_on, 1) = YEARWEEK(CURDATE(), 1) ...)`
   with `SUM(CASE WHEN submitted_on BETWEEN %s AND %s ...)`.
4. Today count: only meaningful when `week_start` covers today; else
   set to 0 (or omit).
5. Outreach: similar BETWEEN replacement on `outreach_at`.
6. Follow-ups pending stays unchanged (it's a now-state, not a
   week-state).
7. Recent submissions list stays unchanged (it's "last 10," not
   week-scoped).
8. Response shape adds `week_start` (already there) and a new
   `is_current_week: bool` so the SPA can branch on header copy and
   today-tile visibility.

### New `outreach_history.py` handler

```sql
SELECT
    co.id, co.outreach_at, co.subject, co.body_text,
    co.gmail_message_id, co.notes,
    od.short_name AS direction,
    om.short_name AS method,
    c.id AS contact_id, c.name AS contact_name,
    ck.short_name AS contact_kind,
    s.id AS submission_id, s.role_title,
    comp.name AS company_name
FROM contact_outreach co
JOIN contacts c             ON c.id = co.contact_id AND c.deleted_at IS NULL
JOIN contact_kinds ck       ON ck.id = c.contact_kind_id
JOIN outreach_directions od ON od.id = co.outreach_direction_id
LEFT JOIN outreach_methods om ON om.id = co.outreach_method_id
LEFT JOIN submissions s     ON s.gmail_thread_id = co.gmail_thread_id
                            AND s.user_id = co.user_id
                            AND s.deleted_at IS NULL
LEFT JOIN companies comp    ON comp.id = s.company_id AND comp.deleted_at IS NULL
WHERE co.user_id = %s
  AND co.deleted_at IS NULL
  AND co.outreach_at >= %s
  AND co.outreach_at <  %s + INTERVAL 7 DAY
ORDER BY co.outreach_at DESC, co.id DESC
```

The `LEFT JOIN submissions s ON s.gmail_thread_id = co.gmail_thread_id`
brings the role/company context for email-sourced rows. Manually-logged
rows (no `gmail_thread_id`) just have NULL role / company.

### Catalog reuse

Reuse `common.users.get_user_id` and the existing dashboard pattern
of one connection per request.

## Frontend wiring

### New `WeekNav` component (`frontend/src/components/WeekNav.tsx`)

Shared by Dashboard and the new history page. Props:

```ts
interface Props {
  weekStart: string;           // YYYY-MM-DD
  onChange: (next: string) => void;
}
```

Renders three controls inline:
- ◀ Previous week button (subtracts 7 days, calls onChange)
- Date input pinned to a Monday (rounds whatever day picked back to
  its Monday before calling onChange)
- ▶ Next week button (adds 7 days). Disabled when next-week-Monday is
  in the future (no point looking past now).

Helper: `mondayOf(date: string): string` (computes the prior Monday
in UTC).

### `Dashboard.tsx`

- New URL param: `?week=YYYY-MM-DD`. Read on mount, default = current
  Monday.
- Mount the `WeekNav` above the today/week tile rows. On change,
  push the new param into URL via `setSearchParams` and re-fetch.
- Conditionally hide the "Today (...)" header + tile row when
  `is_current_week` is false. Re-label "This week" header to
  "Week of YYYY-MM-DD" when off-current-week.

### New `OutreachHistory.tsx` page

- Route: `/outreach`. Add to the SPA router.
- Top: `WeekNav`.
- Body: list of events from `/outreach/history?week_start=...`.
- Each row renders: direction arrow ↑/↓, contact name (linkable to
  contact detail), method badge, subject (or notes), date+time, role
  + company badge if linked to a submission. Same expand-collapse
  body pattern as ContactDetail timeline.
- Empty state: "No outreach in this week. Use ◀ to look earlier."
- Add a nav-bar entry for the new page.

## Test plan

### `test_dashboard.py` additions

- `?week_start=2026-04-13` returns counts using the BETWEEN range.
- `?week_start=` not provided defaults to current week.
- Bad date format → 400.
- `is_current_week` flag flips false on past weeks, true on the
  current week.
- Today counters = 0 (or omitted) on past weeks.

### `test_outreach_history.py` (new)

- Happy path: returns events in the requested week, sorted desc.
- Joins `contact_outreach` → `submissions` through `gmail_thread_id`,
  surfaces role/company when present.
- Manually-logged rows (no `gmail_thread_id`) come through with
  null role/company.
- Empty week → empty list.
- Bad date → 400.

### Manual end-to-end

- Dashboard: navigate ◀ a few weeks; counters reflect that week.
  ▶ to current week works; ▶ disabled when at current.
- Outreach history page: events from imported gmail thread show up
  with the right role/company link if a submission has the same
  thread linked.

## Out of scope (deferred)

- **Per-day breakdown within a week.** Recommend simple per-week
  totals for v1. A daily heatmap or stacked-bar chart can come later.
- **Per-contact-kind splits in history.** Filter dropdown for
  personal vs. recruiter.
- **CSV / JSON export of week data.** Defer until requested.
- **Timezone configuration.** Same as today: UTC weeks. A future
  slice can add a per-user timezone column on `users`.
- **Submissions and follow-ups in the history view.** Outreach only
  for v1 per Fork 3.

## Starter task list

1. ~~User decides forks 1–5.~~ Done 2026-05-10 (see Forks section above).
2. Backend: parameterize `dashboard.py` on `week_start`. Update tests.
3. Backend: new `outreach_history.py` handler + tests + api template
   route registration (`GET /outreach/history`).
4. Frontend: `WeekNav` component.
5. Frontend: `Dashboard.tsx` integrates `WeekNav` + URL param +
   conditional today-row visibility.
6. Frontend: new `OutreachHistory.tsx` page + router entry + nav
   item.
7. Manual end-to-end on the live deploy.
8. End-of-slice: write `docs/slices/13-tailored-pdf-generation.md`
   plan (slice 12 shelved; slice 13 is next).

## Resumption notes

- Slice 10 is shipped and verified end-to-end (compose, reply-from-
  contact, sync import, dashboard inbound counters).
- No schema changes in this slice; pure parameterization + new view.
  Deploy sequence is just `make deploy-api && make sync-frontend`.
- The week-start Monday computation lives in the SPA's `WeekNav`
  helper; backend just accepts the param. Keep both sides
  consistent (Monday-rounding) to avoid off-by-one.