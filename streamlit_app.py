"""IEEE OU Explorer -- navigate the Organizational Unit hierarchy as lists.

For the selected Organizational Unit (OU) the app shows, top to bottom:

* its parents, one per line,
* the OU itself,
* its children, one per line.

Every parent and child is a button: click it to navigate there. Joint chapters
that belong to multiple sections or societies simply list all of them under
Parents, because the OU structure is a directed graph, not a tree.

Names for the listed parents/children are fetched one OU List API call each
(cached), except for SPOIDs that begin with "A" followed by a digit, or digits
followed by a dash -- those legacy/affinity codes are shown as SPOID only.

Run locally:  streamlit run streamlit_app.py
Deploy:       point Streamlit Community Cloud at this file.
"""

import concurrent.futures
import csv
import logging
from pathlib import Path

import streamlit as st
import urllib3
from streamlit_searchbox import st_searchbox

import ouclient
import outype
from ouclient import OU
from outype import UnitType

# Pre-built name-search index (spoid,name,type of active units); see
# build_index.py. Lives next to this file so it works on Streamlit Cloud.
_INDEX_PATH = Path(__file__).parent / "units.csv"
_SEARCH_MIN_CHARS = 3
_SEARCH_LIMIT = 50

# Known parent/child edges the OU List API omits; used to supplement the API's
# relationship data. See check_reciprocity.py. A committed snapshot.
_RECIP_PATH = Path(__file__).parent / "reciprocity_violations.csv"
# Marks parent/child rows that came from the supplement rather than the API.
_SUPP_MARK = " †"  # dagger

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="IEEE OU Explorer", page_icon="🌐",
                   layout="centered")

# Concurrency for OU lookups. The shared connection pool is sized to the worker
# count so concurrent requests to the same host reuse connections instead of
# being discarded ("Connection pool is full" warnings).
_MAX_WORKERS = 8
_HTTP = urllib3.PoolManager(maxsize=_MAX_WORKERS)

# Sentinel: name cache miss (never queried) vs. cached None (queried, no data).
_MISSING = object()


def name_fetchable(spoid):
    # Every unit is fetched now (Academic codes like A8636 / 1-SG21PW carry real
    # university names worth resolving).
    return bool((spoid or "").strip())


def resolve_spoid(spoid):
    """Region 10's OU List data lives under 'R0'; look that up for 'R10'."""
    return "R0" if spoid == "R10" else spoid


@st.cache_data(show_spinner=False)
def load_supplements():
    """Load reciprocity_violations.csv into edge-supplement maps.

    Returns (extra_children, extra_parents, info):
      extra_children[parent_spoid] -> [child spoids the API omits]
      extra_parents[child_spoid]   -> [parent spoids the API omits]
      info[spoid]                  -> (name, type_desc, status) from the CSV,
                                      so supplemented units need not be fetched.
    SPOIDs are normalized for the R0/R10 alias.
    """
    extra_children, extra_parents, info = {}, {}, {}
    if not _RECIP_PATH.exists():
        return extra_children, extra_parents, info
    with open(_RECIP_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            unit = resolve_spoid((r.get("unit_spoid") or "").strip())
            related = resolve_spoid((r.get("related_spoid") or "").strip())
            issue = r.get("issue") or ""
            if not unit or not related:
                continue
            info.setdefault(unit, (r.get("unit_name", ""),
                                   r.get("unit_type", ""),
                                   r.get("unit_status", "")))
            info.setdefault(related, (r.get("related_name", ""),
                                      r.get("related_type", ""),
                                      r.get("related_status", "")))
            if issue.startswith("parent"):      # parent 'related' omits 'unit'
                extra_children.setdefault(related, [])
                if unit not in extra_children[related]:
                    extra_children[related].append(unit)
            elif issue.startswith("child"):      # child 'related' omits 'unit'
                extra_parents.setdefault(related, [])
                if unit not in extra_parents[related]:
                    extra_parents[related].append(unit)
    return extra_children, extra_parents, info


def supplement_ou(spoid):
    """Build a lightweight OU for a supplemented unit from CSV info, or None."""
    _ec, _ep, info = load_supplements()
    row = info.get(resolve_spoid(spoid))
    if row is None:
        return None
    name, type_desc, status = row
    return OU(spoid=spoid, name=name, type_desc=type_desc, status_desc=status)


def merge_supplement(base_spoids, extra_spoids):
    """Append supplement SPOIDs not already present; return (list, added_set).

    'added_set' holds the normalized SPOIDs that came from the supplement, so
    callers can mark those rows.
    """
    present = {resolve_spoid(s) for s in base_spoids}
    added = [s for s in extra_spoids if resolve_spoid(s) not in present]
    added_ids = {resolve_spoid(s) for s in added}
    return list(base_spoids) + added, added_ids


def name_of(spoid, ou_cache):
    ou = ou_cache.get(spoid)
    return ou.name if ou is not None else ""


def has_data(spoid, ou_cache):
    """Whether a listed unit should appear.

    A fetchable SPOID is dropped only if the OU List API returned no data for
    it (cached value is None). SPOIDs we deliberately never query are kept.
    """
    if not name_fetchable(spoid):
        return True
    return ou_cache.get(spoid, _MISSING) is not None


def is_inactive(spoid, ou_cache):
    """True if the fetched unit's status-description is 'Inactive'.

    Units we never queried have unknown status and are not treated as inactive.
    """
    ou = ou_cache.get(spoid)
    return ou is not None and (ou.status_desc or "").lower() == "inactive"


@st.cache_data(ttl=3600, show_spinner=False)
def load_ou(spoid):
    """Fetch a full OU, cached in-memory (Streamlit Cloud disk is ephemeral)."""
    return ouclient.get_ou(spoid)


@st.cache_data(ttl=3600, show_spinner=False)
def load_officers(spoid):
    """Fetch a unit's officer list (WebInABox), cached."""
    return ouclient.get_officers(spoid, http=_HTTP)


@st.cache_data(ttl=86400, show_spinner=False)
def region_names():
    """Map R1..R10 -> region name for the 'Jump to a Region' labels."""
    spoids = [f"R{i}" for i in range(1, 11)]
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = pool.map(
            lambda s: ouclient.get_ou(resolve_spoid(s), http=_HTTP), spoids)
        for spoid, ou in zip(spoids, results):
            out[spoid] = ou.name if ou is not None else ""
    return out


@st.cache_data(show_spinner=False)
def load_index():
    """Load the name-search index once, with names lowercased for matching."""
    rows = []
    if not _INDEX_PATH.exists():
        return rows
    with open(_INDEX_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            spoid = (r.get("spoid") or "").strip()
            name = (r.get("name") or "").strip()
            if not spoid or not name:
                continue
            type_desc = (r.get("type") or "").strip()
            _c, _s, emoji, _z = outype.style_for(
                outype.classify_ou(OU(spoid=spoid, type_desc=type_desc)))
            rows.append({"spoid": spoid, "name": name, "type": type_desc,
                         "name_lower": name.lower(),
                         "label": f"{emoji} {name} ({spoid})"})
    return rows


@st.cache_data(show_spinner=False)
def index_lookup():
    """Map (normalized) SPOID -> (name, type) for all active units in the index.

    Used to resolve a listed unit's name/type without an API call. A SPOID
    absent from the index is treated as inactive (dropped from the lists).
    """
    return {resolve_spoid(r["spoid"]): (r["name"], r["type"])
            for r in load_index()}


def neighbor_ou(spoid):
    """Lightweight OU for a listed unit from the index, or None if absent."""
    row = index_lookup().get(resolve_spoid(spoid))
    if row is None:
        return None
    return OU(spoid=spoid, name=row[0], type_desc=row[1], status_desc="Active")


def search_units(query):
    """st_searchbox callback: units whose name contains the query (>=3 chars).

    Prefix matches rank first, then alphabetical; capped at _SEARCH_LIMIT.
    Returns (label, spoid) tuples so selecting a result yields its SPOID.
    """
    q = (query or "").strip().lower()
    if len(q) < _SEARCH_MIN_CHARS:
        return []
    matches = [r for r in load_index() if q in r["name_lower"]]
    matches.sort(key=lambda r: (not r["name_lower"].startswith(q),
                                r["name_lower"]))
    return [(r["label"], r["spoid"]) for r in matches[:_SEARCH_LIMIT]]


def init_state():
    if "current" not in st.session_state:
        st.session_state.current = None
        st.session_state.view = "main"
        st.session_state.last_search = None


def neighbor_cache(spoids, supp_ids):
    """Resolve listed units' name/type without fetching.

    Supplemented units come from the reciprocity CSV; everything else from the
    units.csv index. A SPOID in neither is None -> treated as inactive and
    dropped from the lists.
    """
    cache = {}
    for spoid in dict.fromkeys(spoids):
        if resolve_spoid(spoid) in supp_ids:
            cache[spoid] = supplement_ou(spoid) or neighbor_ou(spoid)
        else:
            cache[spoid] = neighbor_ou(spoid)
    return cache


def hint_map(ou):
    """Classify an OU's parents from its ancestry shortcut fields."""
    hints = {}
    for sp in ou.region_spoids:
        hints[sp] = UnitType.REGION
    for sp in ou.section_spoids:
        hints.setdefault(sp, UnitType.SECTION)
    for sp in ou.society_spoids:
        hints[sp] = UnitType.SOCIETY
    for sp in ou.division_spoids:
        hints[sp] = UnitType.DIVISION
    return hints


def type_for(spoid, hints, ou_cache):
    """Best type for a listed unit.

    Prefer the fetched OU's own type-description (authoritative for Grouping,
    Area, Zone, Society, etc.); otherwise fall back to an ancestry hint, then
    to SPOID-prefix classification.
    """
    ou = ou_cache.get(spoid)
    if ou is not None:
        return outype.classify_ou(ou)
    return hints.get(spoid) or outype.classify_spoid(spoid)


def go_to(spoid):
    st.session_state.current = spoid
    st.session_state.view = "main"


def show_officers():
    st.session_state.view = "officers"


def show_main():
    st.session_state.view = "main"


def load_from_fields():
    """Load whichever 'Start here' field is set (typed SPOID wins over Region).

    Used by the Load button and as the on_change handler for both fields, so
    pressing Return in either field loads.
    """
    target = (st.session_state.get("spoid_input") or "").strip()
    target = target or st.session_state.get("region_select") or ""
    if target:
        st.session_state.current = target
        st.session_state.view = "main"


def on_region_change():
    """Selecting a Region clears any typed SPOID, then loads the Region."""
    if st.session_state.get("region_select"):
        st.session_state.spoid_input = ""
    load_from_fields()


def nav_row(spoid, unit_type, name, sort_by, supplemented, key):
    """Render one compact, clickable parent/child row.

    Label order follows the sort: "(SPOID) Name" when sorted by SPOID,
    "Name (SPOID)" when sorted by unit name. A leading emoji marks the type
    (see the sidebar legend). Rendered as a borderless (tertiary) button so
    rows are single-line and single-spaced rather than boxed. A trailing dagger
    marks a relationship added from the reciprocity supplement.
    """
    _colour, _shape, emoji, _size = outype.style_for(unit_type)
    if name:
        text = f"({spoid}) {name}" if sort_by == "SPOID" else f"{name} ({spoid})"
    else:
        text = f"({spoid})"
    label = f"{emoji} {text}"
    help_text = None
    if supplemented:
        label += _SUPP_MARK
        help_text = ("Added from reciprocity data; the OU List API does not "
                     "return this relationship.")
    st.button(label, key=key, type="tertiary", use_container_width=False,
              on_click=go_to, args=(spoid,), help=help_text)


def legend_markdown():
    rows = []
    for unit_type in UnitType:
        _c, _s, emoji, _z = outype.style_for(unit_type)
        rows.append(f"{emoji} {unit_type.value}")
    return "  \n".join(rows)


def sort_spoids(spoids, ou_cache, sort_by):
    """Sort SPOIDs by unit name or by SPOID.

    When sorting by name, SPOIDs with no fetched name fall back to their SPOID
    as the sort key so they still order sensibly.
    """
    if sort_by == "SPOID":
        return sorted(spoids, key=lambda s: s)
    return sorted(spoids, key=lambda s: (name_of(s, ou_cache) or s).lower())


def _plural(unit_type, count):
    """'88 groupings', '6 other', '1 chapter' for the hidden-count breakdown."""
    if unit_type is UnitType.UNKNOWN:  # "Other" has no plural form
        return f"{count} other"
    name = unit_type.value.lower()
    return f"{count} {name}" if count == 1 else f"{count} {name}s"


def hidden_breakdown(hidden_types):
    """Format a per-type breakdown, most numerous first, for the caption."""
    counts = {}
    for t in hidden_types:
        counts[t] = counts.get(t, 0) + 1
    order = list(UnitType)
    items = sorted(counts.items(), key=lambda kv: (-kv[1], order.index(kv[0])))
    return ", ".join(_plural(t, c) for t, c in items)


def render_unit_list(title, spoids, hints, ou_cache, visible_types, sort_by,
                     key_prefix, supp_ids=frozenset()):
    st.subheader(title)
    # Includable = present in the index/supplement (active); of those, some may
    # be hidden solely because their type is not in the current filter. A unit
    # absent from the index is treated as inactive and dropped (has_data False).
    candidates = [s for s in spoids
                  if has_data(s, ou_cache) and not is_inactive(s, ou_cache)]
    typed = [(s, type_for(s, hints, ou_cache)) for s in candidates]
    shown = [s for s, t in typed if t in visible_types]
    hidden_types = [t for s, t in typed if t not in visible_types]
    hidden = len(hidden_types)
    shown = sort_spoids(shown, ou_cache, sort_by)

    if not candidates:
        st.caption("None.")
    else:
        any_supp = False
        for i, spoid in enumerate(shown):
            supplemented = resolve_spoid(spoid) in supp_ids
            any_supp = any_supp or supplemented
            nav_row(spoid, type_for(spoid, hints, ou_cache),
                    name_of(spoid, ou_cache), sort_by, supplemented,
                    key=f"{key_prefix}_{i}_{spoid}")
        if any_supp:
            st.caption(f"{_SUPP_MARK.strip()} added from the reciprocity "
                       "supplement (not returned by the OU List API).")
        if hidden:
            word = "unit" if hidden == 1 else "units"
            st.caption(f"{hidden} {word} hidden by the type filter "
                       f"({hidden_breakdown(hidden_types)}).")


def render_selected(ou):
    unit_type = outype.classify_ou(ou)
    _c, _s, emoji, _z = outype.style_for(unit_type)
    with st.container(border=True):
        st.markdown(f"### {emoji} {ou.spoid}")
        if ou.name:
            st.markdown(f"**{ou.name}**")
        # Show the raw type-description in parentheses, but omit it when it
        # would just repeat the classified type (e.g. "Chapter (Chapter)").
        detail = ou.type_desc or ou.type_code or ""
        if detail and detail.lower() != unit_type.value.lower():
            st.write(f"Type: {unit_type.value} ({detail})")
        else:
            st.write(f"Type: {unit_type.value}")
        if ou.url:
            st.markdown(f"[Website]({ou.url})")
        api_url = f"{ouclient.OU_LIST_URL}?spoid={ou.spoid}"
        st.markdown(f"[OU List API]({api_url})")
        # Show an Officers link only when a published officer list exists.
        if load_officers(ou.spoid):
            st.button("Officers", type="tertiary", key="officers_link",
                      on_click=show_officers)
        extras = []
        if ou.society_spoids:
            extras.append("Societies: " + ", ".join(ou.society_spoids))
        if ou.section_spoids:
            extras.append("Sections: " + ", ".join(ou.section_spoids))
        if ou.region_spoids:
            extras.append("Regions: " + ", ".join(ou.region_spoids))
        if ou.division_spoids:
            extras.append("Divisions: " + ", ".join(ou.division_spoids))
        for line in extras:
            st.caption(line)


def render_officers_page(ou):
    """A dedicated page listing the selected unit's officers."""
    st.button("← Back", key="officers_back", on_click=show_main)
    unit_type = outype.classify_ou(ou)
    _c, _s, emoji, _z = outype.style_for(unit_type)
    st.header(f"{emoji} Officers")
    st.markdown(f"**{ou.name}**  \n`{ou.spoid}`")

    officers = load_officers(ou.spoid)
    if not officers:
        st.info("No officer list is available for this unit.")
        return
    st.table([{"Position": o["position"], "Name": o["name"]}
              for o in officers])


# --------------------------------------------------------------------------- #

init_state()

st.title("🌐 IEEE OU Explorer")
st.caption(
    "Navigate the parent/child structure of IEEE Organizational Units. "
    "Click any parent or child to move to it."
)

# Tighten the tertiary-button rows into a compact, single-spaced list.
st.markdown(
    """
    <style>
    /* On larger screens widen the sidebar so the Search-by-name prompt fits on
       one line and the Region/Council/Zone/Area filter chips fit on the first
       line. Left at Streamlit's responsive default on small/mobile screens so
       it doesn't overflow a narrow viewport. */
    @media (min-width: 768px) {
        section[data-testid="stSidebar"] {
            width: 420px !important;
            min-width: 420px !important;
        }
    }
    /* Descendant (not direct-child) combinators so rows with a help tooltip --
       which Streamlit wraps in stTooltipHoverTarget -- are tightened too. */
    div[data-testid="stButton"] button[kind="tertiary"] {
        padding: 0.05rem 0.2rem;
        min-height: 0;
        line-height: 1.35;
        text-align: left;
        justify-content: flex-start;
    }
    div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]
        button[kind="tertiary"]) {
        margin-top: -0.55rem;
        margin-bottom: -0.55rem;
    }
    /* Neutralize the tooltip wrapper's own spacing on these rows. */
    div[data-testid="stButton"] [data-testid="stTooltipHoverTarget"] {
        display: block;
    }
    /* Officers: style the button as a hyperlink so it's obviously clickable,
       matching the Website / OU List API links above it. */
    .st-key-officers_link { margin-top: 0 !important; margin-bottom: 0 !important; }
    .st-key-officers_link button[kind="tertiary"] {
        color: #00629B !important;
        text-decoration: underline !important;
        padding: 0 !important;
    }
    .st-key-officers_link button[kind="tertiary"]:hover { color: #004b75 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Start here")
    regions = [""] + [f"R{i}" for i in range(1, 11)]
    _rnames = region_names()

    def region_label(spoid):
        if not spoid:
            return "Select a Region..."
        name = _rnames.get(spoid, "")
        return f"({spoid}) {name}" if name else f"({spoid})"

    st.selectbox("Jump to a Region", regions, index=0, key="region_select",
                 on_change=on_region_change, format_func=region_label)
    st.text_input("...or enter any SPOID", key="spoid_input",
                  placeholder="e.g. R60007 or CH06198",
                  on_change=load_from_fields)
    col_a, col_b = st.columns(2)
    col_a.button("Load", type="primary", use_container_width=True,
                 on_click=load_from_fields)
    if col_b.button("Reset", use_container_width=True):
        st.session_state.current = None

    # Type-ahead search by unit name (>=3 letters), backed by units.csv.
    if load_index():
        picked = st_searchbox(search_units, key="unit_search",
                              placeholder="...or search by name (3+ letters)",
                              label="Search by name")
        if picked and picked != st.session_state.last_search:
            st.session_state.last_search = picked
            st.session_state.current = picked
            st.session_state.view = "main"

    st.divider()
    st.header("Filter")
    all_types = list(UnitType)
    # Hidden by default: "Other" (obsolete/typo SPOIDs), plus the numerous
    # Grouping and Academic units that clutter the lists. Enable as needed.
    hidden_by_default = {UnitType.UNKNOWN, UnitType.GROUPING,
                         UnitType.ACADEMIC}
    chosen = st.multiselect(
        "Show unit types",
        options=[t.value for t in all_types],
        default=[t.value for t in all_types if t not in hidden_by_default],
    )
    visible_types = {t for t in all_types if t.value in chosen}

    sort_by = st.radio("Sort lists by", ["Unit name", "SPOID"], index=0,
                       horizontal=True)

    st.divider()
    st.header("Legend")
    st.markdown(legend_markdown())

current = st.session_state.current

if not current:
    st.info("Pick a Region (or enter a SPOID) in the sidebar and press "
            "**Load** to begin, or use **Search by name** to find a unit.")
else:
    ou = load_ou(resolve_spoid(current))
    if ou is None:
        st.error(f"No OU found for SPOID '{current}'.")
    elif st.session_state.view == "officers":
        render_officers_page(ou)
    else:
        hints = hint_map(ou)
        # Supplement the API's parent/child lists with known-omitted edges.
        extra_children, extra_parents, _info = load_supplements()
        parents_list, parent_supp = merge_supplement(
            ou.parents, extra_parents.get(resolve_spoid(ou.spoid), []))
        children_list, child_supp = merge_supplement(
            ou.children, extra_children.get(resolve_spoid(ou.spoid), []))
        supp_all = parent_supp | child_supp

        # Resolve every listed unit's name/type from the index + supplement --
        # no per-neighbour API calls. Units absent from both are dropped.
        ou_cache = neighbor_cache(parents_list + children_list, supp_all)

        render_unit_list("Parents", parents_list, hints, ou_cache,
                         visible_types, sort_by, "par", parent_supp)
        render_selected(ou)
        render_unit_list(f"Children ({len(children_list)})", children_list,
                         hints, ou_cache, visible_types, sort_by, "chi",
                         child_supp)
