"""Desktop interface. Run from the project root with: python run.py."""
import csv
import json
import sys
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
from .physics import (FIELDS, example, layer, solve, validate, normalize, alignment,
                     profile, shift_band)
from .analysis_views import analysis_segments, interface_offsets, sample_position
from .performance_page import PerformancePage
from .app_info import APP_NAME, APP_ENGLISH_NAME
from .ui_helpers import ScrollableFrame, FlowToolbar, configure_theme
from .device_structure import DeviceStructure, preview_layers
from .paths import RESOURCE_ROOT

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        assets = RESOURCE_ROOT / 'assets'
        self._app_icon = tk.PhotoImage(file=str(assets / 'logo.png'))
        self.iconphoto(True, self._app_icon)
        if sys.platform == 'win32':
            self.iconbitmap(str(assets / 'logo.ico'))
        self.title(f'{APP_NAME} · {APP_ENGLISH_NAME}')
        width=min(1320,max(900,self.winfo_screenwidth()-50))
        height=min(880,max(560,self.winfo_screenheight()-110))
        self.geometry(f'{width}x{height}'); self.minsize(900,560)
        configure_theme(self)
        self.layers=[normalize(a) for a in example()]; self.index=0; self.busy=False; self.pending=None
        self.result=None; self.snapshot=None
        self.drag=None; self.drag_history=[]; self.band_artists={}
        self.pages=ttk.Notebook(self,style='Main.TNotebook');self.pages.pack(fill='both',expand=True)
        band_page=ttk.Frame(self.pages);self.pages.add(band_page,text='能带模拟')
        top=FlowToolbar(band_page,padding=(12,8)); top.pack(fill='x')
        top.button('打开参数',self.load)
        top.button('保存参数',self.save)
        top.menu('导出',[('参数 CSV',self.export_params),('当前曲线 CSV',self.export_curve),('当前图像 PNG / SVG',self.export_image)])
        top.button('设为对照',self.compare)
        top.button('清除对照',self.clear_compare)
        top.button('模型说明',self.help)
        body=ttk.Panedwindow(band_page,orient='horizontal'); body.pack(fill='both',expand=True,padx=10)
        left=ttk.Frame(body,width=350); right=ttk.Frame(body); body.add(left,weight=0); body.add(right,weight=1)
        # Set the initial split after the notebook has assigned its actual size.
        def initial_split(event):
            body.unbind('<Map>')
            self.after_idle(lambda:body.sashpos(0,350))
        body.bind('<Map>',initial_split)
        ttk.Label(left,text='材料层（从左电极到右电极）',font=('Microsoft YaHei',11,'bold')).pack(anchor='w')
        self.listbox=tk.Listbox(left,height=5,exportselection=False,font=('Microsoft YaHei',10),
                                bg='#f6f9fc',fg='#223b4b',selectbackground='#087e8b',selectforeground='white',
                                relief='flat',highlightthickness=1,highlightbackground='#dce5ec')
        self.listbox.pack(fill='x',pady=6); self.listbox.bind('<<ListboxSelect>>',self.select)
        buttons=ttk.Frame(left); buttons.pack(fill='x')
        for text,cmd in [('添加',self.add),('删除',self.remove),('上移',lambda:self.move(-1)),('下移',lambda:self.move(1))]:
            ttk.Button(buttons,text=text,width=5,command=cmd).pack(side='left',padx=2)
        notebook=ttk.Notebook(left); notebook.pack(fill='both',expand=True,pady=8)
        form_scroll=ScrollableFrame(notebook,width=325);notebook.add(form_scroll,text='基本参数')
        form=form_scroll.content
        self.vars={}
        for row,(key,label,unit) in enumerate([('name','材料名称','')]+FIELDS):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=4)
            var=tk.StringVar(); self.vars[key]=var
            ttk.Entry(form,textvariable=var,width=12).grid(row=row,column=1,padx=5)
            ttk.Label(form,text=unit).grid(row=row,column=2,sticky='w')
            var.trace_add('write',self.schedule)
        grading_scroll=ScrollableFrame(notebook,width=325);notebook.add(grading_scroll,text='渐变设置')
        grading=grading_scroll.content
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
        note=ttk.Label(left,text='迁移率与寿命用于性能页，热平衡页只保存。\n示例参数仅用于演示。',foreground='#805c16')
        note.pack(side='bottom',anchor='w',pady=4,before=notebook)
        self.structure_direction=tk.StringVar(value='左侧入射')
        self.structure=DeviceStructure(body,direction_variable=self.structure_direction)
        self.structure.pack(in_=right,fill='x',pady=(0,4))
        self.structure.host=right
        self.structure_direction.trace_add('write',self.update_structure)
        controls=ttk.LabelFrame(right,text='求解条件',padding=10); controls.pack(fill='x',pady=(0,7))
        self.mode=tk.StringVar(value='热平衡弯曲能带')
        ttk.Label(controls,text='能带模型').grid(row=0,column=0,sticky='w',padx=3)
        combo=ttk.Combobox(controls,textvariable=self.mode,values=['热平衡弯曲能带','真空参考能级排列'],state='readonly',width=20)
        combo.grid(row=1,column=0,sticky='ew',padx=3); combo.bind('<<ComboboxSelected>>',self.schedule)
        controls.columnconfigure(0,weight=2)
        self.settings={}
        for col,(key,label,default) in enumerate([('T','温度 / K','300'),('left','左功函数 / eV','5.0'),('right','右功函数 / eV','4.1')],1):
            ttk.Label(controls,text=label).grid(row=0,column=col,sticky='w',padx=6)
            var=tk.StringVar(value=default); self.settings[key]=var
            ttk.Entry(controls,textvariable=var,width=7).grid(row=1,column=col,sticky='ew',padx=6)
            controls.columnconfigure(col,weight=1);var.trace_add('write',self.schedule)
        view_controls=FlowToolbar(right);view_controls.pack(fill='x',pady=(0,4))
        self.view=tk.StringVar(value='能带')
        self.view_combo=ttk.Combobox(view_controls,textvariable=self.view,
                                    values=['能带','电势','电场','载流子浓度'],state='readonly',width=12)
        view_controls.add(self.view_combo)
        self.view_combo.bind('<<ComboboxSelected>>',self.change_view)
        self.drag_enabled=tk.BooleanVar(value=True)
        view_controls.add(ttk.Checkbutton(view_controls,text='拖动带边',variable=self.drag_enabled))
        view_controls.button('撤销拖动',self.undo_drag)
        self.show_offsets=tk.BooleanVar(value=True)
        view_controls.add(ttk.Checkbutton(view_controls,text='界面 ΔEc / ΔEv',variable=self.show_offsets,command=self.change_view))
        self.drag_summary=tk.StringVar()
        drag_label=ttk.Label(right,textvariable=self.drag_summary,foreground='#245565',wraplength=800)
        drag_label.pack(fill='x',pady=5)
        self.hover_text=tk.StringVar(value='将鼠标移到图中查看该位置的数值。\n')
        hover_label=ttk.Label(right,textvariable=self.hover_text,foreground='#245565',wraplength=850,justify='left')
        hover_label.pack(fill='x',pady=(0,5))
        def resize_band_right(event):
            for widget in (drag_label,hover_label):widget.configure(wraplength=max(200,event.width-20))
            self.structure.fit_height(event.height,left,right,notebook,controls)
        right.bind('<Configure>',resize_band_right)
        self.fig=Figure(figsize=(8,6),dpi=100); self.fig.set_layout_engine('tight',pad=1.5)
        self.ax=self.fig.add_subplot(111)
        self.canvas=FigureCanvasTkAgg(self.fig,master=right)
        self.canvas.get_tk_widget().configure(width=250,height=140)
        self.toolbar=NavigationToolbar2Tk(self.canvas,right,pack_toolbar=False)
        self.toolbar.pack(side='bottom',fill='x')
        self.canvas.get_tk_widget().pack(fill='both',expand=True)
        self.canvas.mpl_connect('button_press_event',self.drag_press)
        self.canvas.mpl_connect('motion_notify_event',self.drag_motion)
        self.canvas.mpl_connect('motion_notify_event',self.hover_motion)
        self.canvas.mpl_connect('figure_leave_event',self.clear_hover)
        self.canvas.mpl_connect('button_release_event',self.drag_release)
        self.bind('<Escape>',self.cancel_drag)
        self.status=tk.StringVar(value='')
        status_label=ttk.Label(band_page,textvariable=self.status,wraplength=1250,padding=6,foreground='#245565')
        status_label.pack(side='bottom',fill='x',before=body)
        band_page.bind('<Configure>',lambda e:status_label.configure(wraplength=max(300,e.width-24)))
        self.refresh_list(); self.show_fields(); self.calculate()
        self.performance=PerformancePage(self.pages,self)
        self.pages.add(self.performance,text='器件性能模拟')

    def destroy(self):
        if hasattr(self,'performance'):self.performance.shutdown()
        if self.pending:
            self.after_cancel(self.pending);self.pending=None
        super().destroy()

    def refresh_list(self):
        self.listbox.delete(0,'end')
        for a in self.layers:self.listbox.insert('end',f"{a['name']}   |   {a['thickness']:g} nm")
        self.listbox.selection_set(self.index)
        self.listbox.see(self.index)
        self.update_structure()

    def update_structure(self,*_):
        if hasattr(self,'structure'):
            layers=preview_layers(self.layers,self.index,self.vars) if hasattr(self,'vars') else self.layers
            self.structure.set_layers(layers,'right' if self.structure_direction.get()=='右侧入射' else 'left',self.index)

    def show_fields(self):
        self.busy=True
        self.layers[self.index]=normalize(self.layers[self.index])
        for k,v in self.vars.items():v.set(self.layers[self.index][k] if k=='name' or k.endswith('_mode') else f'{self.layers[self.index][k]:.12g}')
        self.busy=False
        self.update_band_controls()
        self.update_structure()

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
        self.update_structure()
        if self.drag:self.cancel_drag()
        self.drag_history.clear()
        self.update_band_controls()
        if self.pending:self.after_cancel(self.pending)
        self.result=None
        self.clear_hover()
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
            r['mode']=self.mode.get()
            r['temperature']=s['T'];r['left']=s['left'];r['right']=s['right']
            self.result=r
            self.view_combo.configure(values=['能带','电势','电场','载流子浓度'] if 'phi' in r else ['能带'])
            if 'phi' not in r:self.view.set('能带')
            self.plot()
            self.status.set(note+'  模型：一维、暗态、零外加偏压；不含离子、陷阱、界面偶极。')
            return True
        except Exception as e:
            self.result=None;self.clear_hover(); self.ax.set_title('当前输入无有效结果 · 图中保留上一次曲线',color='#bd452c'); self.canvas.draw_idle()
            self.status.set('未更新：'+str(e)); return False

    def plot(self):
        r=self.result; ax=self.ax; ax.clear(); pos=0
        ax.set_yscale('linear')
        self.band_artists={};self.offset_artists=[]
        self.hover_line=None;self.hover_text.set('将鼠标移到图中查看数值；界面位置取右侧材料极限。\n')
        colors=['#e4edf8','#f9e9d4','#e0f0e6','#efe5f5']
        for i,a in enumerate(self.layers):
            end=pos+a['thickness']; ax.axvspan(pos,end,color=colors[i%4],alpha=.7)
            ax.text((pos+end)/2,1.015,a['name'],ha='center',transform=ax.get_xaxis_transform(),fontsize=9,rotation=15)
            ax.axvline(end,color='#aab4bd',lw=.7); pos=end
        # Draw each layer separately to avoid interpolating interface discontinuities.
        self.segments=analysis_segments(self.layers,r,r['temperature'],r['left'],r['right'])
        self.offsets=interface_offsets(self.layers,self.segments)
        if self.view.get()!='能带':
            self.plot_diagnostic()
            self.finish_plot(pos)
            return
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
                for i,s in enumerate(self.snapshot['segments']):
                    ax.plot(s['x'],s[key],color=color,ls=':',alpha=.65,label=key+' 对照' if i==0 else None)
        ax.set(xlabel='位置 x (nm)',ylabel='能量 (eV)',xlim=(0,pos))
        energies=[np.array([0.])]+[s[k] for s in self.segments for k in ('Ec','Ev')]
        if self.snapshot and self.snapshot['mode']==r['mode']:
            energies.extend([self.snapshot['Ec'],self.snapshot['Ev']])
        low=min(float(np.min(v)) for v in energies);high=max(float(np.max(v)) for v in energies)
        margin=max(.15,.12*(high-low));ax.set_ylim(low-margin,high+margin)
        if self.show_offsets.get():
            for i,offset in enumerate(self.offsets):
                for key,color,dy in [('Ec','#2279b5',14),('Ev','#cd6930',-30)]:
                    y=self.segments[i+1][key][0]
                    near_right=offset['x']>.75*pos
                    artist=ax.annotate(f"Δ{key} {offset[key]:+.3f} eV",xy=(offset['x'],y),
                                       xytext=(-8 if near_right else 8,dy),textcoords='offset points',
                                       ha='right' if near_right else 'left',fontsize=8,color=color,
                                       bbox=dict(boxstyle='round,pad=.25',fc='white',ec=color,alpha=.9),
                                       arrowprops=dict(arrowstyle='-',color=color,lw=.7))
                    self.offset_artists.append(artist)
        self.finish_plot(pos)

    def finish_plot(self, width):
        ax=self.ax
        ax.set_xlabel('位置 x (nm)');ax.set_xlim(0,width)
        ax.grid(alpha=.18); ax.legend(loc='best')
        self.hover_line=ax.axvline(0,color='#536a80',ls=':',lw=.8,visible=False)
        self.canvas.draw_idle()

    def plot_diagnostic(self):
        ax=self.ax;view=self.view.get()
        choices={'电势': [('phi','φ','#5267ad')],
                 '电场': [('field','E','#9755b3')],
                 '载流子浓度': [('n','电子 n','#2279b5'),('p','空穴 p','#cd6930')]}
        for key,label,color in choices[view]:
            for i,s in enumerate(self.segments):
                ax.plot(s['x'],s[key],color=color,lw=2,label=label if i==0 else None)
        snapshot=self.snapshot
        if snapshot and snapshot['mode']==self.result['mode']:
            # Snapshots keep their own layer geometry, including interface limits.
            for key,label,color in choices[view]:
                for i,s in enumerate(snapshot['segments']):
                    ax.plot(s['x'],s[key],color=color,ls=':',alpha=.65,
                            label=label+' 对照' if i==0 else None)
        ax.set_ylabel({'电势':'电势 φ (V)','电场':'电场 E (V/cm)',
                       '载流子浓度':r'载流子浓度 (cm$^{-3}$)'}[view])
        if view=='载流子浓度':ax.set_yscale('log')
        else:ax.axhline(0,color='#87929b',lw=.7,ls='--')
        ax.set_title('热平衡 · E = −dφ/dx（分层求导）' if view=='电场' else '热平衡 · '+view,
                     fontsize=10,y=1.13,pad=6)

    def change_view(self, event=None):
        if self.drag:self.cancel_drag()
        if self.result is not None:
            if 'phi' not in self.result:self.view.set('能带')
            self.plot()

    def clear_hover(self, event=None):
        self.hover_text.set('将鼠标移到图中查看数值。\n')
        line=getattr(self,'hover_line',None)
        if line is not None and line.get_visible():
            line.set_visible(False);self.canvas.draw_idle()

    def hover_motion(self, event):
        if self.drag or self.result is None or self.toolbar.mode or event.inaxes!=self.ax or event.xdata is None:
            self.clear_hover();return
        x=float(np.clip(event.xdata,0,sum(a['thickness'] for a in self.layers)))
        v=sample_position(self.layers,self.segments,x,self.result['temperature'])
        text=f"{v['name']}  |  x {x:.2f} nm  ·  Ec {v['Ec']:.4f} eV  ·  Ev {v['Ev']:.4f} eV  ·  Eg {v['Eg']:.4f} eV"
        if 'phi' in v:
            text+=f"\nφ {v['phi']:.4f} V  ·  E {v['field']:+.3e} V/cm  ·  n {v['n']:.3e} cm⁻³  ·  p {v['p']:.3e} cm⁻³"
        else:text+='\n真空参考排列：不计算静电势、电场和载流子浓度。'
        self.hover_text.set(text)
        self.hover_line.set_xdata([x,x]);self.hover_line.set_visible(True);self.canvas.draw_idle()

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
        if event.button!=1 or self.view.get()!='能带' or not self.drag_enabled.get() or self.toolbar.mode or self.result is None:return
        target=self.drag_target(event)
        if target is None:return
        _,i,key,part=target
        self.clear_hover()
        for artist in self.offset_artists:artist.set_visible(False)
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
        if not d['changed']:self.plot();return
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
        if p:Path(p).write_text(json.dumps(dict(schema=3,layers=self.layers,settings=s,mode=self.mode.get(),
                                              structure_illumination='right' if self.structure_direction.get()=='右侧入射' else 'left'),ensure_ascii=False,indent=2),encoding='utf-8')

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
            direction=data.get('structure_illumination',s.get('optics',{}).get('illumination','left'))
            self.structure_direction.set('右侧入射' if direction=='right' else '左侧入射')
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
            if 'phi' in r:
                r['field']=np.concatenate([s['field'][1:-1] for s in self.segments])
                keys+=['field'];header+=['E_V_per_cm']
            keys+=['Eg','chi','Nd','Na'];header+=['Eg_eV','chi_eV','Nd_cm-3','Na_cm-3']
            np.savetxt(p,np.column_stack([r[k] for k in keys]),delimiter=',',header=','.join(header),comments='')

    def export_image(self):
        if not self.calculate():return
        p=filedialog.asksaveasfilename(defaultextension='.png',filetypes=[('PNG','*.png'),('SVG','*.svg')])
        if p:self.fig.savefig(p,dpi=220,bbox_inches='tight')

    def compare(self):
        if self.calculate():
            self.snapshot=deepcopy(self.result)
            self.snapshot['segments']=deepcopy(self.segments)
            self.plot()

    def clear_compare(self):self.snapshot=None;self.calculate()

    def help(self):
        messagebox.showinfo('适用范围',
            '能带页用于热平衡参数探索；器件性能页使用 SimSS 5.36 计算稳态 J–V。\n\n'
            '热平衡：一维泊松-玻尔兹曼方程，完全电离浅掺杂；电子/空穴共享 EF=0。'
            '接触采用功函数确定的 Dirichlet 电势边界。假设电子亲和能规则，无界面偶极或固定电荷。\n\n'
            '能带页不含光照、外加偏压、离子迁移、陷阱电荷、复合与电流输运，不直接预测 J–V 或效率。'
            '不适于把 SAM 直接当普通体层精确模拟。高掺杂/强积累时玻尔兹曼统计可能失效。\n\n'
            '参数全部保留，但迁移率、寿命不进入平衡静电方程。不能从单张能带图唯一反演它们。'
            '此版没有自动图像识别或反演。\n\n'
            '器件性能：参数独立，可从能带页导入；支持均匀与渐变层，Nc=Nv。'
            '渐变通过薄子层近似，虚拟界面保持原体迁移率；建议增加分段数比较稳定性。'
            '支持 AM1.5G/自定义光谱，经法向入射薄膜传输矩阵计算 G(x)，也可手动输入均匀 G。'
            '采用中性体 SRH 陷阱、双分子复合，并支持真实材料界面上的单能级陷阱复合；无离子迁移。'
            '寿命通过 C=1/(τ·Nt) 换算捕获系数，Nt=0 时关闭体 SRH。'
            '光谱模式按完整入射功率计算效率；各层 n,k 须按实际材料指定，内置光学常数仅供演示。'
            '可选择固定 n,k 或吸收边随局部 Eg 近似联动，近似模式的 n 保持原谱。'
            '手动 G 模式须自行对应 G 和入射功率。损失分析使用原生复合等效电流；EQE 为短路微分电流响应。'
            '输出电流正号表示发电；扫描未覆盖开路点时不显示 Voc、FF 和效率。\n\n'
            '鼠标拖线会移动该层的一条带边；拖端点圆点会改变渐变，保持另一条带边的真空参考位置。'
            '拖动后采用独立电子亲和能端点；掺杂、迁移率和寿命保持原值。'
            '平衡态拖动期间暂固定电势，松开后自洽求解，最终能带可能偏离预览；拖动不等于参数反演。'
            'Esc 取消；按钮可撤销。缩放/平移工具启用时暂停能带拖动。\n\n'
            '查看菜单可切换能带、电势、电场和载流子浓度；后三者仅限热平衡模式。'
            '鼠标悬停显示所在材料的 x、Ec、Ev、Eg 及热平衡读数；精确界面取右侧极限。'
            '界面 ΔEc、ΔEv 定义为右侧减左侧，正负仅表示带边台阶方向。'
            '电场为 E=-dφ/dx，按材料层分别求导，单位 V/cm；载流子浓度使用对数纵轴。\n\n'
            '单位：厚度 nm；能量 eV；态密度/掺杂 cm⁻³；迁移率 cm²/(V·s)；寿命 s。'
            '导出的参数表不是 SCAPS 原生文件。')

if __name__=='__main__':App().mainloop()
