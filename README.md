# IEEE OU Explorer

An interactive web app for exploring the parent/child structure of IEEE
**Organizational Units (OUs)** -- Regions, Councils, Sections, Sub-sections,
Chapters, Affinity Groups, Student Branches, Student Branch Chapters, and the
Societies / Technical Councils that chapters belong to.

Start from a Region (e.g. Region 6) and drill down: see the councils and
sections in it, and the chapters, affinity groups, student branches, and
student branch chapters in each section. Click a chapter to see which
**section(s)** and **society(ies)** it belongs to. Joint chapters belong to
more than one section or society, so they list **multiple parents** -- the OU
hierarchy is a directed graph, not a tree.

For the selected OU the app shows, top to bottom: its **parents** (one per
line), the **OU itself**, then its **children** (one per line). Every parent
and child is a button -- click it to navigate there.

## How it works

Everything is driven by the public **OU List API**:

```
GET https://vtools.vtools.ieee.org/api/public/v1/ous/list?spoid=<SPOID>
```

A single call returns, for one OU, both its `parent-spoids` and `child-spoids`
plus type/status metadata and precomputed region/section/society/division
ancestry. Navigating to an OU costs one API call for that OU, plus one cached
call per listed parent/child to resolve its name (see below). Responses are
cached in memory for the session.

Each parent/child row shows an emoji + SPOID + name, where the emoji encodes
the unit type (see the sidebar legend).

Officer rosters come from the public **WebInABox Unit Details** feed
(`https://webinabox.vtools.ieee.org/wibp_officers/feed/<SPOID>`), parsed with
the Python standard library (no extra dependency).

## Running locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501).

## Deploying to Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. In Streamlit Community Cloud, create an app pointing at `streamlit_app.py`.
3. No secrets or credentials are needed -- the OU List API is fully public.

## Usage tips

- **Start:** pick a Region from the dropdown, or type any SPOID (e.g. `R60007`
  for the Boise Section, `CH06198` for a Computer Society chapter) and press
  **Load** or hit Return. Picking a Region clears any SPOID you typed and loads
  the Region.
- **Navigate:** click any parent or child row to move to that unit.
- **Reciprocity flag (⚠️):** a row is flagged when the relationship isn't
  mutual -- a listed parent that doesn't list the current unit among its
  children, or a listed child that doesn't list it among its parents. Hover the
  row for the reason. (SPOID-only codes that are never queried can't be
  checked, so they're never flagged.)
- **Filter:** use *Show unit types* in the sidebar to hide clutter (e.g. show
  only Sections and Chapters) -- handy because a full Region has hundreds of
  child units. "Other" and "Grouping" units are hidden by default. When units
  are hidden, each list notes how many and of which types, e.g. "94 units
  hidden by the type filter (88 groupings, 6 other)."
- **Details:** the boxed middle section shows the selected unit's name, type,
  website, its societies/sections/regions/divisions, and -- when the unit
  publishes one -- an **Officers** link that opens a formatted roster of the
  unit's officers (position and name).

## Files

| File               | Purpose                                                     |
| ------------------ | ----------------------------------------------------------- |
| `streamlit_app.py` | Streamlit UI: sidebar controls, parent/self/child list view |
| `ouclient.py`      | OU List API client and response parsing (`OU` dataclass)    |
| `outype.py`        | Unit-type classification and per-type colour/shape/emoji    |

## Notes and known quirks

- **Cycles / bad data:** some Regions (notably R5, R8, R10) have loops in the
  IEEE OU relationship data. The explorer tracks which nodes have been expanded
  and never re-expands one, so a cycle can't send it into an infinite loop.
- **Region 10:** its OU List data lives under `R0` rather than `R10`, so
  selecting `R10` automatically looks up `R0`. The two codes are treated as the
  same unit for the reciprocity check.
- **Other SPOIDs:** a handful of units have a type that doesn't match a known
  category. They fall back to a neutral "Other" type (hidden by default in the
  filter). Known types include Region, Council, Zone, Area, Section,
  Sub-section, Chapter, Affinity Group, Student Branch (+ Chapter/Affinity),
  Society / Technical Council, Division, and Grouping.
- **Names not fetched for some codes:** SPOIDs that begin with "A" followed by
  a digit (legacy affinity codes like `A2249`) or digits followed by a dash
  (like `1-877HNR2`) are shown as SPOID only -- their names are not looked up.
- **No-data units dropped:** a listed parent/child whose OU List API response
  contains no data is omitted from the lists. (The SPOID-only codes above are
  never queried, so they are kept.)
- **Inactive units dropped:** a listed parent/child whose status-description is
  "Inactive" is omitted from the lists.

## License

See [LICENSE](LICENSE).
