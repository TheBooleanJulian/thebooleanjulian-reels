import numpy as np
from scipy import ndimage
def find_moon_jupiter(img):
    a = np.asarray(img.convert('L'), dtype=np.float32)
    m = a > 200
    lab, n = ndimage.label(m)
    if n == 0: return None, None
    sizes = ndimage.sum(m, lab, range(1, n+1))
    mi = int(np.argmax(sizes)) + 1
    moon = ndimage.center_of_mass(m, lab, mi)
    ys, xs = np.where(lab == mi)
    bottom = ys.max()
    # jupiter: brightest small blob below moon bottom within 200px, excluding moon mask (dilated)
    dil = ndimage.binary_dilation(lab == mi, iterations=6)
    b = a.copy(); b[dil] = 0
    y0 = int(moon[0]); win = b[y0:min(a.shape[0], bottom+220), max(0,int(moon[1])-250):int(moon[1])+250]
    if win.size == 0: return (moon[1], moon[0]), None
    yy, xx = np.unravel_index(np.argmax(win), win.shape)
    if win[yy, xx] < 90: return (moon[1], moon[0]), None
    return (moon[1], moon[0]), (xx + max(0,int(moon[1])-250), yy + y0)


def bright_dot(img, rows=1100):
    """Brightest blurred spot in the upper part of the frame (e.g. the Moon/a planet in a wide sky shot)."""
    a = ndimage.gaussian_filter(np.asarray(img.convert('L'), dtype=np.float32)[:rows], 3)
    y, x = np.unravel_index(a.argmax(), a.shape)
    return (float(x), float(y))
