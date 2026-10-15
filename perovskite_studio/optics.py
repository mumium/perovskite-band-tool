"""Validated, portable spectrum/nk inputs for the native SimSS TMM module.

Public tables: wavelength in nm, irradiance in W/m2/nm; n and k dimensionless.
SimSS tables: wavelength in m, irradiance in W/m2/m. Never extrapolate nk data.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import sys

import numpy as np
from .paths import RESOURCE_ROOT

integrate = np.trapezoid if hasattr(np, 'trapezoid') else np.trapz

DATA_DIR = RESOURCE_ROOT/'assets'/'optics'
NK_LIBRARY = {'PTAA': 'nk_PTAA.txt', 'MAPI': 'nk_MAPI.txt', 'C60': 'nk_C60_1.txt',
              'ITO': 'nk_ITO.txt', 'Au': 'nk_Au.txt', 'Ag': 'nk_Ag.txt',
              'SiO2': 'nk_SiO2.txt', 'Air': 'nk_Air.txt'}
OPTICS_DEFAULTS = dict(mode='manual', illumination='left', wavelength_min=300., wavelength_max=800.,
                       spectrum={'kind': 'builtin', 'name': 'AM1.5G'},
                       substrate={'kind': 'builtin', 'name': 'ITO'},
                       tco={'kind': 'builtin', 'name': 'ITO'}, tco_thickness=200.,
                       back={'kind': 'builtin', 'name': 'Ag'}, back_thickness=100.)
HC_EV_NM = 1239.8419843320026


def optical_model(layer):
    # Old projects keep measured/fixed nk unless explicitly opting in.
    return dict(mode='fixed', reference_gap=layer['Eg']) | layer.get('optical_model', {})


def effective_nk(layer, wavelengths):
    """Basic edge model: remap alpha(E), anchor the source UV end; keep n fixed.

    The reference gap and local electrical gap determine the optical edge.
    This is explicitly an approximation, not a material/composition model.
    """
    raw = nk_table(layer['optical_nk'])
    wave = np.asarray(wavelengths, dtype=float)
    if wave.min() < raw[0, 0]-1e-6 or wave.max() > raw[-1, 0]+1e-6:
        raise ValueError(layer['name']+' 的 n,k 未覆盖计算波段，不能外推')
    n = np.interp(wave, raw[:, 0], raw[:, 1])
    model = optical_model(layer)
    if model['mode'] == 'fixed':
        k = np.interp(wave, raw[:, 0], raw[:, 2])
    elif model['mode'] == 'edge':
        ref, gap = model['reference_gap'], layer['Eg']
        emin, emax = HC_EV_NM/raw[-1, 0], HC_EV_NM/raw[0, 0]
        if not np.isfinite(ref) or not emin <= ref < emax or not 0 < gap < emax:
            raise ValueError(layer['name']+'：参考 Eg 须落在 n,k 能量覆盖范围内，当前 Eg 须小于最高覆盖能量')
        energy = HC_EV_NM/wave
        above = energy >= gap
        k = np.zeros_like(wave)
        query_energy = ref+(energy[above]-gap)*(emax-ref)/(emax-gap)
        query_wave = np.clip(HC_EV_NM/query_energy, raw[0, 0], raw[-1, 0])
        alpha = 4*np.pi*np.interp(query_wave, raw[:, 0], raw[:, 2])/(query_wave*1e-9)
        k[above] = alpha*wave[above]*1e-9/(4*np.pi)
    else:
        raise ValueError('未知的带隙吸收联动模式')
    return np.column_stack([wave, n, k])


def get_optics(settings):
    result = deepcopy(OPTICS_DEFAULTS)
    result.update(deepcopy(settings.get('optics', {})))
    return result


def default_nk(index, absorber):
    return dict(kind='builtin', name='MAPI' if index == absorber else 'PTAA' if index < absorber else 'C60')


def _validate_table(values, columns, label):
    try:
        a = np.asarray(values, dtype=float)
    except (ValueError, TypeError):
        raise ValueError(label+'包含非数值内容') from None
    if a.ndim != 2 or a.shape[1] != columns or len(a) < 2 or not np.all(np.isfinite(a)):
        raise ValueError(label+f'须至少有两行、{columns} 列有限数值')
    if a[0, 0] <= 0 or np.any(np.diff(a[:, 0]) <= 0):
        raise ValueError(label+'波长须为正数，严格递增且不能重复（单位 nm）')
    if columns == 3 and (np.any(a[:, 1] <= 0) or np.any(a[:, 2] < 0)):
        raise ValueError(label+'要求 n > 0、k ≥ 0')
    if columns == 2 and np.any(a[:, 1] < 0):
        raise ValueError(label+'光谱辐照度须 ≥ 0')
    return a


def read_csv(path, columns):
    """Read a user nm-based CSV/whitespace table; embed data for portability."""
    rows = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            row = [float(x) for x in re.split(r'[,;\s]+', line)]
        except ValueError:
            if not rows and not line[0].isdigit():
                continue  # Optional header, e.g. wavelength_nm,n,k.
            raise ValueError('数据文件中存在无效数值：'+line[:80]) from None
        if len(row) != columns:
            raise ValueError(f'文件须有 {columns} 列；n,k 文件为 波长_nm,n,k，光谱为 波长_nm,W/m²/nm')
        rows.append(row)
    a = _validate_table(rows, columns, Path(path).name)
    return dict(kind='custom', name=Path(path).name, values=a.tolist(),
                sha256=sha256(a.astype('<f8').tobytes()).hexdigest())


def nk_table(spec):
    if not isinstance(spec, dict):
        raise ValueError('请选择每层的 n,k 数据')
    if spec.get('kind') == 'builtin' and spec.get('name') in NK_LIBRARY:
        a = np.loadtxt(DATA_DIR/NK_LIBRARY[spec['name']], skiprows=1)
        a[:, 0] *= 1e9
    elif spec.get('kind') == 'custom':
        a = spec.get('values')
    else:
        raise ValueError('未知的 n,k 数据来源')
    return _validate_table(a, 3, str(spec.get('name', 'n,k 数据')))


def spectrum_table(spec):
    if spec.get('kind') == 'builtin' and spec.get('name') == 'AM1.5G':
        a = np.loadtxt(DATA_DIR/'AM1.5G_ASTMG173.csv', delimiter=',', skiprows=2)[:, [0, 2]]
    elif spec.get('kind') == 'custom':
        a = spec.get('values')
    else:
        raise ValueError('未知的光谱来源')
    a = _validate_table(a, 2, '入射光谱')
    power = integrate(a[:, 1], a[:, 0])
    if not np.isfinite(power) or power <= 0:
        raise ValueError('入射光谱的积分功率须 > 0')
    return a


def validate_optics(layers, settings):
    o = get_optics(settings)
    if o['mode'] not in ('manual', 'spectrum'):
        raise ValueError('光照模式须选择手动 G 或光谱 TMM')
    if o['mode'] == 'manual':
        return
    if o['illumination'] not in ('left', 'right'):
        raise ValueError('请选择光从左侧或右侧入射')
    lo, hi = o['wavelength_min'], o['wavelength_max']
    if not all(np.isfinite(v) for v in (lo, hi, o['tco_thickness'], o['back_thickness'])):
        raise ValueError('光学设置须为有限数值')
    if not 100 <= lo < hi <= 4000 or hi-lo < 1 or (hi-lo) > 2500:
        raise ValueError('光学计算波段须在 100–4000 nm 内，宽度为 1–2500 nm')
    if lo != round(lo) or hi != round(hi):
        raise ValueError('光学计算波段端点请填写整数 nm')
    if o['tco_thickness'] < 0 or o['back_thickness'] <= 0:
        raise ValueError('前透明电极厚度须 ≥ 0（0 表示不使用），背电极厚度须 > 0')
    spectrum = spectrum_table(o['spectrum'])
    if lo < spectrum[0, 0] or hi > spectrum[-1, 0]:
        raise ValueError('光学计算波段超出光谱数据范围，不能外推')
    specs = [('基底', o['substrate']), ('背电极', o['back'])]
    if o['tco_thickness'] > 0:
        specs.append(('前透明电极', o['tco']))
    specs += [(a['name'], a.get('optical_nk')) for a in layers]
    for name, spec in specs:
        a = nk_table(spec)
        if lo < a[0, 0]-1e-6 or hi > a[-1, 0]+1e-6:
            raise ValueError(f'{name} 的 n,k 仅覆盖 {a[0,0]:g}–{a[-1,0]:g} nm；请缩小波段或导入覆盖完整波段的数据。')
    from .physics import profile
    for a in layers:
        for gap in profile(a, [0., 1.])['Eg']:
            effective_nk(dict(a, Eg=float(gap)), [lo, hi])


def prepare_optical_files(folder, pieces, settings):
    """Write SI optical files and return complete input provenance."""
    o = get_optics(settings)
    if o['mode'] == 'manual':
        return dict(mode='manual')
    folder = Path(folder)
    raw = spectrum_table(o['spectrum'])
    reference_power = float(integrate(raw[:, 1], raw[:, 0]))
    # Normalization always uses the complete incident spectrum, not the subset
    # of wavelengths modelled for generation.
    scaled = raw.copy()
    scaled[:, 1] *= settings['pin']*10/reference_power
    si = scaled.copy()
    if np.min(np.diff(si[:, 0])) < .5-1e-8:
        grid = np.arange(o['wavelength_min'], o['wavelength_max']+1., 1.)
        si = np.column_stack([grid, np.interp(grid, scaled[:, 0], scaled[:, 1])])
    si[:, 0] *= 1e-9
    si[:, 1] *= 1e9
    np.savetxt(folder/'spectrum.txt', si, fmt=['%.9e', '%.16e'], header='lambda I', comments='')
    boundary_snapshots = []
    for filename, spec in [('substrate.txt', o['substrate']), ('back.txt', o['back'])]+(
            [('tco.txt', o['tco'])] if o['tco_thickness'] > 0 else []):
        a = nk_table(spec).copy()
        boundary_snapshots.append(dict(role=filename.split('.')[0], source=deepcopy(spec), values=a.tolist(),
                                       sha256=sha256(a.astype('<f8').tobytes()).hexdigest()))
        grid = np.arange(o['wavelength_min'], o['wavelength_max']+1., 1.)
        si_nk = np.column_stack([grid*1e-9, np.interp(grid, a[:, 0], a[:, 1]), np.interp(grid, a[:, 0], a[:, 2])])
        np.savetxt(folder/filename, si_nk, fmt=['%.9e', '%.16e', '%.16e'], header='lambda n k', comments='')
    snapshots = []
    for i, p in enumerate(pieces):
        a = nk_table(p['optical_nk'])
        grid = np.arange(o['wavelength_min'], o['wavelength_max']+1., 1.)
        effective = effective_nk(p, grid)
        snapshots.append(dict(parent_index=p['parent_index'], name=p['name'], local_gap_eV=p['Eg'], optical_model=optical_model(p),
                              source=deepcopy(p['optical_nk']), values=a.tolist(),
                              effective_values=effective.tolist(),
                              sha256=sha256(a.astype('<f8').tobytes()).hexdigest()))
        si = effective.copy()
        si[:, 0] *= 1e-9
        np.savetxt(folder/f'nk_layer_{i+1}.txt', si, fmt=['%.9e', '%.16e', '%.16e'], header='lambda n k', comments='')
    lo, hi = o['wavelength_min'], o['wavelength_max']
    wavelengths = np.r_[lo, raw[(raw[:, 0] > lo) & (raw[:, 0] < hi), 0], hi]
    modelled = float(integrate(np.interp(wavelengths, scaled[:, 0], scaled[:, 1]), wavelengths))/10
    return dict(mode='spectrum', model='SimSS normal-incidence TMM', integration_step_nm=1., settings=o,
                spectrum_name=o['spectrum']['name'], spectrum_nm_W_m2_nm=scaled.tolist(),
                reference_power_W_m2=reference_power, incident_power_mW_cm2=float(settings['pin']),
                modelled_band_power_mW_cm2=modelled, nk_snapshots=snapshots, boundary_nk_snapshots=boundary_snapshots,
                spectrum_sha256=sha256(raw.astype('<f8').tobytes()).hexdigest())
