"""Run: python app.py"""
import csv
import json
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('TkAgg')
from matplotlib import rcParams
rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
rcParams['axes.unicode_minus']=False
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from physics import (FIELDS, example, layer, solve, validate, normalize, alignment,
                     profile, shift_band, drawing_segments)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('钙钛矿能带小工具 · 能带拖动版')
        self.geometry('1320x880'); self.minsize(1080,760)
        self.layers=[normalize(a) for a in example()]; self.index=0; self.busy=False; self.pending=None
        self.result=None; self.snapshot=None
        self.drag=None; self.drag_history=[]; self.band_artists={}
        top=ttk.Frame(self,padding=10); top.pack(fill='x')
        for text,cmd in [('打开参数',self.load),('保存参数',self.save),('导出参数 CSV',self.export_params),
                         ('导出曲线 CSV',self.export_curve),('保存图片',self.export_image),
                         ('设为对照',self.compare),('清除对照',self.clear_compare),('模型说明',self.help)]:
            ttk.Button(top,text=text,command=cmd).pack(side='left',padx=3)
        body=ttk.Panedwindow(self,orient='horizontal'); body.pack(fill='both',expand=True,padx=10)
        left=ttk.Frame(body,width=345); right=ttk.Frame(body); body.add(left,weight=0); body.add(right,weight=1)
        ttk.Label(left,text='材料层（从左电极到右电极）',font=('Microsoft YaHei',11,'bold')).pack(anchor='w')
        self.listbox=tk.Listbox(left,height=5,exportselection=False,font=('Microsoft YaHei',10))
        self.listbox.pack(fill='x',pady=6); self.listbox.bind('<<ListboxSelect>>',self.select)
        buttons=ttk.Frame(left); buttons.pack(fill='x')
        for text,cmd in [('添加',self.add),('删除',self.remove),('上移',lambda:self.move(-1)),('下移',lambda:self.move(1))]:
            ttk.Button(buttons,text=text,width=7,command=cmd).pack(side='left',padx=2)
        notebook=ttk.Notebook(left); notebook.pack(fill='both',expand=True,pady=8)
        form=ttk.Frame(notebook,padding=(0,10)); notebook.add(form,text='基本参数')
        self.vars={}
        for row,(key,label,unit) in enumerate([('name','材料名称','')]+FIELDS):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=4)
            var=tk.StringVar(); self.vars[key]=var
            ttk.Entry(form,textvariable=var,width=17).grid(row=row,column=1,padx=5)
            ttk.Label(form,text=unit).grid(row=row,column=2,sticky='w')
            var.trace_add('write',self.schedule)
        grading=ttk.Frame(notebook,padding=10); notebook.add(grading,text='渐变设置')
        ttk.Label(grading,text='方向：本层左侧 → 本层右侧\n起点使用「基本参数」；均匀模式忽略终点。',justify='left').pack(anchor='w',pady=(0,6))
        for key,label,unit in [('Eg','带隙 Eg','eV'),('Nd','施主浓度 Nd','cm⁻³'),('Na','受主浓度 Na','cm⁻³')]:
            box=ttk.LabelFrame(grading,text=label+' · 右侧终点 '+unit,padding=6); box.pack(fill='x',pady=3)
            mode=tk.StringVar(value='均匀'); self.vars[key+'_mode']=mode
            ttk.Combobox(box,textvariable=mode,values=['均匀','线性'] if key=='Eg' else ['均匀','线性','对数'],state='readonly',width=9).grid(row=0,column=0,padx=3)
            end=tk.StringVar(); self.vars[key+'_end']=end
            ttk.Entry(box,textvariable=end,width=13).grid(row=0,column=1,padx=6)
            mode.trace_add('write',self.schedule); end.trace_add('write',self.schedule)
        box=ttk.LabelFrame(grading,text='电子亲和能 χ · 右侧终点 eV',padding=6);box.pack(fill='x',pady=3)
        affinity=tk.StringVar(value='随带隙分配');self.vars['affinity_mode']=affinity
        ttk.Combobox(box,textvariable=affinity,values=['随带隙分配','独立端点'],state='readonly',width=12).grid(row=0,column=0)
        chi_end=tk.StringVar();self.vars['chi_end']=chi_end
        self.chi_end_entry=ttk.Entry(box,textvariable=chi_end,width=13)
        self.chi_end_entry.grid(row=0,column=1,padx=6)
        affinity.trace_add('write',self.schedule);chi_end.trace_add('write',self.schedule)
        box=ttk.LabelFrame(grading,text='带隙分配（独立端点模式不使用 α）',padding=6);box.pack(fill='x',pady=6)
        fraction=tk.StringVar(value='0'); self.vars['band_fraction']=fraction
        ttk.Label(box,text='导带分配比例 α（0–1）').pack(anchor='w')
        self.fraction_entry=ttk.Entry(box,textvariable=fraction,width=12)
        self.fraction_entry.pack(anchor='w',pady=5)
        fraction.trace_add('write',self.schedule)
        ttk.Label(box,text='0：固定电子亲和能，只改变价带\n1：固定真空参考价带，只改变导带\n0.5：带隙变化由两条带边均分',justify='left').pack(anchor='w')
        ttk.Label(grading,text='拖动会切换到「独立端点」，同步 Eg、χ。\n掺杂线性允许零浓度；对数两端须为正。',justify='left').pack(anchor='w',pady=6)
        ttk.Label(left,text='迁移率与寿命会保存和导出；\n热平衡模式中不参与静电计算。\n示例参数仅用于演示，须替换为实测或文献值。',foreground='#805c16').pack(anchor='w',pady=6)
        controls=ttk.Frame(right); controls.pack(fill='x')
        self.mode=tk.StringVar(value='热平衡弯曲能带')
        combo=ttk.Combobox(controls,textvariable=self.mode,values=['热平衡弯曲能带','真空参考能级排列'],state='readonly',width=20)
        combo.pack(side='left',padx=4); combo.bind('<<ComboboxSelected>>',self.schedule)
        self.settings={}
        for key,label,default in [('T','温度 K','300'),('left','左功函数 eV','5.0'),('right','右功函数 eV','4.1')]:
            ttk.Label(controls,text=label).pack(side='left',padx=3)
            var=tk.StringVar(value=default); self.settings[key]=var
            ttk.Entry(controls,textvariable=var,width=7).pack(side='left'); var.trace_add('write',self.schedule)
        drag_controls=ttk.Frame(right);drag_controls.pack(fill='x',pady=(7,0))
        self.drag_enabled=tk.BooleanVar(value=True)
        ttk.Checkbutton(drag_controls,text='鼠标拖动能带',variable=self.drag_enabled).pack(side='left')
        ttk.Button(drag_controls,text='撤销拖动',command=self.undo_drag).pack(side='left',padx=6)
        ttk.Label(drag_controls,text='拖线：移动该带边  ·  拖圆点：改变渐变  ·  Esc：取消').pack(side='left')
        self.drag_summary=tk.StringVar()
        ttk.Label(right,textvariable=self.drag_summary,foreground='#245565',wraplength=800).pack(fill='x',pady=5)
        self.fig=Figure(figsize=(8,6),dpi=100); self.ax=self.fig.add_subplot(111)
        self.canvas=FigureCanvasTkAgg(self.fig,master=right); self.canvas.get_tk_widget().pack(fill='both',expand=True)
        self.toolbar=NavigationToolbar2Tk(self.canvas,right)
        self.canvas.mpl_connect('button_press_event',self.drag_press)
        self.canvas.mpl_connect('motion_notify_event',self.drag_motion)
        self.canvas.mpl_connect('button_release_event',self.drag_release)
        self.bind('<Escape>',self.cancel_drag)
        self.status=tk.StringVar(value='')
        ttk.Label(self,textvariable=self.status,wraplength=1250,padding=10,foreground='#245565').pack(fill='x')
        self.refresh_list(); self.show_fields(); self.calculate()

    def refresh_list(self):
        self.listbox.delete(0,'end')
        for a in self.layers:self.listbox.insert('end',f"{a['name']}   |   {a['thickness']:g} nm")
        self.listbox.selection_set(self.index)

    def show_fields(self):
        self.busy=True
        self.layers[self.index]=normalize(self.layers[self.index])
        for k,v in self.vars.items():v.set(self.layers[self.index][k] if k=='name' or k.endswith('_mode') else f'{self.layers[self.index][k]:.12g}')
        self.busy=False
        self.update_band_controls()

    def update_band_controls(self):
        independent=self.vars['affinity_mode'].get()=='独立端点'
        self.chi_end_entry.configure(state='normal' if independent else 'disabled')
        self.fraction_entry.configure(state='disabled' if independent else 'normal')
        a=self.layers[self.index];p=profile(a,[0,1])
        self.drag_summary.set(f"{a['name']}  |  左 → 右：Eg {p['Eg'][0]:.4f} → {p['Eg'][1]:.4f} eV；"
                              f"χ {p['chi'][0]:.4f} → {p['chi'][1]:.4f} eV")

    def commit(self):
        a={k:(v.get() if k=='name' or k.endswith('_mode') else float(v.get())) for k,v in self.vars.items()}
        temp=[dict(z) for z in self.layers]; temp[self.index]=a
        settings={k:float(v.get()) for k,v in self.settings.items()}
        validate(temp,settings['T'],settings['left'],settings['right'])
        self.layers=temp
        return settings

    def schedule(self,*_):
        if self.busy:return
        if self.drag:self.cancel_drag()
        self.drag_history.clear()
        self.update_band_controls()
        if self.pending:self.after_cancel(self.pending)
        self.result=None
        self.status.set('参数已变更，等待重新计算…')
        self.pending=self.after(600,self.calculate)

    def calculate(self):
        if self.pending:self.after_cancel(self.pending)
        self.pending=None
        try:
            s=self.commit(); self.refresh_list()
            self.update_band_controls()
            if self.mode.get()=='热平衡弯曲能带':
                r=solve(self.layers,s['T'],s['left'],s['right'])
                note=f"已收敛 · {r['iterations']} 次迭代 · 归一化残差 {r['error']:.1e} V。"+'；'.join(r['warnings'])
            else:
                r=alignment(self.layers)
                note='真空参考排列：不含接触后的电荷转移与能带弯曲。'
            r['mode']=self.mode.get(); self.result=r; self.plot()
            self.status.set(note+'  模型：一维、暗态、零外加偏压；不含离子、陷阱、界面偶极。')
            return True
        except Exception as e:
            self.result=None; self.ax.set_title('当前输入无有效结果 · 图中保留上一次曲线',color='#bd452c'); self.canvas.draw_idle()
            self.status.set('未更新：'+str(e)); return False

    def plot(self):
        r=self.result; ax=self.ax; ax.clear(); pos=0
        self.band_artists={}
        colors=['#e4edf8','#f9e9d4','#e0f0e6','#efe5f5']
        for i,a in enumerate(self.layers):
            end=pos+a['thickness']; ax.axvspan(pos,end,color=colors[i%4],alpha=.7)
            ax.text((pos+end)/2,1.015,a['name'],ha='center',transform=ax.get_xaxis_transform(),fontsize=9,rotation=15)
            ax.axvline(end,color='#aab4bd',lw=.7); pos=end
        # Draw each layer separately to avoid interpolating interface discontinuities.
        self.segments=drawing_segments(self.layers,r,float(self.settings['left'].get()),float(self.settings['right'].get()))
        for i,segment in enumerate(self.segments):
            for key,color in [('Ec','#2279b5'),('Ev','#cd6930')]:
                line,=ax.plot(segment['x'],segment[key],color=color,lw=2,
                              marker='o',markevery=[0,-1],markersize=6 if i==self.index else 4,
                              markerfacecolor='white',clip_on=False,label=key if i==0 else None)
                self.band_artists[i,key]=line
        if r['mode']=='热平衡弯曲能带':ax.axhline(0,color='#537357',ls='--',label='EF = 0 eV')
        else:ax.axhline(0,color='#537357',ls='--',label='真空能级 = 0 eV')
        if self.snapshot and self.snapshot['mode']==r['mode']:
            for key,color in [('Ec','#2279b5'),('Ev','#cd6930')]:
                ax.plot(self.snapshot['x'],self.snapshot[key],color=color,ls=':',alpha=.65,label=key+' 对照')
        ax.set(xlabel='位置 x (nm)',ylabel='能量 (eV)',xlim=(0,pos))
        energies=[np.array([0.])]+[s[k] for s in self.segments for k in ('Ec','Ev')]
        if self.snapshot and self.snapshot['mode']==r['mode']:
            energies.extend([self.snapshot['Ec'],self.snapshot['Ev']])
        low=min(float(np.min(v)) for v in energies);high=max(float(np.max(v)) for v in energies)
        margin=max(.15,.12*(high-low));ax.set_ylim(low-margin,high+margin)
        ax.grid(alpha=.18); ax.legend(loc='best')
        self.fig.tight_layout(pad=2.4); self.canvas.draw_idle()

    def drag_target(self,event):
        """Pixel-space hit testing keeps narrow layers and endpoints usable."""
        if event.inaxes!=self.ax or event.x is None or event.y is None:return None
        mouse=np.array([event.x,event.y]); ends=[]; lines=[]
        for (i,key),line in self.band_artists.items():
            xy=self.ax.transData.transform(np.column_stack(line.get_data()))
            for j,part in ((0,'left'),(-1,'right')):
                distance=np.linalg.norm(mouse-xy[j])
                if distance<=9:ends.append((distance,i,key,part))
            a=xy[:-1];v=xy[1:]-a
            fraction=np.clip(np.sum((mouse-a)*v,axis=1)/np.maximum(np.sum(v*v,axis=1),1e-20),0,1)
            distance=np.min(np.linalg.norm(mouse-(a+fraction[:,None]*v),axis=1))
            if distance<=7:lines.append((distance,i,key,'whole'))
        matches=ends or lines
        return min(matches,key=lambda z:(z[0],z[1]!=self.index)) if matches else None

    def drag_press(self,event):
        if event.button!=1 or not self.drag_enabled.get() or self.toolbar.mode or self.result is None:return
        target=self.drag_target(event)
        if target is None:return
        _,i,key,part=target
        if self.pending:self.after_cancel(self.pending);self.pending=None
        self.index=i;self.refresh_list();self.show_fields()
        self.drag=dict(before=deepcopy(self.layers),result=self.result,index=i,band=key,part=part,
                       y=event.ydata,segments=deepcopy(self.segments),changed=False)
        self.canvas.get_tk_widget().focus_set()
        self.canvas.get_tk_widget().configure(cursor='sb_v_double_arrow')
        self.status.set('上下拖动调整带边；左侧参数实时更新。松开求解；Esc 取消。')

    def drag_motion(self,event):
        if not self.drag:
            if not self.toolbar.mode:
                active=self.drag_enabled.get() and self.result is not None and self.drag_target(event) is not None
                self.canvas.get_tk_widget().configure(cursor='hand2' if active else '')
            return
        if event.inaxes!=self.ax or event.ydata is None:return
        d=self.drag;delta=event.ydata-d['y']
        if abs(delta)<1e-12 and not d['changed']:return
        try:
            candidate=shift_band(d['before'][d['index']],d['band'],d['part'],delta)
            validate([candidate],float(self.settings['T'].get()),float(self.settings['left'].get()),float(self.settings['right'].get()))
        except ValueError as e:
            self.status.set('已到参数边界，保留最近有效预览：'+str(e));return
        self.layers[d['index']]=candidate;self.show_fields();d['changed']=True
        # Frozen-potential preview; keep the axes fixed throughout the gesture.
        segment=d['segments'][d['index']]
        t=(segment['x']-segment['x'][0])/candidate['thickness']
        weight=1-t if d['part']=='left' else t if d['part']=='right' else np.ones_like(t)
        self.band_artists[d['index'],d['band']].set_ydata(segment[d['band']]+delta*weight)
        self.ax.set_title('拖动预览：静电势暂固定，松开后重新求解' if 'phi' in d['result'] else '拖动预览：带边与材料参数同步',fontsize=10)
        self.status.set(f"{candidate['name']} · {d['band']} · 位移 {delta:+.4f} eV；已同步 Eg、χ 起终点。Esc 取消。")
        self.canvas.draw_idle()

    def drag_release(self,event):
        if not self.drag:return
        d=self.drag;self.drag=None
        self.canvas.get_tk_widget().configure(cursor='')
        if not d['changed']:return
        if self.calculate():
            self.drag_history.append((d['before'],deepcopy(self.layers),d['index']))
            self.drag_history=self.drag_history[-30:]
            self.status.set(self.status.get()+' 拖动已应用；可撤销。'+('自洽电势调整后，最终曲线可能偏离拖动预览。' if 'phi' in d['result'] else ''))
        else:
            error=self.status.get();self.layers=d['before'];self.show_fields();self.calculate()
            self.status.set('拖动未能求解，已恢复拖动前参数。'+error)

    def cancel_drag(self,event=None):
        if not self.drag:return
        d=self.drag;self.drag=None;self.layers=d['before'];self.result=d['result']
        self.show_fields();self.refresh_list();self.plot()
        self.canvas.get_tk_widget().configure(cursor='')
        self.status.set('已取消本次拖动，参数与能带已恢复。')

    def undo_drag(self):
        if self.drag:self.cancel_drag();return
        if not self.drag_history:
            self.status.set('没有可撤销的拖动；手动修改参数或层结构后，拖动撤销记录会清空。');return
        before,after,index=self.drag_history.pop()
        if self.layers!=after:
            self.drag_history.clear();self.status.set('参数已另外修改，不再撤销此前拖动。');return
        self.layers=before;self.index=index;self.show_fields();self.refresh_list();self.calculate()
        self.status.set(self.status.get()+' 已撤销一次拖动。')

    def select(self,event=None):
        chosen=self.listbox.curselection()
        if not chosen or chosen[0]==self.index:return
        try:self.commit()
        except Exception as e:
            messagebox.showerror('请先修正参数',str(e));self.refresh_list();return
        self.index=chosen[0]; self.show_fields(); self.calculate()

    def add(self):
        if not self.calculate():return
        self.drag_history.clear()
        self.layers.append(layer('新材料',50,10,1.6,4));self.index=len(self.layers)-1
        self.refresh_list();self.show_fields();self.calculate()

    def remove(self):
        if len(self.layers)==1:return
        self.drag_history.clear()
        self.layers.pop(self.index); self.index=min(self.index,len(self.layers)-1)
        self.refresh_list();self.show_fields();self.calculate()

    def move(self,direction):
        target=self.index+direction
        if not 0<=target<len(self.layers) or not self.calculate():return
        self.drag_history.clear()
        self.layers[self.index],self.layers[target]=self.layers[target],self.layers[self.index]
        self.index=target;self.refresh_list();self.show_fields();self.calculate()

    def save(self):
        try:s=self.commit()
        except Exception as e:messagebox.showerror('参数错误',str(e));return
        p=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('参数项目','*.json')])
        if p:Path(p).write_text(json.dumps(dict(schema=3,layers=self.layers,settings=s,mode=self.mode.get()),ensure_ascii=False,indent=2),encoding='utf-8')

    def load(self):
        p=filedialog.askopenfilename(filetypes=[('参数项目','*.json')])
        if not p:return
        try:
            data=json.loads(Path(p).read_text(encoding='utf-8-sig'));s=data['settings']
            if data.get('schema') not in (1,2,3):raise ValueError('不支持的文件版本')
            data['layers']=[normalize(a) for a in data['layers']]
            validate(data['layers'],s['T'],s['left'],s['right'])
            self.busy=True;self.layers=data['layers'];self.index=0
            for k,v in self.settings.items():v.set(s[k])
            self.mode.set(data.get('mode','热平衡弯曲能带'));self.snapshot=None
            self.drag_history.clear()
            self.busy=False;self.refresh_list();self.show_fields();self.calculate()
        except Exception as e:self.busy=False;messagebox.showerror('打开失败',str(e))

    def export_params(self):
        try:self.commit()
        except Exception as e:messagebox.showerror('参数错误',str(e));return
        p=filedialog.asksaveasfilename(defaultextension='.csv')
        if p:
            with open(p,'w',newline='',encoding='utf-8-sig') as f:
                extra=['Eg_mode','Eg_end','Nd_mode','Nd_end','Na_mode','Na_end','band_fraction','affinity_mode','chi_end']
                w=csv.writer(f);w.writerow(['材料']+[f'{label} ({unit})' for _,label,unit in FIELDS]+['Eg渐变','Eg右端_eV','Nd渐变','Nd右端_cm-3','Na渐变','Na右端_cm-3','导带分配比例','电子亲和能模式','独立χ右端_eV'])
                for a in self.layers:
                    a=normalize(a);w.writerow([a['name']]+[a[k] for k,_,_ in FIELDS]+[a[k] for k in extra])

    def export_curve(self):
        if not self.calculate():return
        p=filedialog.asksaveasfilename(defaultextension='.csv')
        if p:
            r=self.result;keys=['x','Ec','Ev']+(['phi','n','p'] if 'phi' in r else [])
            header=['x_nm','Ec_eV','Ev_eV']+(['phi_V','n_cm-3','p_cm-3'] if 'phi' in r else [])
            keys+=['Eg','chi','Nd','Na'];header+=['Eg_eV','chi_eV','Nd_cm-3','Na_cm-3']
            np.savetxt(p,np.column_stack([r[k] for k in keys]),delimiter=',',header=','.join(header),comments='')

    def export_image(self):
        if not self.calculate():return
        p=filedialog.asksaveasfilename(defaultextension='.png',filetypes=[('PNG','*.png'),('SVG','*.svg')])
        if p:self.fig.savefig(p,dpi=220,bbox_inches='tight')

    def compare(self):
        if self.calculate():self.snapshot={k:v.copy() if isinstance(v,np.ndarray) else v for k,v in self.result.items()};self.plot()

    def clear_compare(self):self.snapshot=None;self.calculate()

    def help(self):
        messagebox.showinfo('适用范围',
            '这是参数探索工具，不是完整性能模拟器。\n\n'
            '热平衡：一维泊松-玻尔兹曼方程，完全电离浅掺杂；电子/空穴共享 EF=0。'
            '接触采用功函数确定的 Dirichlet 电势边界。假设电子亲和能规则，无界面偶极或固定电荷。\n\n'
            '不含光照、外加偏压、离子迁移、陷阱电荷、复合与电流输运；不能预测 J–V 或效率。'
            '不适于把 SAM 直接当普通体层精确模拟。高掺杂/强积累时玻尔兹曼统计可能失效。\n\n'
            '参数全部保留，但迁移率、寿命不进入平衡静电方程。不能从单张能带图唯一反演它们。'
            '此版没有自动图像识别或反演。\n\n'
            '鼠标拖线会移动该层的一条带边；拖端点圆点会改变渐变，保持另一条带边的真空参考位置。'
            '拖动后采用独立电子亲和能端点；掺杂、迁移率和寿命保持原值。'
            '平衡态拖动期间暂固定电势，松开后自洽求解，最终能带可能偏离预览；拖动不等于参数反演。'
            'Esc 取消；按钮可撤销。缩放/平移工具启用时暂停能带拖动。\n\n'
            '单位：厚度 nm；能量 eV；态密度/掺杂 cm⁻³；迁移率 cm²/(V·s)；寿命 s。'
            '导出的参数表不是 SCAPS 原生文件。')

if __name__=='__main__':App().mainloop()
