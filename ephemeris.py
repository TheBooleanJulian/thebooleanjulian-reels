"""Moon-Jupiter separation, phase and distances for a given UTC time and site (default: Singapore).
usage: python3 ephemeris.py 2026-11-02T21:04"""
import sys
from astropy.time import Time
from astropy.coordinates import get_body, get_sun, EarthLocation, get_constellation, solar_system_ephemeris
import astropy.units as u, numpy as np
solar_system_ephemeris.set('builtin')
loc = EarthLocation(lat=1.35 * u.deg, lon=103.82 * u.deg)
T = Time(sys.argv[1] if len(sys.argv) > 1 else '2026-11-02T21:04')
m, j, s = get_body('moon', T, loc), get_body('jupiter', T, loc), get_sun(T)
ill = (1 - np.cos(s.separation(get_body('moon', T)).rad)) / 2
print(f'separation {m.separation(j).deg:.2f} deg | moon lit {ill*100:.0f}% | moon {m.distance.to(u.km).value:,.0f} km | '
      f'jupiter {j.distance.to(u.km).value/1e6:,.0f} million km | in {get_constellation(j)}')
