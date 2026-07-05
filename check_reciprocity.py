"""Report non-reciprocal parent/child relationships in the IEEE OU graph.

Crawls the OU graph (BFS from the ten Regions, following both child-spoids and
parent-spoids, like build_index.py) and keeps each unit's full parent/child
lists. It then reports, for every ACTIVE unit whose related unit is also
ACTIVE:

  * a parent that does not list the unit among its children, and
  * a child that does not list the unit among its parents.

This is the per-unit reciprocity check from the app, applied in bulk. R0/R10
are treated as the same unit, and a related unit that wasn't fetched (or
returned no data) is skipped -- you can't verify a list you don't have.

Usage:
    python check_reciprocity.py [--out reciprocity_violations.csv] [--workers 40]
"""

import argparse
import concurrent.futures
import csv
import logging
import time

import ouclient

REGIONS = ["R0" if i == 10 else f"R{i}" for i in range(1, 11)]


def _norm(spoid):
    """Normalize the Region 10 alias so R0/R10 compare equal."""
    return "R0" if spoid == "R10" else spoid


def _active(ou):
    return ou is not None and (ou.status_desc or "").lower() == "active"


def crawl_graph(workers):
    """BFS the OU graph (children + parents); return {spoid: OU}."""
    http = ouclient.urllib3.PoolManager(maxsize=workers)

    def fetch(spoid):
        return ouclient.get_ou(spoid, http=http)

    graph = {}
    visited = set(REGIONS)
    frontier = list(REGIONS)
    start = time.time()
    depth = 0

    while frontier:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            ous = list(pool.map(fetch, frontier))
        nxt = []
        for ou in ous:
            if ou is None:
                continue
            graph[ou.spoid] = ou
            for neighbour in ou.children + ou.parents:
                neighbour = _norm(neighbour)
                if neighbour and neighbour not in visited:
                    visited.add(neighbour)
                    nxt.append(neighbour)
        logging.info("depth %d: %d fetched (%d total), %d queued, %.0fs",
                     depth, len(ous), len(graph), len(nxt), time.time() - start)
        depth += 1
        frontier = nxt

    logging.info("Crawl done: %d units, %.0fs", len(graph), time.time() - start)
    return graph


def find_violations(graph):
    """Active-only non-reciprocal relationships, one row per broken claim."""
    rows = []
    for spoid, ou in graph.items():
        if not _active(ou):
            continue
        me = _norm(spoid)

        # Requirement 1: a parent that doesn't list this unit as a child.
        for parent_spoid in ou.parents:
            parent = graph.get(_norm(parent_spoid))
            if not _active(parent):
                continue
            if me not in {_norm(c) for c in parent.children}:
                rows.append(_row("parent does not list unit as child",
                                 ou, parent))

        # Requirement 2: a child that doesn't list this unit as a parent.
        for child_spoid in ou.children:
            child = graph.get(_norm(child_spoid))
            if not _active(child):
                continue
            if me not in {_norm(p) for p in child.parents}:
                rows.append(_row("child does not list unit as parent",
                                 ou, child))

    rows.sort(key=lambda r: (r["unit_name"] or "").lower())
    return rows


def _row(issue, unit, related):
    return {
        "issue": issue,
        "unit_spoid": unit.spoid,
        "unit_name": unit.name,
        "unit_type": unit.type_desc,
        "unit_status": unit.status_desc,
        "related_spoid": related.spoid,
        "related_name": related.name,
        "related_type": related.type_desc,
        "related_status": related.status_desc,
    }


FIELDS = ["issue", "unit_spoid", "unit_name", "unit_type", "unit_status",
          "related_spoid", "related_name", "related_type", "related_status"]


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    logging.info("Wrote %d violations to %s", len(rows), path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="reciprocity_violations.csv")
    parser.add_argument("--workers", type=int, default=40)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    graph = crawl_graph(args.workers)
    rows = find_violations(graph)
    parents = sum(1 for r in rows if r["issue"].startswith("parent"))
    children = len(rows) - parents
    logging.info("Violations: %d total (%d parent-not-listing, "
                 "%d child-not-listing)", len(rows), parents, children)
    write_csv(rows, args.out)


if __name__ == "__main__":
    main()
