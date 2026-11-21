"""EPSG:5070 (NAD83 / Conus Albers) forward projection, Snyder (1987) ellipsoidal formulas.

Only needed to request NLCD on its native 30 m grid; small enough not to pull in pyproj.
"""

import math

_A = 6378137.0
_F = 1 / 298.257222101            # GRS80
_E2 = 2 * _F - _F * _F
_E = math.sqrt(_E2)
_LAT1, _LAT2, _LAT0, _LON0 = map(math.radians, (29.5, 45.5, 23.0, -96.0))


def _q(phi: float) -> float:
    s = math.sin(phi)
    return (1 - _E2) * (s / (1 - _E2 * s * s) - math.log((1 - _E * s) / (1 + _E * s)) / (2 * _E))


def _m(phi: float) -> float:
    s = math.sin(phi)
    return math.cos(phi) / math.sqrt(1 - _E2 * s * s)


_M1, _M2 = _m(_LAT1), _m(_LAT2)
_Q1, _Q2, _Q0 = _q(_LAT1), _q(_LAT2), _q(_LAT0)
_N = (_M1 ** 2 - _M2 ** 2) / (_Q2 - _Q1)
_C = _M1 ** 2 + _N * _Q1
_RHO0 = _A * math.sqrt(_C - _N * _Q0) / _N


def forward(lon: float, lat: float) -> tuple[float, float]:
    rho = _A * math.sqrt(_C - _N * _q(math.radians(lat))) / _N
    theta = _N * (math.radians(lon) - _LON0)
    return rho * math.sin(theta), _RHO0 - rho * math.cos(theta)
