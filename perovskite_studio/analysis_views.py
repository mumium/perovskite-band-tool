"""Layer-local readouts; never interpolate across a heterojunction."""
import numpy as np
from .physics import drawing_segments, profile


def analysis_segments(layers, result, temperature=300, left=5., right=4.1):
    segments = drawing_segments(layers, result, left, right)
    vt = 8.617333262e-5 * temperature
    for a, s in zip(layers, segments):
        t = (s['x'] - s['x'][0]) / a['thickness']
        props = profile(a, t)
        s['Eg'] = props['Eg']
        if 'phi' in result:
            s['phi'] = -props['chi'] - s['Ec']
            # x is in nm: -d(phi)/dx * 1e7 gives V/cm. Each material
            # retains its own one-sided derivative at an interface.
            s['field'] = -np.gradient(s['phi'], s['x']) * 1e7
            s['n'] = props['Nc'] * np.exp(np.clip(-s['Ec']/vt, -150, 80))
            s['p'] = props['Nv'] * np.exp(np.clip(s['Ev']/vt, -150, 80))
    return segments


def interface_offsets(layers, segments):
    """Signed offsets: right material minus left material, in eV."""
    return [dict(x=float(a['x'][-1]), left=layers[i]['name'],
                 right=layers[i+1]['name'],
                 Ec=float(b['Ec'][0]-a['Ec'][-1]),
                 Ev=float(b['Ev'][0]-a['Ev'][-1]))
            for i, (a, b) in enumerate(zip(segments, segments[1:]))]


def sample_position(layers, segments, x, temperature=300, side='right'):
    """Sample within a material; exact interfaces default to the right limit."""
    ends = np.cumsum([a['thickness'] for a in layers])
    if not np.isfinite(x) or x < 0 or x > ends[-1]:
        raise ValueError('位置超出器件范围')
    i = min(int(np.searchsorted(ends, x, side=side)), len(layers)-1)
    s = segments[i]
    out = dict(index=i, name=layers[i]['name'], x=float(x))
    for key in ('Ec', 'Ev', 'Eg', 'phi', 'field'):
        if key in s:
            out[key] = float(np.interp(x, s['x'], s[key]))
    if 'phi' in s:
        vt = 8.617333262e-5 * temperature
        out['n'] = float(layers[i]['Nc'] * np.exp(np.clip(-out['Ec']/vt, -150, 80)))
        out['p'] = float(layers[i]['Nv'] * np.exp(np.clip(out['Ev']/vt, -150, 80)))
    return out
