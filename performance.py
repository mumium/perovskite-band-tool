"""Uniform/graded steady-state device simulation using bundled SimSS 5.36.

Public inputs use the same cm/eV/nm units as physics.py. SimSS uses SI and
positive energies below vacuum. Output J is positive for delivered PV current.
"""
from copy import deepcopy
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import time

import numpy as np

from physics import normalize, profile, validate

ENGINE_VERSION = '5.36'
ENGINE_BUILD = '5.36-pds1'
ENGINE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'vendor' / 'simsalabim'
DEFAULT_SETTINGS = dict(T=300., left=5., right=4.1, left_anode=1,
                        G=3e21, pin=100., vmin=-.04, vmax=1.5, vstep=.02,
                        sn_left=0., sp_left=1e7, sn_right=1e7, sp_right=0.,
                        rs=0., rsh=1e6, points=250, timeout=120, grading_slices=16)
EXTRA_FIELDS = [('Nt', '中性体陷阱浓度 Nt', 'cm⁻³'),
                ('Et', '陷阱位置 Et − Ev', 'eV'),
                ('B', '双分子复合系数 B', 'cm³/s'),
                ('transfer_n', '右界面电子传输速度', 'cm/s'),
                ('transfer_p', '右界面空穴传输速度', 'cm/s')]


class SimulationCancelled(Exception):
    pass


def import_layers(layers):
    """Copy inputs, preserving all spatial grading definitions."""
    absorber = min(range(len(layers)), key=lambda i: layers[i]['Eg'])
    result = []
    for i, original in enumerate(layers):
        a = normalize(deepcopy(original))
        a.update(Nt=1e15, Et=a['Eg']/2, B=1e-10,
                 transfer_n=1e5, transfer_p=1e5, absorber=i == absorber)
        result.append(a)
    return result


def is_graded(a):
    p = profile(a, [0., 1.])
    return any(not np.isclose(p[k][0], p[k][1], rtol=1e-12, atol=0.)
               for k in ('Eg', 'chi', 'Nd', 'Na'))


def discretize_layers(layers, slices=16):
    """Endpoint-centred finite sublayers; keep physical interfaces distinct.

    Endpoint samples have half widths, so contacts see exact endpoint values.
    Internal virtual interfaces are later assigned the original bulk mobility.
    """
    result = []
    for parent, original in enumerate(layers):
        a = normalize(original)
        n = int(slices) if is_graded(a) else 1
        t = np.linspace(0., 1., n) if n > 1 else np.array([.5])
        faces = np.r_[0., (t[:-1]+t[1:])/2, 1.]
        p = profile(a, t)
        for j in range(n):
            b = deepcopy(a)
            b.update({k: float(p[k][j]) for k in ('Eg', 'chi', 'Nd', 'Na')})
            b.update(thickness=float(a['thickness']*(faces[j+1]-faces[j])),
                     parent_index=parent, fraction=float(t[j]), virtual_right=j < n-1)
            for key in ('Eg', 'Nd', 'Na'):
                b[key+'_mode'] = '均匀'
                b[key+'_end'] = b[key]
            b.update(affinity_mode='独立端点', chi_end=b['chi'])
            result.append(b)
    return result


def _interface_spacings(layers, points, grad=4.):
    """Reproduce upstream 5.36 Make_Grid to preserve bulk mobility at cuts.

    See Units/DDRoutines.pas: Make_Sub_Grid, Make_Grid, Calc_Elec/Hole_Mob.
    Returned spacings are in metres, after interface mesh smoothing.
    """
    widths = np.array([a['thickness']*1e-9 for a in layers])
    total = widths.sum()
    budget = points-(len(layers)-1)
    thin = budget*widths/total < 5
    available = budget-5*int(thin.sum())
    thick_width = total-widths[thin].sum()
    if thick_width <= 0:
        raise ValueError('网格不足以描述渐变子层，请增加空间网格点数或减少分段数。')
    h = np.zeros(points+2)
    starts, ends = [], []
    start = 0
    for i, width in enumerate(widths):
        count = max(5, int(np.floor(available*width/thick_width)))
        end = start+count if i < len(layers)-1 else points+1
        if end-start < 5:
            raise ValueError('网格不足以描述渐变子层，请增加空间网格点数或减少分段数。')
        ending = round(.5*(end-start))  # Free Pascal ROUND uses ties-to-even.
        weights = np.ones(end-start+1)
        for j in range(ending+1):
            weights[j] = weights[-1-j] = np.exp(grad*(1/ending-1/(j+1)))
        h[start:end+1] = weights*width/(total*weights.sum())
        starts.append(start)
        ends.append(end)
        start = end+1
    for start in starts[1:]:
        h[start-2:start+1] = np.mean(h[start-2:start+1])
    return total*h[np.array(ends[:-1], dtype=int)]


def simulation_mesh(layers, settings):
    pieces = discretize_layers(layers, settings.get('grading_slices', 16))
    if len(pieces) > 120:
        raise ValueError('渐变离散后不能超过 120 个子层，请减少渐变分段数或材料层数。')
    points = max(int(settings['points']), 8*len(pieces))
    while points <= 2000:
        try:
            spacing = _interface_spacings(pieces, points)
            return pieces, points, spacing
        except ValueError:
            points += 50
    raise ValueError('渐变离散需要超过 2000 个网格点，请减少分段数或层数。')


def validate_device(layers, settings):
    s = settings
    for key in DEFAULT_SETTINGS:
        if key == 'grading_slices' and key not in s:  # Legacy performance projects.
            continue
        if key not in s or not np.isfinite(s[key]):
            raise ValueError(f'模拟设置 {key} 必须为有限数值')
    validate(layers, s['T'], s['left'], s['right'])
    if s['left_anode'] not in (0, 1):
        raise ValueError('请选择左侧电极类型')
    if not s['vmin'] <= 0 < s['vmax'] or s['vstep'] <= 0:
        raise ValueError('扫描须覆盖 0 V：起点 ≤ 0、终点 > 0、步长 > 0')
    count = (s['vmax']-s['vmin'])/s['vstep']
    if not 2 <= count <= 2000:
        raise ValueError('扫描至少需要 3 个点，且不能超过 2001 个点；请调整范围或步长')
    if s['G'] < 0 or s['pin'] <= 0:
        raise ValueError('光生率须 ≥ 0，入射功率密度须 > 0')
    if s['G'] > 0 and not any(a.get('absorber') for a in layers):
        raise ValueError('请至少勾选一个光生吸收层')
    if s['rs'] < 0 or s['rsh'] <= 0:
        raise ValueError('串联电阻须 ≥ 0，并联电阻须 > 0（单位 Ω·cm²）')
    for key in ('sn_left', 'sp_left', 'sn_right', 'sp_right'):
        if s[key] < 0:
            raise ValueError('接触速度须 ≥ 0，0 表示阻挡接触')
    if s['points'] != int(s['points']) or not max(20, 5*len(layers)) <= s['points'] <= 2000:
        raise ValueError('网格点数须为整数，在 max(20, 5×层数) 至 2000 内')
    if s['timeout'] != int(s['timeout']) or not 1 <= s['timeout'] <= 3600:
        raise ValueError('超时须为 1–3600 秒的整数')
    slices = s.get('grading_slices', 16)
    if slices != int(slices) or not 2 <= slices <= 100:
        raise ValueError('每个渐变层的分段数须为 2–100 的整数')
    for a in layers:
        p = profile(a, [0., 1.])
        # SimSS has one DOS parameter for both bands. Reject unequal inputs.
        if not np.isclose(a['Nc'], a['Nv'], rtol=1e-12, atol=0.):
            raise ValueError(f"{a['name']}：SimSS 5.36 要求 Nc = Nv，请在性能页填写相同值。")
        for key, label, _ in EXTRA_FIELDS:
            if key not in a or not np.isfinite(a[key]) or a[key] < 0:
                raise ValueError(f"{a['name']}：{label} 须为有限非负数")
        if not 0 <= a['Et'] <= float(np.min(p['Eg'])):
            raise ValueError(f"{a['name']}：陷阱位置 Et − Ev 须在 0 至本层最小 Eg 内")
        if a['transfer_n'] <= 0 or a['transfer_p'] <= 0:
            raise ValueError(f"{a['name']}：界面传输速度须 > 0")
    for key, a, t in (('left', layers[0], 0.), ('right', layers[-1], 1.)):
        p = profile(a, [t])
        if not p['chi'][0] <= s[key] <= p['chi'][0]+p['Eg'][0]:
            side = '左' if key == 'left' else '右'
            raise ValueError(f'{side}电极功函数须在接触层 χ 至 χ + Eg 范围内（SimSS 限制）')
    simulation_mesh(layers, s)


def _template(name, replacements):
    text = (ENGINE_DIR / name).read_text(encoding='utf-8-sig')
    for key, value in replacements.items():
        value = format(value, '.16g') if isinstance(value, (int, float)) else str(value)
        text, count = re.subn(r'(?m)^' + re.escape(key) + r'\s*=\s*[^*\r\n]*',
                             lambda m: f'{key} = {value} ', text)
        if count != 1:
            raise RuntimeError(f'SimSS 模板版本不匹配：{key}')
    return text


def write_inputs(folder, layers, settings):
    """Write a complete, reproducible run directory (no optical data required)."""
    validate_device(layers, settings)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    s = settings
    pieces, points, spacing = simulation_mesh(layers, s)
    setup = _template('simulation_setup.txt', dict(
        T=s['T'], leftElec=1 if s['left_anode'] else -1,
        W_L=s['left'], W_R=s['right'],
        S_n_L=s['sn_left']*.01, S_p_L=s['sp_left']*.01,
        S_n_R=s['sn_right']*.01, S_p_R=s['sp_right']*.01,
        R_series=s['rs']*1e-4, R_shunt=s['rsh']*1e-4,
        genProfile='none', G_frac=1, NP=points,
        convVar=4, failureMode=0, Vmin=s['vmin'], Vmax=s['vmax'], Vstep=s['vstep'],
        Vscan=1, untilVoc=0, timeout=int(s['timeout']), autoTidy=0,
        varFile='none', limitDigits=0))
    setup = re.sub(r'(?m)^l[123]\s*=.*\n?', '', setup)
    layer_lines = '\n'.join(f'l{i+1} = layer_{i+1}.txt * layer {i+1}' for i in range(len(pieces)))
    setup = setup.replace('**Contacts', layer_lines+'\n\n**Contacts', 1)
    (folder/'simulation_setup.txt').write_text(setup, encoding='ascii')
    for i, a in enumerate(pieces):
        nt = a['Nt']*1e6
        # SimSS assigns mu = dx * nu / Vt at every layer interface.
        # At numerical cuts, choose nu so mu equals the unchanged bulk value.
        # User transfer speeds apply only to real material boundaries.
        vt = 1.3807e-23*s['T']/1.6022e-19
        vn = a['mun']*1e-4*vt/spacing[i] if a['virtual_right'] else a['transfer_n']*.01
        vp = a['mup']*1e-4*vt/spacing[i] if a['virtual_right'] else a['transfer_p']*.01
        text = _template('layer_parameters.txt', dict(
            L=a['thickness']*1e-9, eps_r=a['eps'], E_c=a['chi'], E_v=a['chi']+a['Eg'],
            N_c=a['Nc']*1e6, N_D=a['Nd']*1e6, N_A=a['Na']*1e6,
            mu_n=a['mun']*1e-4, mu_p=a['mup']*1e-4,
            nu_int_n=vn, nu_int_p=vp,
            N_t_int=0, N_anion=0, N_cation=0, mu_anion=0, mu_cation=0, ionsMayEnter=0,
            G_ehp=s['G']*1e6 if a.get('absorber') else 0,
            layerGen=int(bool(a.get('absorber'))), k_direct=a['B']*1e-6,
            N_t_bulk=nt, C_n_bulk=1/(a['taun']*nt) if nt else 0,
            C_p_bulk=1/(a['taup']*nt) if nt else 0,
            E_t_bulk=a['chi']+a['Eg']-a['Et'], bulkTrapType=0))
        (folder/f'layer_{i+1}.txt').write_text(text, encoding='ascii')
    mesh = dict(grading_slices=int(s.get('grading_slices', 16)), sublayers=len(pieces),
                requested_points=int(s['points']), effective_points=points,
                method='endpoint-centred sublayers; bulk mobility at virtual interfaces', pieces=pieces)
    (folder/'inputs.json').write_text(json.dumps(dict(engine=ENGINE_VERSION, layers=layers,
                                                     settings=s, mesh=mesh), ensure_ascii=False, indent=2), encoding='utf-8')
    return mesh


def calculate_metrics(voltage, current, pin):
    """Metrics from piecewise-linear delivered J (mA/cm²); no extrapolation."""
    v, j = np.asarray(voltage, dtype=float), np.asarray(current, dtype=float)
    if v.ndim != 1 or j.shape != v.shape or len(v) < 3 or not np.all(np.isfinite([v, j])):
        raise ValueError('J–V 数据无效或不足三个点')
    order = np.argsort(v)
    v, j = v[order], j[order]
    if np.any(np.diff(v) <= 0) or not np.isfinite(pin) or pin <= 0:
        raise ValueError('J–V 电压必须互异，入射功率密度须 > 0')
    warnings = []
    metrics = dict(Jsc=None, Voc=None, FF=None, PCE=None, Vmp=None, Jmp=None, Pmax=None)
    if v[0] <= 0 <= v[-1]:
        metrics['Jsc'] = float(np.interp(0, v, j))
    else:
        warnings.append('端电压没有覆盖 0 V，无法计算 Jsc；请降低扫描起点。')
    positive = metrics['Jsc'] is not None and metrics['Jsc'] > 1e-8
    if positive:
        for i in range(len(v)-1):
            if v[i+1] > 0 and j[i] > 0 >= j[i+1]:
                metrics['Voc'] = float(v[i]-j[i]*(v[i+1]-v[i])/(j[i+1]-j[i]))
                break
        if metrics['Voc'] is None:
            warnings.append('扫描未覆盖开路点，Voc、FF、PCE 和最大功率尚未确认；请增大扫描终点。')
        else:
            voc = metrics['Voc']
            candidates = [(0., metrics['Jsc']), (voc, 0.)]
            for i in range(len(v)-1):
                lo, hi = max(0., v[i]), min(voc, v[i+1])
                if lo > hi:
                    continue
                slope = (j[i+1]-j[i])/(v[i+1]-v[i])
                intercept = j[i]-slope*v[i]
                candidates.extend((x, slope*x+intercept) for x in (lo, hi))
                if slope < 0 and lo <= -intercept/(2*slope) <= hi:
                    x = -intercept/(2*slope)
                    candidates.append((x, slope*x+intercept))
            vm, jm = max(candidates, key=lambda pair: pair[0]*pair[1])
            pm = vm*jm  # V × mA/cm² = mW/cm²
            metrics.update(Vmp=float(vm), Jmp=float(jm), Pmax=float(pm),
                           FF=float(100*pm/(voc*metrics['Jsc'])), PCE=float(100*pm/pin))
    else:
        warnings.append('未获得正向光伏输出，Voc、FF 和效率不适用；请检查光照、接触选择性和电极方向。')
    return v, j, metrics, warnings


def run_simulation(layers, settings, cancel=None):
    validate_device(layers, settings)
    exe = ENGINE_DIR / ('simss.exe' if sys.platform == 'win32' else 'simss')
    if not exe.is_file():
        raise RuntimeError(f'未找到 SimSS {ENGINE_VERSION} 求解器：{exe}')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='bandtool_simss_') as folder:
        mesh = write_inputs(folder, layers, settings)
        if cancel is not None and cancel.is_set():
            raise SimulationCancelled('模拟已取消')
        process = subprocess.Popen([str(exe), 'simulation_setup.txt'], cwd=folder,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        try:
            while True:
                try:
                    output, _ = process.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        raise SimulationCancelled('模拟已取消')
                    if time.monotonic()-started > settings['timeout']+5:
                        raise RuntimeError('SimSS 计算超时；请减少扫描点数或调整器件参数。')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        log_path = Path(folder)/'log.txt'
        log = log_path.read_text(encoding='utf-8', errors='replace') if log_path.exists() else ''
        console = output.decode('utf-8', errors='replace')
        if process.returncode != 0:
            raise RuntimeError(f'SimSS 未完成计算（退出码 {process.returncode}），未采用部分曲线。\n'
                               + (console or log)[-3500:])
        data_path = Path(folder)/'JV.dat'
        if not data_path.exists():
            raise RuntimeError('SimSS 未生成 J–V 文件。\n'+(console or log)[-2000:])
        data = np.atleast_1d(np.genfromtxt(data_path, names=True))
        if not {'Vext', 'Jext', 'errJ'}.issubset(data.dtype.names or ()):
            raise RuntimeError('SimSS 输出列不匹配，要求 Vext、Jext、errJ。')
        v, j, metrics, warnings = calculate_metrics(data['Vext'], -data['Jext']/10, settings['pin'])
        if mesh['sublayers'] > len(layers):
            warnings.append(f"渐变层已离散为 {mesh['sublayers']} 个总子层；建议增加分段数比较结果稳定性。")
        if mesh['effective_points'] != mesh['requested_points']:
            warnings.append(f"空间网格已自动增至 {mesh['effective_points']} 点以容纳子层。")
        if not np.all(np.isfinite(data['errJ'])):
            raise RuntimeError('SimSS 电流守恒误差无效，未采用此曲线。')
        generated = 1.602176634e-19*settings['G']*sum(a['thickness']*1e-7 for a in layers if a.get('absorber'))*1e3
        if metrics['Jsc'] is not None and metrics['Jsc'] > generated*1.01+1e-5:
            warnings.append('Jsc 高于 q∫Gdx 的光生电流参考值，请检查接触注入及数值收敛。')
        if metrics['PCE'] is not None and metrics['PCE'] > 100:
            warnings.append('效率超过 100%；请检查手动光生率与入射功率是否一致。')
        relative_error = float(np.max(data['errJ']/np.maximum(np.abs(data['Jint']), max(generated*10, 1e-12))))
        if relative_error > .005:
            warnings.append('归一化电流不均匀度超过 0.5%，建议增加网格点数并检查参数。')
        return dict(voltage=v, current=j, metrics=metrics, warnings=warnings,
                    generated_current=generated, elapsed=time.monotonic()-started,
                    max_current_error=float(np.max(data['errJ'])),
                    relative_current_error=relative_error,
                    layers=deepcopy(layers), settings=dict(settings), log=log,
                    engine=ENGINE_VERSION, engine_build=ENGINE_BUILD, mesh=mesh)
