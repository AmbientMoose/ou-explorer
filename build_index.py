"""Build the unit-name search index for the IEEE OU Explorer.

Crawls the IEEE Organizational Unit graph via the public OU List API, starting
from the ten Regions and doing a breadth-first traversal that follows BOTH
child-spoids and parent-spoids. This reaches the geographic hierarchy plus the
Societies, Technical Councils, and Divisions that sit above chapters. Every
unit whose status is "Active" is written to a CSV of spoid, name, type so the
app can offer instant name search without hitting the API at run time.

Usage:
    python build_index.py [--out units.csv] [--workers 40] [--max-units N]

Re-run it whenever you want to refresh the index, then commit the CSV.
"""

import argparse
import concurrent.futures
import csv
import logging
import time

import ouclient

REGIONS = ["R0" if i == 10 else f"R{i}" for i in range(1, 11)]


def _norm(spoid):
    """Normalize the Region 10 alias so R0/R10 aren't crawled twice."""
    return "R0" if spoid == "R10" else spoid


def crawl(workers, max_units):
    """BFS the OU graph (children + parents); return {spoid: (name, type)}."""
    http = ouclient.urllib3.PoolManager(maxsize=workers)

    def fetch(spoid):
        return ouclient.get_ou(spoid, http=http)

    visited = set(REGIONS)
    frontier = list(REGIONS)
    active = {}          # spoid -> (name, type_desc); active units only
    fetched = 0
    start = time.time()

    depth = 0
    while frontier:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            ous = list(pool.map(fetch, frontier))

        nxt = []
        for ou in ous:
            fetched += 1
            if ou is None:
                continue
            if (ou.status_desc or "").lower() == "active":
                active[ou.spoid] = (ou.name, ou.type_desc)
            for neighbour in ou.children + ou.parents:
                neighbour = _norm(neighbour)
                if neighbour and neighbour not in visited:
                    visited.add(neighbour)
                    nxt.append(neighbour)

        logging.info("depth %d: fetched %d units (%d total), %d active, "
                     "%d queued, %.0fs elapsed", depth, len(ous), fetched,
                     len(active), len(nxt), time.time() - start)
        depth += 1

        if max_units and fetched >= max_units:
            logging.warning("Reached --max-units=%d; stopping early.",
                            max_units)
            break
        frontier = nxt

    logging.info("Crawl done: %d units fetched, %d active, %.0fs",
                 fetched, len(active), time.time() - start)
    return active


def write_csv(active, path):
    """Write the index sorted by name (stable diffs)."""
    rows = sorted(((sp, nm, td) for sp, (nm, td) in active.items()),
                  key=lambda r: (r[1] or "").lower())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["spoid", "name", "type"])
        writer.writerows(rows)
    logging.info("Wrote %d active units to %s", len(rows), path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="units.csv", help="output CSV path")
    parser.add_argument("--workers", type=int, default=40,
                        help="concurrent requests")
    parser.add_argument("--max-units", type=int, default=0,
                        help="stop after fetching this many units (0 = no cap)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    active = crawl(args.workers, args.max_units)
    write_csv(active, args.out)


if __name__ == "__main__":
    main()
