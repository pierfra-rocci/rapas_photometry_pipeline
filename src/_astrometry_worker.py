"""Internal subprocess entry point for bounded stdpipe plate solving."""

from __future__ import annotations

import argparse
import json

from astropy.table import Table
from stdpipe import astrometry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources")
    parser.add_argument("options")
    parser.add_argument("workdir")
    parser.add_argument("result")
    args = parser.parse_args()

    sources = Table.read(args.sources, format="fits")
    with open(args.options, encoding="utf-8") as handle:
        options = json.load(handle)
    solution = astrometry.blind_match_objects(sources, _workdir=args.workdir, **options)
    if solution is not None:
        solution.to_fits(relax=True).writeto(args.result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
