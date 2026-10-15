"""Uniform/graded steady-state device simulation using bundled SimSS 5.36.

Public inputs use the same cm/eV/nm units as physics.py. SimSS uses SI and
positive energies below vacuum. Output J is positive for delivered PV current.
"""
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import numpy as np
from scipy.optimize import brentq

from .physics import normalize, profile, validate
from .optics import get_optics, default_nk, validate_optics, prepare_optical_files, integrate, optical_model
from .paths import RESOURCE_ROOT

ENGINE_VERSION = '5.36'
ENGINE_BUILD = '5.36-pds1'
ENGINE_DIR = RESOURCE_ROOT / 'vendor' / 'simsalabim'
DEFAULT_SETTINGS = dict(T=300., left=5., right=4.1, left_anode=1,
                        G=3e21, pin=100., vmin=-.04, vmax=1.5, vstep=.02,
                        sn_left=0., sp_left=1e7, sn_right=1e7, sp_right=0.,
                        rs=0.3, rsh=4e4, points=250, timeout=120, grading_slices=16)
EXTRA_FIELDS = [('Nt', '中性体陷阱浓度 Nt', 'cm⁻³'),
                ('Et', '陷阱位置 Et − Ev', 'eV'),
                ('B', '双分子复合系数 B', 'cm³/s'),
                ('transfer_n', '右界面电子传输速度', 'cm/s'),
                ('transfer_p', '右界面空穴传输速度', 'cm/s')]
INTERFACE_DEFAULTS = dict(Nt=0., Et=4.7, Cn=1e-8, Cp=1e-8, trap_type=0, auto_energy=True)
EQE_DEFAULTS = dict(step_nm=10., photon_flux_cm2_s=1e14, background_scale=1.)


def interface_parameters(layer):
    return dict(INTERFACE_DEFAULTS) | layer.get('interface', {})


def interface_energy(left, right):
    """Positive depth below vacuum, within both adjacent endpoint band gaps."""
    l, r = profile(left, [1.]), profile(right, [0.])
    low = max(l['chi'][0], r['chi'][0])
    high = min(l['chi'][0]+l['Eg'][0], r['chi'][0]+r['Eg'][0])
    p = interface_parameters(left)
    energy = (low+high)/2 if p['auto_energy'] else p['Et']
    if p['Nt'] > 0 and not low < energy < high:
        raise ValueError(f"{left['name']} / {right['name']}：界面陷阱深度须位于两侧共同禁带 ({low:g}, {high:g}) eV 内")
    return float(energy)


class SimulationCancelled(Exception):
    pass


class EngineFailure(RuntimeError):
    def __init__(self, message, returncode=None, diagnostic_path=None):
        super().__init__(message)
        self.returncode = returncode
        self.diagnostic_path = diagnostic_path


def import_layers(layers):
    """Copy inputs, preserving all spatial grading definitions."""
    absorber = min(range(len(layers)), key=lambda i: layers[i]['Eg'])
    result = []
    for i, original in enumerate(layers):
        a = normalize(deepcopy(original))
        a.update(Nt=1e15, Et=a['Eg']/2, B=1e-10,
                 transfer_n=1e5, transfer_p=1e5, absorber=i == absorber)
        a.setdefault('optical_nk', default_nk(i, absorber))
        ref_gap = 1.55 if a['optical_nk'] == dict(kind='builtin', name='MAPI') else a['Eg']
        a.setdefault('optical_model', dict(mode='edge' if i == absorber else 'fixed', reference_gap=ref_gap))
        a.setdefault('interface', deepcopy(INTERFACE_DEFAULTS))
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
    if (s['G'] > 0 or get_optics(s)['mode'] != 'manual') and not any(a.get('absorber') for a in layers):
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
    for left, right in zip(layers[:-1], layers[1:]):
        p = interface_parameters(left)
        if any(not np.isfinite(p[k]) or p[k] < 0 for k in ('Nt', 'Et', 'Cn', 'Cp')):
            raise ValueError('界面缺陷浓度、深度与捕获系数须为有限非负数')
        if p['trap_type'] not in (-1, 0, 1) or p['auto_energy'] not in (True, False):
            raise ValueError('界面陷阱类型或能级模式无效')
        if p['Nt'] > 0 and p['Cn']+p['Cp'] == 0:
            raise ValueError('界面缺陷开启时，电子和空穴捕获系数不能同时为零；关闭请设 Nt=0')
        interface_energy(left, right)
    for key, a, t in (('left', layers[0], 0.), ('right', layers[-1], 1.)):
        p = profile(a, [t])
        if not p['chi'][0] <= s[key] <= p['chi'][0]+p['Eg'][0]:
            side = '左' if key == 'left' else '右'
            raise ValueError(f'{side}电极功函数须在接触层 χ 至 χ + Eg 范围内（SimSS 限制）')
    simulation_mesh(layers, s)
    validate_optics(layers, s)


def _reverse_for_illumination(layers, settings):
    """Native TMM illuminates the left side; mirror the full physical device.

    Keep photovoltaic terminal sign convention, swap contact conditions, and
    carry true interface transfer speeds with the corresponding interface.
    """
    reversed_layers = []
    for i in range(len(layers)-1, -1, -1):
        a = normalize(deepcopy(layers[i]))
        p = profile(a, [0., 1.])
        for key in ('Eg', 'Nd', 'Na'):
            a[key], a[key+'_end'] = float(p[key][1]), float(p[key][0])
        a.update(affinity_mode='独立端点', chi=float(p['chi'][1]), chi_end=float(p['chi'][0]))
        if i > 0:
            for key in ('transfer_n', 'transfer_p'):
                a[key] = layers[i-1][key]
            a['interface'] = deepcopy(interface_parameters(layers[i-1]))
        reversed_layers.append(a)
    s = deepcopy(settings)
    s['left'], s['right'] = s['right'], s['left']
    s['left_anode'] = 1-int(s['left_anode'])
    s['sn_left'], s['sn_right'] = s['sn_right'], s['sn_left']
    s['sp_left'], s['sp_right'] = s['sp_right'], s['sp_left']
    return reversed_layers, s


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
    """Write a complete run directory, including normalized optical tables."""
    validate_device(layers, settings)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    s = settings
    mirrored = get_optics(s)['mode'] == 'spectrum' and get_optics(s)['illumination'] == 'right'
    if mirrored:
        layers, s = _reverse_for_illumination(layers, s)
    pieces, points, spacing = simulation_mesh(layers, s)
    optical = prepare_optical_files(folder, pieces, s)
    spectral = optical['mode'] == 'spectrum'
    replacements = dict(
        T=s['T'], leftElec=1 if s['left_anode'] else -1,
        W_L=s['left'], W_R=s['right'],
        S_n_L=s['sn_left']*.01, S_p_L=s['sp_left']*.01,
        S_n_R=s['sn_right']*.01, S_p_R=s['sp_right']*.01,
        R_series=s['rs']*1e-4, R_shunt=s['rsh']*1e-4,
        genProfile='calc' if spectral else 'none', G_frac=1, NP=points,
        convVar=4, failureMode=0, Vmin=s['vmin'], Vmax=s['vmax'], Vstep=s['vstep'],
        Vscan=1, untilVoc=0, timeout=int(s['timeout']), autoTidy=0,
        varFile='OpticalProfile.dat' if spectral else 'none', limitDigits=0)
    if spectral:
        o = get_optics(s)
        replacements.update(spectrum='spectrum.txt', nkSubstrate='substrate.txt', nkTCO='tco.txt', nkBE='back.txt',
                            L_TCO=o['tco_thickness']*1e-9, L_BE=o['back_thickness']*1e-9,
                            lambda_min=o['wavelength_min']*1e-9, lambda_max=o['wavelength_max']*1e-9,
                            outputRatio=int((s['vmax']-s['vmin'])/s['vstep']+1e-10)+1)
    setup = _template('simulation_setup.txt', replacements)
    setup = re.sub(r'(?m)^l[123]\s*=.*\n?', '', setup)
    layer_lines = '\n'.join(f'l{i+1} = layer_{i+1}.txt * layer {i+1}' for i in range(len(pieces)))
    setup = setup.replace('**Contacts', layer_lines+'\n\n**Contacts', 1)
    (folder/'simulation_setup.txt').write_text(setup, encoding='ascii')
    for i, a in enumerate(pieces):
        nt = a['Nt']*1e6
        inter = interface_parameters(a)
        real_interface = not a['virtual_right'] and i < len(pieces)-1
        trap_energy = interface_energy(a, pieces[i+1]) if real_interface else inter['Et']
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
            N_t_int=inter['Nt']*1e4 if real_interface else 0,
            E_t_int=trap_energy, C_n_int=inter['Cn']*1e-6, C_p_int=inter['Cp']*1e-6,
            intTrapType=inter['trap_type'], intTrapFile='none',
            N_anion=0, N_cation=0, mu_anion=0, mu_cation=0, ionsMayEnter=0,
            G_ehp=0 if spectral else s['G']*1e6 if a.get('absorber') else 0,
            nkLayer=f'nk_layer_{i+1}.txt' if spectral else 'none',
            layerGen=int(bool(a.get('absorber'))), k_direct=a['B']*1e-6,
            N_t_bulk=nt, C_n_bulk=1/(a['taun']*nt) if nt else 0,
            C_p_bulk=1/(a['taup']*nt) if nt else 0,
            E_t_bulk=a['chi']+a['Eg']-a['Et'], bulkTrapType=0))
        (folder/f'layer_{i+1}.txt').write_text(text, encoding='ascii')
    mesh = dict(grading_slices=int(s.get('grading_slices', 16)), sublayers=len(pieces),
                requested_points=int(s['points']), effective_points=points,
                method='endpoint-centred sublayers; bulk mobility at virtual interfaces', pieces=pieces,
                mirrored=mirrored, optical=optical)
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


def _diagnostics_directories():
    local = os.environ.get('LOCALAPPDATA')
    preferred = Path(local)/'PerovskiteDeviceStudio'/'diagnostics' if local else Path.home()/'PerovskiteDeviceStudio-diagnostics'
    return preferred, Path(tempfile.gettempdir())/'PerovskiteDeviceStudio-diagnostics'


def _engine_executable():
    """Frozen apps run an exact, verified engine copy from a stable user path."""
    bundled = ENGINE_DIR/('simss.exe' if sys.platform == 'win32' else 'simss')
    if not bundled.is_file():
        raise RuntimeError(f'未找到 SimSS {ENGINE_VERSION} 求解器：{bundled}')
    if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
        return bundled
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return bundled
    digest = hashlib.sha256(bundled.read_bytes()).hexdigest()
    directory = Path(local)/'PerovskiteDeviceStudio'/'engines'/digest
    target = directory/'simss.exe'
    temporary = directory/('simss_'+uuid.uuid4().hex+'.tmp')
    def valid():
        return target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest
    try:
        if valid():
            return target
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundled, temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise OSError('求解器副本校验失败')
        try:
            os.replace(temporary, target)
        except OSError:
            if not valid():
                raise
        return target
    except OSError:
        return bundled  # Read-only profiles can still use the original engine.
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _engine_failure(folder, context, message, stdout='', stderr='', log='', returncode=None, executable=None):
    """Keep this attempt's inputs and full output after its temporary folder closes."""
    folder = Path(folder)
    saved = None
    try:
        from .app_info import APP_VERSION
        metadata = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                        operation=context, returncode=returncode, message=message,
                        app_version=APP_VERSION, engine=ENGINE_VERSION, engine_build=ENGINE_BUILD,
                        executable=str(executable or ENGINE_DIR/('simss.exe' if sys.platform == 'win32' else 'simss')),
                        frozen=bool(getattr(sys, 'frozen', False)), platform=sys.platform,
                        windows_version=list(sys.getwindowsversion()) if sys.platform == 'win32' else None)
        for name, content in [('stdout.txt', stdout), ('stderr.txt', stderr),
                              ('diagnostics.json', json.dumps(metadata, ensure_ascii=False, indent=2))]:
            (folder/name).write_text(content, encoding='utf-8')
        name = 'simss_failure_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
        for directory in _diagnostics_directories():
            try:
                directory.mkdir(parents=True, exist_ok=True)
                saved = shutil.make_archive(str(directory/name), 'zip', root_dir=folder)
                break
            except OSError:
                continue
    except (OSError, ValueError):
        pass  # A diagnostic write failure must never hide the solver error.
    sections = [f'{context}失败：{message}']
    if returncode == 226:
        sections.append('226 是求解器运行时异常，不能按普通数值不收敛处理；请比较文件夹版或检查运行环境。')
    sections.append('本次失败输入与完整日志已保存：\n'+saved if saved else '诊断包未能保存。')
    if stderr.strip():
        sections.append('本次错误输出：\n'+stderr[-1400:])
    if stdout.strip():
        sections.append('本次控制台输出：\n'+stdout[-1000:])
    if log.strip():
        sections.append('本次计算日志：\n'+log[-1000:])
    elif not stdout.strip() and not stderr.strip():
        sections.append('本次未生成运行输出或计算日志；没有使用前一次计算的日志。')
    return EngineFailure('\n\n'.join(sections), returncode, saved)


def _run_engine_once(folder, settings, cancel=None, started=None, context='J–V 计算'):
    """Execute one native run; never return partial or nonfinite output."""
    if cancel is not None and cancel.is_set():
        raise SimulationCancelled('模拟已取消')
    exe = _engine_executable()
    started = time.monotonic() if started is None else started
    if time.monotonic()-started > settings['timeout']:
        raise RuntimeError('模拟总时长超过超时设置')
    folder = Path(folder)
    # EQE reuses a directory. No previous run's log or result may describe or
    # validate a new attempt that crashes before opening its output files.
    for name in ('log.txt', 'JV.dat', 'OpticalProfile.dat', 'scPars.txt', 'Var.dat',
                 'stdout.txt', 'stderr.txt', 'diagnostics.json'):
        (folder/name).unlink(missing_ok=True)
    try:
        process = subprocess.Popen([str(exe), 'simulation_setup.txt'], cwd=folder,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except OSError as error:
        raise _engine_failure(folder, context, '无法启动 SimSS：'+str(error), executable=exe) from error
    timed_out = False
    try:
        while True:
            try:
                output, error_output = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    raise SimulationCancelled('模拟已取消')
                if time.monotonic()-started > settings['timeout']+5:
                    process.kill()
                    output, error_output = process.communicate()
                    timed_out = True
                    break
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    log_path = Path(folder)/'log.txt'
    log = log_path.read_text(encoding='utf-8', errors='replace') if log_path.exists() else ''
    console = output.decode('utf-8', errors='replace')
    stderr = (error_output or b'').decode('utf-8', errors='replace')
    def failed(message):
        return _engine_failure(folder, context, message, console, stderr, log, process.returncode, exe)
    if timed_out:
        raise failed('SimSS 计算超时；请减少计算点数或调整超时设置。')
    if process.returncode != 0:
        raise failed(f'SimSS 未完成计算（退出码 {process.returncode}），未采用部分曲线。')
    data_path = Path(folder)/'JV.dat'
    if not data_path.exists():
        raise failed('SimSS 未生成本次 J–V 文件。')
    try:
        data = np.atleast_1d(np.genfromtxt(data_path, names=True))
    except (ValueError, OSError) as error:
        raise failed('SimSS 输出无法读取：'+str(error)) from error
    if data.size == 0 or not {'Vext', 'Jext', 'errJ'}.issubset(data.dtype.names or ()):
        raise failed('SimSS 输出列不匹配，要求 Vext、Jext、errJ。')
    if not np.all(np.isfinite([data['Vext'], data['Jext'], data['errJ']])):
        raise failed('SimSS 电流或收敛误差包含无效数值')
    if cancel is not None and cancel.is_set():
        raise SimulationCancelled('模拟已取消')
    return data, log


def _run_engine(folder, settings, cancel=None, started=None, context='J–V 计算'):
    """Only transient runtime 226 may restart, with identical inputs and deadline."""
    started = time.monotonic() if started is None else started
    failures = []
    for attempt in range(1, 4):
        try:
            data, log = _run_engine_once(folder, settings, cancel, started,
                                         context=f'{context}（尝试 {attempt}/3）')
        except EngineFailure as error:
            if error.returncode != 226 or attempt == 3:
                raise
            failures.append(dict(context=context, returncode=226, attempt=attempt,
                                 diagnostic_path=error.diagnostic_path))
            if cancel is not None:
                if cancel.wait(.15):
                    raise SimulationCancelled('模拟已取消') from error
            else:
                time.sleep(.15)
            continue
        if failures:
            path = Path(folder)/'engine_recoveries.json'
            previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            path.write_text(json.dumps(previous+failures, ensure_ascii=False, indent=2), encoding='utf-8')
            log += f'\nPDS: {context} 在 {len(failures)} 次退出码 226 后重新启动成功；失败诊断已保留。\n'
        return data, log


def _recovery_report(folder, warnings):
    path = Path(folder)/'engine_recoveries.json'
    recoveries = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if recoveries:
        locations = [a['diagnostic_path'] for a in recoveries if a.get('diagnostic_path')]
        note = f'本次发生 {len(recoveries)} 次求解器 226 异常，重新启动后完成；请关注运行环境。'
        if locations:
            note += ' 诊断目录：'+str(Path(locations[0]).parent)
        warnings.append(note)
    return recoveries


def run_simulation(layers, settings, cancel=None):
    validate_device(layers, settings)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='bandtool_simss_') as folder:
        mesh = write_inputs(folder, layers, settings)
        data, log = _run_engine(folder, settings, cancel, started)
        v, j, metrics, warnings = calculate_metrics(data['Vext'], -data['Jext']/10, settings['pin'])
        if mesh['sublayers'] > len(layers):
            warnings.append(f"渐变层已离散为 {mesh['sublayers']} 个总子层；建议增加分段数比较结果稳定性。")
        if mesh['effective_points'] != mesh['requested_points']:
            warnings.append(f"空间网格已自动增至 {mesh['effective_points']} 点以容纳子层。")
        if not np.all(np.isfinite(data['errJ'])):
            raise RuntimeError('SimSS 电流守恒误差无效，未采用此曲线。')
        optical = mesh.pop('optical')
        if optical['mode'] == 'spectrum':
            profile_data = np.atleast_1d(np.genfromtxt(Path(folder)/'OpticalProfile.dat', names=True))
            required = ('x', 'G_ehp', 'lid')
            if not set(required).issubset(profile_data.dtype.names or ()) or len(profile_data) != mesh['effective_points']+2:
                raise RuntimeError('光生分布输出不完整，未采用此次模拟。')
            x = profile_data['x']*1e9
            generation = profile_data['G_ehp']/1e6
            lid = np.asarray(profile_data['lid'], dtype=int)-1
            parents = np.array([p['parent_index'] for p in mesh['pieces']])[lid]
            if mesh['mirrored']:
                x = sum(a['thickness'] for a in layers)-x
                parents = len(layers)-1-parents
            order = np.argsort(x)
            x, generation, parents = x[order], generation[order], parents[order]
            if not np.all(np.isfinite([x, generation])) or np.any(generation < 0):
                raise RuntimeError('光生分布包含无效或负数值。')
            generated = float(1.602176634e-19*integrate(generation, x*1e-7)*1e3)
            optical.update(x_nm=x.tolist(), G_cm3_s=generation.tolist(), layer_index=parents.tolist())
            warnings.append(f"光生计算波段 {optical['settings']['wavelength_min']:g}–{optical['settings']['wavelength_max']:g} nm；效率分母采用完整入射光谱 {settings['pin']:g} mW/cm²。")
            if any(snap['source']['kind'] == 'builtin' for snap in optical['nk_snapshots']+optical['boundary_nk_snapshots']):
                warnings.append('部分光学层使用内置示例 n,k，请按实际材料替换。')
            linked = any(optical_model(a)['mode'] == 'edge' for a in layers)
            if linked:
                warnings.append('吸收边随局部 Eg（含电学渐变子层）近似联动，n 保持原谱；不是实测组分光学模型。')
            if mesh['sublayers'] > len(layers) and any(optical_model(a)['mode'] == 'fixed' and is_graded(a) for a in layers):
                warnings.append('部分电学渐变层使用固定 n,k，光学常数沿厚度保持相同。')
        else:
            generated = 1.602176634e-19*settings['G']*sum(a['thickness']*1e-7 for a in layers if a.get('absorber'))*1e3
        if metrics['Jsc'] is not None and metrics['Jsc'] > generated*1.01+1e-5:
            warnings.append('Jsc 高于 q∫Gdx 的光生电流参考值，请检查接触注入及数值收敛。')
        if metrics['PCE'] is not None and metrics['PCE'] > 100:
            warnings.append('效率超过 100%；请检查手动光生率与入射功率是否一致。')
        relative_error = float(np.max(data['errJ']/np.maximum(np.abs(data['Jint']), max(generated*10, 1e-12))))
        if relative_error > .005:
            warnings.append('归一化电流不均匀度超过 0.5%，建议增加网格点数并检查参数。')
        recoveries = _recovery_report(folder, warnings)
        return dict(voltage=v, current=j, metrics=metrics, warnings=warnings, runtime_recoveries=recoveries,
                    generated_current=generated, elapsed=time.monotonic()-started,
                    max_current_error=float(np.max(data['errJ'])),
                    relative_current_error=relative_error,
                    layers=deepcopy(layers), settings=dict(settings), log=log,
                    engine=ENGINE_VERSION, engine_build=ENGINE_BUILD, mesh=mesh, optics=optical,
                    losses=extract_losses(data, mesh, layers, settings))


def extract_losses(data, mesh, layers, settings):
    """Use native integrated currents, not a reconstructed SRH approximation."""
    order = np.argsort(data['Vext'])
    size = len(data)
    components = {k: np.zeros((len(layers), size)) for k in ('photo', 'bulk', 'direct')}
    interfaces = []
    for i, piece in enumerate(mesh['pieces']):
        parent = piece['parent_index']
        if mesh['mirrored']:
            parent = len(layers)-1-parent
        for key, native in [('photo', 'Jphoto'), ('bulk', 'Jbulk'), ('direct', 'Jdir')]:
            components[key][parent] += data[f'{native}L{i+1}'][order]/10
        if not piece['virtual_right'] and i < mesh['sublayers']-1:
            left = parent-1 if mesh['mirrored'] else parent
            interfaces.append(dict(left_index=left, name=layers[left]['name']+' / '+layers[left+1]['name'],
                                   current=(data[f'JintL{i+1}L{i+2}'][order]/10).tolist()))
    interfaces.sort(key=lambda a: a['left_index'])
    totals = {k: a.sum(axis=0) for k, a in components.items()}
    totals['interface'] = sum((np.asarray(a['current']) for a in interfaces), np.zeros(size))
    totals['collected'] = -data['Jext'][order]/10
    totals['shunt'] = data['JShunt'][order]/10
    totals['residual'] = totals['photo']-totals['collected']-totals['bulk']-totals['direct']-totals['interface']-totals['shunt']
    totals['series_power'] = totals['collected']**2*settings['rs']/1000
    if not all(np.all(np.isfinite(a)) for a in totals.values()):
        raise RuntimeError('损失分析输出包含无效数值')
    return dict(voltage=data['Vext'][order].tolist(), layer_names=[a['name'] for a in layers],
                layer_currents={k: a.tolist() for k, a in components.items()}, interfaces=interfaces,
                **{k: a.tolist() for k, a in totals.items()},
                units='mA/cm2 except series_power (mW/cm2)',
                note='净复合等效电流；residual 含接触/边界及离散残差，不能当作纯接触损失；不是效率损失百分比。')


def loss_at_voltage(result, voltage):
    loss = result['losses']
    v = loss['voltage']
    if voltage is None or not v[0] <= voltage <= v[-1]:
        raise ValueError('此工作点未被扫描覆盖，不能外推损失')
    keys = ('photo', 'bulk', 'direct', 'interface', 'collected', 'shunt', 'residual', 'series_power')
    return dict(voltage=float(voltage), **{k: float(np.interp(voltage, v, loss[k])) for k in keys},
                layer_currents={k: [float(np.interp(voltage, v, a)) for a in arrays]
                                for k, arrays in loss['layer_currents'].items()},
                interface_currents=[float(np.interp(voltage, v, a['current'])) for a in loss['interfaces']])


def validate_eqe(settings):
    if get_optics(settings)['mode'] != 'spectrum':
        raise ValueError('EQE 需要光谱 TMM 模式和各层 n,k 数据')
    e = dict(EQE_DEFAULTS) | settings.get('eqe', {})
    if not all(np.isfinite(e[k]) for k in EQE_DEFAULTS):
        raise ValueError('EQE 设置须为有限数值')
    if not 1 <= e['step_nm'] <= 100 or e['step_nm'] != round(e['step_nm']):
        raise ValueError('EQE 波长步长须为 1–100 nm 的整数')
    if not 1e10 <= e['photon_flux_cm2_s'] <= 1e17 or not 0 <= e['background_scale'] <= 10:
        raise ValueError('EQE 探测光子通量须为 1e10–1e17 cm⁻²/s，背景光比例为 0–10')
    o = get_optics(settings)
    wave = np.arange(o['wavelength_min'], o['wavelength_max'], e['step_nm'])
    wave = np.r_[wave, o['wavelength_max']]
    if len(wave) > 251:
        raise ValueError('EQE 最多 251 个波长点，请增大步长')
    return e, wave


def _rewrite_setup(folder, replacements):
    path = Path(folder)/'simulation_setup.txt'
    text = path.read_text(encoding='ascii')
    for key, value in replacements.items():
        value = format(value, '.16g') if isinstance(value, (int, float)) else str(value)
        text, count = re.subn(r'(?m)^'+re.escape(key)+r'\s*=\s*[^*\r\n]*',
                             lambda m: f'{key} = {value} ', text)
        if count != 1:
            raise RuntimeError('SimSS 模板缺少参数 '+key)
    path.write_text(text, encoding='ascii')


def _eqe_short_circuit(folder, settings, cancel, started, context, guess=0.):
    """Solve Vext = Vint + Rs*Jext = 0, retaining the actual series/shunt resistors."""
    last_voltage, last_data = None, None
    samples = {}
    def evaluate(voltage):
        nonlocal last_voltage, last_data
        voltage = float(voltage)
        if voltage not in samples:
            _rewrite_setup(folder, dict(Vmin=voltage, Vmax=voltage))
            data, _ = _run_engine(folder, settings, cancel, started, context=context)
            if len(data) != 1:
                raise RuntimeError('EQE 单工作点输出不匹配')
            samples[voltage] = data
            last_voltage, last_data = voltage, data
        return float(samples[voltage]['Vext'][0])
    guess = float(guess)
    initial = evaluate(guess)
    if abs(initial) <= 1e-10:
        return last_data, guess
    direction = -np.sign(initial)
    step = max(.02, min(abs(initial), .2))
    for _ in range(10):
        other = guess+direction*step
        residual = evaluate(other)
        if initial*residual <= 0:
            lo, hi = sorted((guess, other))
            root = brentq(evaluate, lo, hi, xtol=1e-12, rtol=1e-12, maxiter=40)
            if root != last_voltage:
                # The retained profile must belong to the accepted root too.
                samples.pop(root, None)
                evaluate(root)
            if abs(float(last_data['Vext'][0])) > 1e-9:
                raise RuntimeError('EQE 端电压未达到短路条件，请检查电阻和接触设置')
            return last_data, float(root)
        step *= 2
    raise RuntimeError('EQE 无法找到端电压为零的短路工作点，请检查电阻和接触设置')


def run_eqe(layers, settings, cancel=None, progress=None):
    """Differential short-circuit EQE: background + a single 1-nm photon bin.

    Same method as the upstream pySIMsalabim EQE workflow, with signed response
    and an explicit numerical-error estimate. No artificial clipping to 0..1.
    """
    validate_device(layers, settings)
    eqe, wavelengths = validate_eqe(settings)
    started = time.monotonic()
    q, h, c = 1.602176634e-19, 6.62607015e-34, 299792458.
    denominator = q*eqe['photon_flux_cm2_s']*1e3  # mA/cm2
    with tempfile.TemporaryDirectory(prefix='pds_eqe_') as folder:
        mesh = write_inputs(folder, layers, settings)
        optical = mesh.pop('optical')
        o = get_optics(settings)
        grid = np.arange(o['wavelength_min'], o['wavelength_max']+1.)
        spectrum = np.asarray(optical['spectrum_nm_W_m2_nm'])
        base = np.interp(grid, spectrum[:, 0], spectrum[:, 1])*1e9*eqe['background_scale']
        _rewrite_setup(folder, dict(Vmin=0, Vmax=0, Vstep=.01, outputRatio=1, varFile='OpticalProfile.dat'))

        short_circuit_guess = 0.
        def solve_spectrum(irradiance, context):
            nonlocal short_circuit_guess
            # Match the native rounded wavelength grid exactly: tiny positive
            # coordinate errors can make its linear interpolator miss a spike.
            np.savetxt(Path(folder)/'spectrum.txt', np.column_stack([grid*1e-9, irradiance]),
                       fmt=['%.9e', '%.16e'], header='lambda I', comments='')
            data, short_circuit_guess = _eqe_short_circuit(
                folder, settings, cancel, started, context, guess=short_circuit_guess)
            p = np.atleast_1d(np.genfromtxt(Path(folder)/'OpticalProfile.dat', names=True))
            if len(p) != mesh['effective_points']+2 or not np.all(np.isfinite(p['G_ehp'])):
                raise RuntimeError('EQE 光生分布不完整')
            return -float(data['Jext'][0])/10, float(data['errJ'][0])/10, p

        if progress:
            progress(0, len(wavelengths))
        baseline, base_error, base_profile = solve_spectrum(base, 'EQE 背景计算')
        responses, absorption, noise = [], [], []
        for i, wavelength in enumerate(wavelengths):
            modified = base.copy()
            index = int(round(wavelength-grid[0]))
            # Native TMM sums 1-nm rectangular bins, including both endpoints.
            modified[index] += eqe['photon_flux_cm2_s']*1e4*h*c/(wavelength*1e-9)/1e-9
            current, error, p = solve_spectrum(modified, f'EQE 第 {i+1}/{len(wavelengths)} 个波长（{wavelength:g} nm）')
            responses.append((current-baseline)/denominator)
            absorption.append(float(q*integrate(p['G_ehp']-base_profile['G_ehp'], p['x'])/10/denominator))
            noise.append((error+base_error)/denominator)
            if progress:
                progress(i+1, len(wavelengths))
        responses, absorption, noise = map(np.asarray, (responses, absorption, noise))
        if not np.all(np.isfinite([responses, absorption, noise])):
            raise RuntimeError('EQE 结果包含无效数值')
        photon_density = np.interp(grid, spectrum[:, 0], spectrum[:, 1])*grid*1e-9/(h*c)
        weighted = float(q*integrate(np.interp(grid, wavelengths, responses)*photon_density, grid)/10)
        warnings = ['短路微分 EQE：背景光上叠加 1 nm 探测光；请比较更小通量与更细波长步长。',
                    '光谱加权电流为微分响应参考，非线性收集时不保证等于 Jsc；不对 EQE 数值强行截断。']
        if np.max(noise) > .01:
            warnings.append('部分波长的数值误差估计超过 1 个百分点；请调整探测通量或网格。')
        if np.min(responses) < -.01 or np.max(responses) > 1.01:
            warnings.append('EQE 出现负值或超过 100%，请检查注入、增益及数值收敛。')
        recoveries = _recovery_report(folder, warnings)
        return dict(kind='eqe', wavelength_nm=wavelengths.tolist(), EQE=responses.tolist(), runtime_recoveries=recoveries,
                    active_absorptance=absorption.tolist(), numerical_error_estimate=noise.tolist(),
                    baseline_Jsc=baseline, weighted_current_mA_cm2=weighted, eqe_settings=eqe,
                    layers=deepcopy(layers), settings=deepcopy(settings), optics=optical,
                    elapsed=time.monotonic()-started, engine=ENGINE_VERSION, engine_build=ENGINE_BUILD,
                    warnings=warnings, mesh=mesh)
