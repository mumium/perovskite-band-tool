"""1D equilibrium Poisson-Boltzmann model; SI internally, eV for energies."""
import numpy as np
from scipy.linalg import solve_banded

FIELDS = [
    ('thickness', '厚度', 'nm'), ('eps', '相对介电常数', '1'),
    ('Eg', '带隙 Eg', 'eV'), ('chi', '电子亲和能 χ', 'eV'),
    ('Nv', '价带有效态密度 Nv', 'cm⁻³'), ('Nc', '导带有效态密度 Nc', 'cm⁻³'),
    ('mun', '电子迁移率', 'cm²/(V·s)'), ('mup', '空穴迁移率', 'cm²/(V·s)'),
    ('Nd', '施主浓度 Nd', 'cm⁻³'), ('Na', '受主浓度 Na', 'cm⁻³'),
    ('taun', '电子 SRH 寿命', 's'), ('taup', '空穴 SRH 寿命', 's')]

def normalize(a):
    a = dict(a)
    for key in ('Eg', 'Nd', 'Na'):
        a.setdefault(key+'_mode', '均匀')
        a.setdefault(key+'_end', a[key])
    a.setdefault('band_fraction', 0.)
    a.setdefault('affinity_mode', '随带隙分配')
    a.setdefault('chi_end', a['chi'])
    return a

def profile(a, t):
    """t is local fractional depth; band_fraction assigns gap change to Ec."""
    a=normalize(a); t=np.asarray(t)
    out={k:np.full(t.shape,a[k],dtype=float) for k in ('eps','chi','Eg','Nc','Nv','Nd','Na')}
    for key in ('Eg','Nd','Na'):
        mode=a[key+'_mode']; start=a[key]; end=a[key+'_end']
        if mode=='线性':out[key]=start+(end-start)*t
        elif mode=='对数':out[key]=np.exp(np.log(start)+(np.log(end)-np.log(start))*t)
    if a['affinity_mode']=='独立端点':
        out['chi']=a['chi']+(a['chi_end']-a['chi'])*t
    else:
        out['chi']=a['chi']-a['band_fraction']*(out['Eg']-a['Eg'])
    return out

def shift_band(a, band, part, delta):
    """Shift one vacuum-reference band edge, preserving the opposite band.

    During equilibrium dragging this is a frozen-potential edit, not inversion.
    Endpoints are geometrical layer endpoints, not finite-volume cell centres.
    """
    if band not in ('Ec','Ev') or part not in ('left','right','whole'):
        raise ValueError('无效的能带拖动目标')
    if not np.isfinite(delta):raise ValueError('拖动位移必须为有限数值')
    a=normalize(a); p=profile(a,[0,1])
    ec=-p['chi']; ev=ec-p['Eg']
    weight={'left':np.array([1.,0.]),'right':np.array([0.,1.]),'whole':np.ones(2)}[part]
    if band=='Ec':ec=ec+delta*weight
    else:ev=ev+delta*weight
    gap=ec-ev; chi=-ec
    if np.any(gap<=0):raise ValueError('带隙须大于 0，导带与价带不能交叉')
    if np.any(chi<0):raise ValueError('电子亲和能须大于或等于 0')
    a.update(Eg=float(gap[0]),Eg_end=float(gap[1]),
             Eg_mode='均匀' if abs(gap[1]-gap[0])<1e-10 else '线性',
             chi=float(chi[0]),chi_end=float(chi[1]),affinity_mode='独立端点')
    return a

def drawing_segments(layers, result, left=5., right=4.1):
    """Add physical endpoints for plotting and dragging cell-centred solutions."""
    segments=[]; offset=0; start=0.
    if 'phi' in result:
        faces=[-left]
        for i in range(len(layers)-1):
            offset+=int(result['counts'][i])
            a,b=layers[i],layers[i+1]
            ga=a['eps']/(a['thickness']/result['counts'][i])
            gb=b['eps']/(b['thickness']/result['counts'][i+1])
            faces.append((ga*result['phi'][offset-1]+gb*result['phi'][offset])/(ga+gb))
        faces.append(-right)
    offset=0
    for i,(a,count) in enumerate(zip(layers,result['counts'])):
        sl=slice(offset,offset+count); end=start+a['thickness']
        if 'phi' in result:
            xx=np.r_[start,result['x'][sl],end]
            potential=np.r_[faces[i],result['phi'][sl],faces[i+1]]
            p=profile(a,(xx-start)/a['thickness'])
            ec=-p['chi']-potential; ev=ec-p['Eg']
        else:
            xx=result['x'][sl];ec=result['Ec'][sl];ev=result['Ev'][sl]
        segments.append(dict(x=xx,Ec=ec,Ev=ev))
        start=end; offset+=count
    return segments

def alignment(layers):
    parts=[]; positions=[]; start=0
    for a in layers:
        t=np.linspace(0,1,121); parts.append(profile(a,t))
        positions.append(start+t*a['thickness']); start+=a['thickness']
    props={k:np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return dict(x=np.concatenate(positions),Ec=-props['chi'],Ev=-props['chi']-props['Eg'],
                Eg=props['Eg'],chi=props['chi'],Nd=props['Nd'],Na=props['Na'],counts=[121]*len(layers))

def layer(name, thickness, eps, Eg, chi, Nd=0, Na=0):
    return dict(name=name, thickness=thickness, eps=eps, Eg=Eg, chi=chi,
                Nv=1e19, Nc=1e19, mun=10., mup=10., Nd=Nd, Na=Na,
                taun=1e-6, taup=1e-6)

def example():
    return [layer('HTL（示例）', 20, 10, 3, 2, Na=1e17),
            layer('钙钛矿（示例）', 500, 25, 1.55, 3.9),
            layer('ETL（示例）', 30, 4, 2.3, 4.0, Nd=1e17)]

def validate(layers, temperature, left, right):
    if not layers: raise ValueError('至少需要一层材料')
    if not 100 <= temperature <= 600: raise ValueError('温度应在 100–600 K 内')
    if not all(np.isfinite(v) and 0 < v < 10 for v in (left, right)):
        raise ValueError('电极功函数必须在 0–10 eV 内')
    for a in layers:
        a=normalize(a)
        if not a['name'].strip(): raise ValueError('材料名不能为空')
        for key, label, unit in FIELDS:
            value = a[key]
            if not np.isfinite(value) or value < 0 or (key not in ('Nd', 'Na', 'chi') and value == 0):
                raise ValueError(f"{a['name']}：{label} 必须为有限正数（掺杂与亲和能允许为零）")
        for key in ('Eg','Nd','Na'):
            mode=a[key+'_mode']; end=a[key+'_end']
            if mode not in (('均匀','线性') if key=='Eg' else ('均匀','线性','对数')):
                raise ValueError('不支持的渐变类型')
            if not np.isfinite(end) or end<0 or (key=='Eg' and end==0):
                raise ValueError(key+' 终点数值无效')
            if mode=='对数' and (a[key]<=0 or end<=0):
                raise ValueError(key+' 对数渐变的起点与终点必须大于零')
        if not np.isfinite(a['band_fraction']) or not 0<=a['band_fraction']<=1:
            raise ValueError('导带分配比例必须在 0–1 之间')
        if a['affinity_mode'] not in ('随带隙分配','独立端点'):
            raise ValueError('不支持的电子亲和能模式')
        if not np.isfinite(a['chi_end']) or a['chi_end']<0:
            raise ValueError('电子亲和能右侧终点必须为有限非负数')
        if np.any(profile(a,[0,1])['chi']<0):raise ValueError('带隙渐变导致负电子亲和能，请调整带边设置')

def solve(layers, temperature=300, left=5.0, right=4.1, points=120):
    validate(layers, temperature, left, right)
    # Cell-centred, layer-aligned finite volumes. Exact layer boundaries are retained.
    widths = np.array([a['thickness'] for a in layers]) * 1e-9
    counts = np.maximum(12, np.ceil(points * widths / widths.sum()).astype(int))
    dx = np.concatenate([np.full(n, w/n) for n,w in zip(counts,widths)])
    edges = np.r_[0., np.cumsum(dx)]
    x = (edges[:-1]+edges[1:])/2
    samples=[profile(a,(np.arange(n)+.5)/n) for n,a in zip(counts,layers)]
    props = {k: np.concatenate([p[k] for p in samples])
             for k in ('eps','chi','Eg','Nc','Nv','Nd','Na')}
    eps = props['eps'] * 8.8541878128e-12
    g = 1 / (dx[:-1]/(2*eps[:-1])+dx[1:]/(2*eps[1:]))
    gl, gr = 2*eps[0]/dx[0], 2*eps[-1]/dx[-1]
    diagonal = -np.r_[gl,g] - np.r_[g,gr]
    vt = 8.617333262e-5 * temperature
    q = 1.602176634e-19
    def state(phi):
        n = props['Nc']*1e6*np.exp(np.clip((props['chi']+phi)/vt,-150,80))
        p = props['Nv']*1e6*np.exp(np.clip((-props['chi']-phi-props['Eg'])/vt,-150,80))
        return n,p
    def residual(phi):
        n,p=state(phi)
        r=diagonal*phi + q*dx*(p-n+1e6*(props['Nd']-props['Na']))
        r[:-1]+=g*phi[1:]; r[1:]+=g*phi[:-1]
        r[0]-=gl*left; r[-1]-=gr*right
        return r
    phi = -left+(left-right)*x/edges[-1]
    scale = np.maximum(-diagonal, 1e-30)
    for iteration in range(500):
        r=residual(phi)
        error=np.max(np.abs(r)/scale)
        if error < 1e-9: break
        n,p=state(phi)
        jac=np.zeros((3,len(x)))
        jac[0,1:]=g; jac[2,:-1]=g
        jac[1]=diagonal-q*dx*(n+p)/vt
        step=solve_banded((1,1),jac,-r)
        damping=min(1., .15/max(np.max(np.abs(step)),1e-30))
        for _ in range(25):
            trial=phi+damping*step
            if np.max(np.abs(residual(trial))/scale)<error: break
            damping*=.5
        phi=trial
    else: raise ValueError('静电求解未收敛；请减小参数跨度或调整接触功函数')
    n,p=state(phi)
    Ec=-props['chi']-phi
    warnings=[]
    if np.any(n > .1*props['Nc']*1e6) or np.any(p > .1*props['Nv']*1e6):
        warnings.append('部分区域接近简并，玻尔兹曼近似精度有限')
    return dict(x=x*1e9, Ec=Ec, Ev=Ec-props['Eg'], phi=phi,
                n=n/1e6,p=p/1e6, iterations=iteration, error=error,
                warnings=warnings, counts=counts,Eg=props['Eg'],chi=props['chi'],Nd=props['Nd'],Na=props['Na'])
