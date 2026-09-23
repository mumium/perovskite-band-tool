"""Run: python app.py"""
import csv
import json
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
from physics import FIELDS, example, layer, solve, validate, normalize, alignment

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('钙钛矿能带小工具 · 材料参数与热平衡能带')
        self.geometry('1320x880'); self.minsize(1080,760)
        self.layers=[normalize(a) for a in example()]; self.index=0; self.busy=False; self.pending=None
        self.result=None; self.snapshot=None
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
        ttk.Label(grading,text='方向：本层左侧 → 本层右侧\n起点使用「基本参数」里的 Eg、Nd、Na。\n均匀模式忽略终点值。',justify='left').pack(anchor='w',pady=(0,12))
        for key,label,unit in [('Eg','带隙 Eg','eV'),('Nd','施主浓度 Nd','cm⁻³'),('Na','受主浓度 Na','cm⁻³')]:
            box=ttk.LabelFrame(grading,text=label,padding=8); box.pack(fill='x',pady=5)
            mode=tk.StringVar(value='均匀'); self.vars[key+'_mode']=mode
            ttk.Combobox(box,textvariable=mode,values=['均匀','线性'] if key=='Eg' else ['均匀','线性','对数'],state='readonly',width=9).grid(row=0,column=0,padx=3)
            ttk.Label(box,text='右侧终点 '+unit).grid(row=0,column=1,padx=3)
            end=tk.StringVar(); self.vars[key+'_end']=end
            ttk.Entry(box,textvariable=end,width=13).grid(row=1,column=1,pady=5)
            mode.trace_add('write',self.schedule); end.trace_add('write',self.schedule)
        box=ttk.LabelFrame(grading,text='带隙变化如何分配到带边',padding=8);box.pack(fill='x',pady=8)
        fraction=tk.StringVar(value='0'); self.vars['band_fraction']=fraction
        ttk.Label(box,text='导带分配比例 α（0–1）').pack(anchor='w')
        ttk.Entry(box,textvariable=fraction,width=12).pack(anchor='w',pady=5)
        fraction.trace_add('write',self.schedule)
        ttk.Label(box,text='0：固定电子亲和能，只改变价带\n1：固定真空参考价带，只改变导带\n0.5：带隙变化由两条带边均分',justify='left').pack(anchor='w')
        ttk.Label(grading,text='掺杂渐变通过静电求解改变平衡能带；\n真空参考排列不受掺杂影响。\n线性允许零浓度；对数要求两端为正数。',justify='left').pack(anchor='w',pady=10)
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
        self.fig=Figure(figsize=(8,6),dpi=100); self.ax=self.fig.add_subplot(111)
        self.canvas=FigureCanvasTkAgg(self.fig,master=right); self.canvas.get_tk_widget().pack(fill='both',expand=True)
        NavigationToolbar2Tk(self.canvas,right)
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
        for k,v in self.vars.items():v.set(self.layers[self.index][k] if k=='name' or k.endswith('_mode') else f'{self.layers[self.index][k]:.8g}')
        self.busy=False

    def commit(self):
        a={k:(v.get() if k=='name' or k.endswith('_mode') else float(v.get())) for k,v in self.vars.items()}
        temp=[dict(z) for z in self.layers]; temp[self.index]=a
        settings={k:float(v.get()) for k,v in self.settings.items()}
        validate(temp,settings['T'],settings['left'],settings['right'])
        self.layers=temp
        return settings

    def schedule(self,*_):
        if self.busy:return
        if self.pending:self.after_cancel(self.pending)
        self.result=None
        self.status.set('参数已变更，等待重新计算…')
        self.pending=self.after(600,self.calculate)

    def calculate(self):
        if self.pending:self.after_cancel(self.pending)
        self.pending=None
        try:
            s=self.commit(); self.refresh_list()
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
        colors=['#e4edf8','#f9e9d4','#e0f0e6','#efe5f5']
        for i,a in enumerate(self.layers):
            end=pos+a['thickness']; ax.axvspan(pos,end,color=colors[i%4],alpha=.7)
            ax.text((pos+end)/2,1.015,a['name'],ha='center',transform=ax.get_xaxis_transform(),fontsize=9,rotation=15)
            ax.axvline(end,color='#aab4bd',lw=.7); pos=end
        # Draw each layer separately to avoid interpolating interface discontinuities.
        offset=0
        for i,count in enumerate(r['counts']):
            sl=slice(offset,offset+count)
            for key,color in [('Ec','#2279b5'),('Ev','#cd6930')]:
                ax.plot(r['x'][sl],r[key][sl],color=color,lw=2,label=key if i==0 else None)
            offset+=count
        if r['mode']=='热平衡弯曲能带':ax.axhline(0,color='#537357',ls='--',label='EF = 0 eV')
        else:ax.axhline(0,color='#537357',ls='--',label='真空能级 = 0 eV')
        if self.snapshot and self.snapshot['mode']==r['mode']:
            for key,color in [('Ec','#2279b5'),('Ev','#cd6930')]:
                ax.plot(self.snapshot['x'],self.snapshot[key],color=color,ls=':',alpha=.65,label=key+' 对照')
        ax.set(xlabel='位置 x (nm)',ylabel='能量 (eV)',xlim=(0,pos))
        ax.grid(alpha=.18); ax.legend(loc='best'); ax.margins(y=.2)
        self.fig.tight_layout(pad=2.4); self.canvas.draw_idle()

    def select(self,event=None):
        chosen=self.listbox.curselection()
        if not chosen or chosen[0]==self.index:return
        try:self.commit()
        except Exception as e:
            messagebox.showerror('请先修正参数',str(e));self.refresh_list();return
        self.index=chosen[0]; self.show_fields(); self.calculate()

    def add(self):
        if not self.calculate():return
        self.layers.append(layer('新材料',50,10,1.6,4));self.index=len(self.layers)-1
        self.refresh_list();self.show_fields();self.calculate()

    def remove(self):
        if len(self.layers)==1:return
        self.layers.pop(self.index); self.index=min(self.index,len(self.layers)-1)
        self.refresh_list();self.show_fields();self.calculate()

    def move(self,direction):
        target=self.index+direction
        if not 0<=target<len(self.layers) or not self.calculate():return
        self.layers[self.index],self.layers[target]=self.layers[target],self.layers[self.index]
        self.index=target;self.refresh_list();self.show_fields();self.calculate()

    def save(self):
        try:s=self.commit()
        except Exception as e:messagebox.showerror('参数错误',str(e));return
        p=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('参数项目','*.json')])
        if p:Path(p).write_text(json.dumps(dict(schema=2,layers=self.layers,settings=s,mode=self.mode.get()),ensure_ascii=False,indent=2),encoding='utf-8')

    def load(self):
        p=filedialog.askopenfilename(filetypes=[('参数项目','*.json')])
        if not p:return
        try:
            data=json.loads(Path(p).read_text(encoding='utf-8-sig'));s=data['settings']
            if data.get('schema') not in (1,2):raise ValueError('不支持的文件版本')
            data['layers']=[normalize(a) for a in data['layers']]
            validate(data['layers'],s['T'],s['left'],s['right'])
            self.busy=True;self.layers=data['layers'];self.index=0
            for k,v in self.settings.items():v.set(s[k])
            self.mode.set(data.get('mode','热平衡弯曲能带'));self.snapshot=None
            self.busy=False;self.refresh_list();self.show_fields();self.calculate()
        except Exception as e:self.busy=False;messagebox.showerror('打开失败',str(e))

    def export_params(self):
        try:self.commit()
        except Exception as e:messagebox.showerror('参数错误',str(e));return
        p=filedialog.asksaveasfilename(defaultextension='.csv')
        if p:
            with open(p,'w',newline='',encoding='utf-8-sig') as f:
                extra=['Eg_mode','Eg_end','Nd_mode','Nd_end','Na_mode','Na_end','band_fraction']
                w=csv.writer(f);w.writerow(['材料']+[f'{label} ({unit})' for _,label,unit in FIELDS]+['Eg渐变','Eg右端_eV','Nd渐变','Nd右端_cm-3','Na渐变','Na右端_cm-3','导带分配比例'])
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
            '单位：厚度 nm；能量 eV；态密度/掺杂 cm⁻³；迁移率 cm²/(V·s)；寿命 s。'
            '导出的参数表不是 SCAPS 原生文件。')

if __name__=='__main__':App().mainloop()
