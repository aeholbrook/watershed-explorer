"""How much of a storm runs off versus soaks in: NRCS curve-number method (TR-55).

Paved/roofed ground and pervious ground (lawn, garden) are computed separately and
then weighted by impervious fraction. That's more accurate than one composite curve
number for the small, frequent storms that make up most of the year's rain.
"""

from __future__ import annotations

import ast
import re

from .http import UpstreamError, fetch_text

NOAA_PF = "https://hdsc.nws.noaa.gov/cgi-bin/new/cgi_readH5.py"

# TR-55 Table 2-2a, open space in good condition (>75% grass cover), by hydrologic soil group.
PERVIOUS_CN = {"A": 39, "B": 61, "C": 74, "D": 80}
IMPERVIOUS_CN = 98
MM_PER_IN = 25.4

# Rows of NOAA Atlas 14 precipitation-frequency tables; columns are return periods.
PF_DURATIONS = ["5-min", "10-min", "15-min", "30-min", "60-min", "2-hr", "3-hr", "6-hr", "12-hr",
                "24-hr", "2-day", "3-day", "4-day", "7-day", "10-day", "20-day", "30-day", "45-day", "60-day"]
PF_PERIODS = [1, 2, 5, 10, 25, 50, 100, 200, 500, 1000]


def runoff_in(p_in: float, cn: float) -> float:
    """Direct runoff depth (inches) for a storm of p_in inches on ground with curve number cn."""
    s = 1000 / cn - 10
    ia = 0.2 * s
    return 0.0 if p_in <= ia else (p_in - ia) ** 2 / (p_in - ia + s)


def soil_group(hydgrp: str | None) -> str | None:
    """Dual groups (A/D etc.) mean 'D undrained, A if drained'; developed land is drained."""
    if not hydgrp:
        return None
    g = hydgrp.strip().upper()[:1]
    return g if g in PERVIOUS_CN else None


def split(p_in: float, impervious_frac: float, group: str) -> dict:
    q_imp = runoff_in(p_in, IMPERVIOUS_CN)
    q_perv = runoff_in(p_in, PERVIOUS_CN[group])
    q = impervious_frac * q_imp + (1 - impervious_frac) * q_perv
    return {
        "storm_in": p_in,
        "storm_mm": round(p_in * MM_PER_IN, 1),
        "runoff_frac": round(q / p_in, 3) if p_in else 0,
        "pervious_runoff_frac": round(q_perv / p_in, 3) if p_in else 0,
    }


async def design_storms(lon: float, lat: float) -> dict | None:
    """24-hour storm depths by return period from NOAA Atlas 14 (contiguous US + territories)."""
    try:
        text = await fetch_text(NOAA_PF, must_contain="quantiles", ttl=60 * 60 * 24 * 365, params={
            "lat": f"{lat:.3f}", "lon": f"{lon:.3f}", "type": "pf", "data": "depth",
            "units": "english", "series": "pds",
        })
    except UpstreamError:
        return None
    m = re.search(r"quantiles = (\[\[.*?\]\]);", text, re.S)
    if not m:
        return None
    rows = ast.literal_eval(m.group(1))
    day = rows[PF_DURATIONS.index("24-hr")]
    region = re.search(r"region = '([^']*)'", text)
    return {
        "source": "NOAA Atlas 14" + (f", {region.group(1)}" if region else ""),
        "24h_in": {str(p): float(v) for p, v in zip(PF_PERIODS, day)},
    }
