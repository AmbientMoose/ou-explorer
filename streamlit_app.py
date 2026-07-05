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
import logging

import streamlit as st
import urllib3

import ouclient
import outype
from outype import UnitType

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


def self_ids(spoid):
    """Identifiers that count as 'this unit', including the R0/R10 alias."""
    ids = {spoid}
    if spoid in ("R0", "R10"):
        ids |= {"R0", "R10"}
    return ids


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


def reciprocity_flag(spoid, ou_cache, current_ids, relation):
    """Return a warning message if the relationship isn't mutual, else None.

    'parent' relation: the parent should list the current unit as a child.
    'child'  relation: the child should list the current unit as a parent.
    Units we never fetched (SPOID-only codes) can't be verified -> no flag.
    """
    ou = ou_cache.get(spoid)
    if ou is None:  # None (no data) or never queried -> cannot verify
        return None
    if relation == "parent" and current_ids.isdisjoint(ou.children):
        return ("This parent does not list the current unit among its "
                "children.")
    if relation == "child" and current_ids.isdisjoint(ou.parents):
        return ("This child does not list the current unit among its "
                "parents.")
    return None


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


def init_state():
    if "current" not in st.session_state:
        st.session_state.current = None
        st.session_state.ou_cache = {}
        st.session_state.view = "main"


def fetch_ous(spoids):
    """Fetch full OUs for fetchable SPOIDs concurrently, cached per session.

    The full OU is kept (not just its name) so each listed unit's own
    parent/child lists are available for the reciprocity check.
    """
    cache = st.session_state.ou_cache
    todo = [s for s in dict.fromkeys(spoids)
            if name_fetchable(s) and s not in cache]
    if not todo:
        return cache
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = pool.map(lambda s: ouclient.get_ou(s, http=_HTTP), todo)
        for spoid, ou in zip(todo, results):
            # None => API returned no data (unit is dropped from the lists).
            cache[spoid] = ou
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


def nav_row(spoid, unit_type, name, sort_by, flag, key):
    """Render one compact, clickable parent/child row.

    Label order follows the sort: "(SPOID) Name" when sorted by SPOID,
    "Name (SPOID)" when sorted by unit name. A leading emoji marks the type
    (see the sidebar legend). Rendered as a borderless (tertiary) button so
    rows are single-line and single-spaced rather than boxed. A trailing
    warning marks a non-reciprocal relationship (see reciprocity_flag).
    """
    _colour, _shape, emoji, _size = outype.style_for(unit_type)
    if name:
        text = f"({spoid}) {name}" if sort_by == "SPOID" else f"{name} ({spoid})"
    else:
        text = f"({spoid})"
    label = f"{emoji} {text}"
    if flag:
        label += " ⚠️"
    st.button(label, key=key, type="tertiary", use_container_width=False,
              on_click=go_to, args=(spoid,), help=flag or None)


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


def render_unit_list(title, spoids, hints, ou_cache, current_ids, relation,
                     visible_types, sort_by, key_prefix):
    st.subheader(title)
    # Includable = real (has data) and active; of those, some may be hidden
    # solely because their type is not in the current filter.
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
        for i, spoid in enumerate(shown):
            flag = reciprocity_flag(spoid, ou_cache, current_ids, relation)
            nav_row(spoid, type_for(spoid, hints, ou_cache),
                    name_of(spoid, ou_cache), sort_by, flag,
                    key=f"{key_prefix}_{i}_{spoid}")
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
    div[data-testid="stButton"] > button[kind="tertiary"] {
        padding: 0.05rem 0.2rem;
        min-height: 0;
        line-height: 1.35;
        text-align: left;
        justify-content: flex-start;
    }
    div[data-testid="stElementContainer"]:has(> div[data-testid="stButton"]
        > button[kind="tertiary"]) {
        margin-top: -0.55rem;
        margin-bottom: -0.55rem;
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
        st.session_state.ou_cache = {}

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
            "**Load** to begin.")
else:
    ou = load_ou(resolve_spoid(current))
    if ou is None:
        st.error(f"No OU found for SPOID '{current}'.")
    elif st.session_state.view == "officers":
        render_officers_page(ou)
    else:
        hints = hint_map(ou)
        with st.spinner("Loading unit names..."):
            ou_cache = fetch_ous(ou.parents + ou.children)
        current_ids = self_ids(ou.spoid)

        render_unit_list("Parents", ou.parents, hints, ou_cache, current_ids,
                         "parent", visible_types, sort_by, "par")
        render_selected(ou)
        render_unit_list(f"Children ({len(ou.children)})", ou.children, hints,
                         ou_cache, current_ids, "child", visible_types,
                         sort_by, "chi")
