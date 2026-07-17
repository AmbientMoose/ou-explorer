# IEEE OU Explorer — Handoff Notes

Maintainer-facing notes on how the app is built, deployed, and where the bodies
are buried. For end-user usage, see [README.md](README.md).

## What it is

A Streamlit web app for browsing the parent/child structure of IEEE
Organizational Units (OUs) — Regions, Councils, Areas, Sections, Sub-sections,
Chapters, Affinity Groups, Student Branches/Chapters, Societies/Technical
Councils, Divisions, etc. — built on the public vTools APIs.

- **Live app:** https://ou-explore.streamlit.app/
- **Owner:** Chris Gunning
- **License:** Apache 2.0

## Repositories & deployment

- **Canonical (IEEE):** GitLab — https://opensource.ieee.org/vtoolslabs/ou-explorer (remote `origin`, fetch)
- **Mirror (for Streamlit):** GitHub — https://github.com/AmbientMoose/ou-explorer (remote `github`)
- `origin` is configured with **two push URLs** (GitLab + GitHub), so a single
  `git push` (or `git push origin main`) updates both. Confirm with `git remote -v`.
- **Streamlit Community Cloud deploys from the GitHub mirror** (Streamlit Cloud
  does not support GitLab). Pushing to `main` triggers an auto-redeploy.
  Changing `requirements.txt` triggers a slower full environment rebuild; if a
  deploy errors, use **Reboot** from the app's ⋮ menu.
- No secrets/credentials needed — all APIs are public.

## Files

| File | Purpose |
| ---- | ------- |
| `streamlit_app.py` | The app: sidebar controls, page layout, search, downloads, deep links. |
| `ouclient.py` | Clients for the OU List API (JSON) and WebInABox Unit Details feed (officers, XML via stdlib). `OU` dataclass. |
| `outype.py` | Unit-type classification (by API type-description, and by SPOID prefix) + per-type colour/emoji/shape. |
| `report.py` | Renders the current view to downloadable Text / JSON / PDF (PDF via reportlab). |
| `build_index.py` | Offline crawler → `units.csv` (name-search index). |
| `check_reciprocity.py` | Offline audit → `reciprocity_violations.csv` (supplement + report). |
| `units.csv` | Committed snapshot: spoid, name, type of every **active** unit (~22.5k rows, ~1.3 MB). |
| `reciprocity_violations.csv` | Committed snapshot: parent/child edges the OU List API omits (~9.6k rows, ~1.5 MB). |

## Architecture / data flow

Navigating to a unit costs **one OU List API call** — for that unit only. Its
parents'/children's names and types are resolved from the committed `units.csv`
index (no per-neighbour API calls), which is what makes big societies load fast.

1. **Current unit** fetched via OU List API (`ouclient.get_ou`), cached with
   `@st.cache_data`. Gives its `parent-spoids` / `child-spoids`.
2. **Supplement**: `reciprocity_violations.csv` adds parent/child edges the API
   omits (its `child-spoids` are badly incomplete for societies/sections). In
   the live app supplemented rows are marked with a dagger (`†`).
3. **Neighbour resolution**: each listed parent/child's name/type comes from
   `units.csv`. A SPOID **absent from `units.csv` is treated as inactive and
   dropped** from the lists.
4. **Officers** come from the WebInABox Unit Details feed (`get_officers`).
5. **Search** (`streamlit-searchbox`) filters `units.csv` names live (≥3 chars).
6. **Deep links**: `?ou=<SPOID>` selects a unit on load; the URL stays in sync
   as you navigate.
7. **Downloads** (`report.py`): Text / JSON / PDF of the current view; PDF links
   are clickable app deep links (`?ou=<SPOID>`).

Page layout order: **unit info → parents → children → officers → data sources**.

## Regenerating the committed data snapshots

Both CSVs are **point-in-time snapshots** and go stale as IEEE data changes.
Re-run and commit periodically (they crawl the whole graph, ~2 min each,
following both `child-spoids` and `parent-spoids` from the ten Regions):

```bash
python build_index.py --out units.csv
python check_reciprocity.py --out reciprocity_violations.csv
git add units.csv reciprocity_violations.csv && git commit && git push
```

Keep them regenerated **together** so the supplement edges and the name index
stay consistent (the app resolves supplemented units' names from `units.csv`).

## Key decisions & gotchas

- **No live reciprocity ⚠️ flag.** Because neighbours aren't fetched, the app
  can't verify relationships live; the reciprocity CSV supplies the known-bad
  edges as a *supplement* instead. `check_reciprocity.py` still exists to audit
  and regenerate that data.
- **R0/R10 alias.** Region 10's OU List data lives under `R0`; `resolve_spoid()`
  maps `R10`→`R0`, and the two are treated as the same unit everywhere.
- **Streamlit Cloud has an ephemeral filesystem** — caching uses `@st.cache_data`
  (in-memory), never on-disk files.
- **Sidebar width** is forced to 420px only at `min-width: 768px` (desktop) via a
  CSS media query, so mobile keeps Streamlit's responsive default.
- **PDF** uses reportlab's bundled Vera font (good Latin/accent coverage, no
  extra font file).
- **`.gitignore`** excludes `reciprocity_violations.xlsx` and
  `check_reciprocity.py.txt` — those are local email attachments, not source.
- **Type classification** prefers the API `type-description`; SPOID-prefix rules
  are a fallback (`outype.classify_spoid`).

## Upstream data issue (context)

The OU List API's `child-spoids` lists are **incomplete** — e.g. IEEE Computer
Society (`C016`) returns ~120 of its ~1,090 chapters. This is a known upstream
vTools data-cache defect raised with the vTools lead developer (Jesse Mueller);
as of the last exchange it was on the backlog without a firm date. The Explorer
works around it with the reciprocity supplement. Separately, the sibling
`ieee-activity-report` tool still uses the older **WebInABox Other Units** API
for parent/child data until the OU List API is fixed.

## Testing / verification notes

- Logic is verified with Streamlit's `AppTest` harness (`streamlit.testing`) and
  direct function calls; PDFs are checked with `pypdf` (link annotations, text).
- The in-tool browser preview's **screenshots hang in this environment**, so
  visual checks were done via DOM measurement (`javascript_tool`) rather than
  screenshots. Worth a manual click-through after deploys.

## Possible next steps

- Automate periodic regeneration of `units.csv` / `reciprocity_violations.csv`
  (e.g. a scheduled job) so the snapshots don't drift.
- Retire the reciprocity supplement once the OU List API `child-spoids` are
  fixed upstream.
- Optionally include Societies/Divisions more prominently in search/among
  known types if requested.
