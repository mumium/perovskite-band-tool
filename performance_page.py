"""Independent Tk page for editable SimSS device inputs and PV results."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from physics import FIELDS, layer
from ui_helpers import ScrollableFrame
from performance import (DEFAULT_SETTINGS, EXTRA_FIELDS, ENGINE_VERSION,
                         SimulationCancelled, import_layers, run_simulation,
                         validate_device)


class PerformancePage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.layers = import_layers(app.layers)
        self.index = 0
        self.loading = True
        self.result = None
        self.previous_result = None
        self.dirty = False
        self.running = False
        self.closed = False
        self.poll_id = None
        self.cancel_event = threading.Event()
        self.events = queue.Queue()
        self.input_widgets = []
        hero = ttk.Frame(self)
        hero.pack(fill='x', pady=(0, 8))
        ttk.Label(hero, text='器件性能模拟', font=('Microsoft YaHei', 14, 'bold'),
                  foreground='#245565').pack(side='left')
        self.run_button = tk.Button(hero, text='▶  运行 J–V 模拟', command=self.run,
                                    font=('Microsoft YaHei', 13, 'bold'), bg='#087e8b', fg='white',
                                    activebackground='#056270', activeforeground='white',
                                    relief='flat', cursor='hand2', padx=22, pady=10)
        self.run_button.pack(side='right', padx=4)
        self.cancel_button = ttk.Button(hero, text='取消计算', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='right', padx=8)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=(0, 8))
        for title, command in [('从能带页导入参数', self.import_from_band),
                               ('打开性能参数', self.load), ('保存性能参数', self.save)]:
            button = ttk.Button(actions, text=title, command=command)
            button.pack(side='left', padx=(0, 6))
            self.input_widgets.append(button)
        self.export_buttons = []
        for title, command in [('导出 J–V CSV', self.export_csv),
                               ('保存结果 JSON', self.export_result), ('保存图片', self.export_image)]:
            button = ttk.Button(actions, text=title, command=command, state='disabled')
            button.pack(side='left', padx=4)
            self.export_buttons.append(button)
        ttk.Label(self, text='支持均匀与渐变器件；修改参数保留上次结果，计算成功后更新并叠加前次曲线。',
                  foreground='#526779').pack(anchor='w', pady=(0, 6))

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body, width=370)
        right = ttk.Frame(body)
        body.add(left, weight=0)
        body.add(right, weight=1)
        ttk.Label(left, text='材料层（从左电极到右电极）', font=('Microsoft YaHei', 10, 'bold')).pack(anchor='w')
        self.listbox = tk.Listbox(left, height=3, exportselection=False)
        self.listbox.pack(fill='x', pady=5)
        self.listbox.bind('<<ListboxSelect>>', self.select)
        self.input_widgets.append(self.listbox)
        row = ttk.Frame(left)
        row.pack(fill='x')
        for title, command in [('添加', self.add), ('删除', self.remove),
                               ('上移', lambda: self.move(-1)), ('下移', lambda: self.move(1))]:
            button = ttk.Button(row, text=title, width=7, command=command)
            button.pack(side='left', padx=2)
            self.input_widgets.append(button)
        self.grading_note = tk.StringVar()
        ttk.Label(left, textvariable=self.grading_note, foreground='#a15d14', wraplength=380).pack(anchor='w', pady=4)

        layer_tabs = ttk.Notebook(left)
        layer_tabs.pack(fill='both', expand=True)
        self.parameter_scroll = ScrollableFrame(layer_tabs, width=345)
        layer_tabs.add(self.parameter_scroll, text='材料参数')
        form = self.parameter_scroll.content
        self.vars = {}
        for i, (key, label, unit) in enumerate([('name', '材料名称', '')]+FIELDS+EXTRA_FIELDS):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky='w', pady=3)
            var = tk.StringVar()
            self.vars[key] = var
            entry = ttk.Entry(form, textvariable=var, width=14)
            entry.grid(row=i, column=1, padx=5, sticky='ew')
            ttk.Label(form, text=unit).grid(row=i, column=2, sticky='w')
            var.trace_add('write', self.mark_dirty)
            self.input_widgets.append(entry)
        self.absorber = tk.BooleanVar()
        check = ttk.Checkbutton(form, text='本层产生光生载流子（使用右侧 G）', variable=self.absorber)
        check.grid(row=len(self.vars), column=0, columnspan=3, sticky='w', pady=8)
        self.absorber.trace_add('write', self.mark_dirty)
        self.input_widgets.append(check)
        ttk.Label(form, text='Nc 与 Nv 须相等；Nt=0 关闭体 SRH。\n捕获系数 C=1/(τ·Nt)；固定寿命时 Nt 不改变复合。\n最后一层的右界面传输速度不参与计算。',
                  foreground='#526779').grid(row=len(self.vars)+1, column=0, columnspan=3, sticky='w')

        grading_scroll = ScrollableFrame(layer_tabs, width=345)
        layer_tabs.add(grading_scroll, text='渐变设置')
        grading = grading_scroll.content
        ttk.Label(grading, text='方向：本层左侧 → 右侧；起点使用材料参数。', wraplength=320).pack(anchor='w', pady=5)
        for key, label in [('Eg', '带隙 Eg / eV'), ('Nd', '施主 Nd / cm⁻³'), ('Na', '受主 Na / cm⁻³')]:
            box = ttk.LabelFrame(grading, text=label, padding=6)
            box.pack(fill='x', pady=4)
            mode = tk.StringVar()
            end = tk.StringVar()
            self.vars[key+'_mode'], self.vars[key+'_end'] = mode, end
            combo = ttk.Combobox(box, textvariable=mode, values=['均匀', '线性'] if key == 'Eg' else ['均匀', '线性', '对数'],
                                 state='readonly', width=8)
            combo.pack(side='left')
            ttk.Label(box, text='右端').pack(side='left', padx=5)
            entry = ttk.Entry(box, textvariable=end, width=13)
            entry.pack(side='left')
            self.input_widgets.extend([combo, entry])
            mode.trace_add('write', self.mark_dirty)
            end.trace_add('write', self.mark_dirty)
        box = ttk.LabelFrame(grading, text='电子亲和能 χ', padding=6)
        box.pack(fill='x', pady=4)
        for key, label, default in [('affinity_mode', '模式', '随带隙分配'),
                                    ('chi_end', '独立右端 / eV', '0'), ('band_fraction', '随 Eg 分配比例 α', '0')]:
            var = tk.StringVar(value=default)
            self.vars[key] = var
            row = ttk.Frame(box)
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=label).pack(side='left')
            if key == 'affinity_mode':
                widget = ttk.Combobox(row, textvariable=var, values=['随带隙分配', '独立端点'], state='readonly', width=12)
            else:
                widget = ttk.Entry(row, textvariable=var, width=13)
            widget.pack(side='right')
            self.input_widgets.append(widget)
            var.trace_add('write', self.mark_dirty)
        ttk.Label(grading, text='独立端点：χ 线性变化，忽略 α。\n随带隙分配：χ=χ左−α(Eg−Eg左)。\n对数掺杂两端须 > 0；渐变分段数在计算设置中。',
                  foreground='#526779', wraplength=325).pack(anchor='w', pady=6)

        self.settings_header = ttk.Frame(right)
        self.settings_header.pack(fill='x')
        self.settings_toggle = ttk.Button(self.settings_header, text='收起模拟设置', command=self.toggle_settings)
        self.settings_toggle.pack(side='left', pady=(0, 3))
        ttk.Label(self.settings_header, text='小屏幕可收起设置以扩大曲线', foreground='#526779').pack(side='left', padx=8)
        self.settings_visible = True
        self.compact = None
        settings_tabs = ttk.Notebook(right, height=150)
        self.settings_tabs = settings_tabs
        settings_tabs.pack(fill='x')
        self.settings = {}
        groups = [
            ('光照与扫描', [('T', '温度', 'K'), ('G', '吸收层均匀光生率 G', 'cm⁻³·s⁻¹'),
                          ('pin', '入射功率密度', 'mW/cm²'), ('vmin', '扫描起点', 'V'),
                          ('vmax', '扫描终点', 'V'), ('vstep', '电压步长', 'V')]),
            ('电极与电阻', [('left', '左电极功函数', 'eV'), ('right', '右电极功函数', 'eV'),
                          ('sn_left', '左接触电子速度', 'cm/s'), ('sp_left', '左接触空穴速度', 'cm/s'),
                          ('sn_right', '右接触电子速度', 'cm/s'), ('sp_right', '右接触空穴速度', 'cm/s'),
                          ('rs', '串联电阻 Rs', 'Ω·cm²'), ('rsh', '并联电阻 Rsh', 'Ω·cm²')]),
            ('计算设置', [('points', '空间网格点数', '20–2000'), ('timeout', '计算超时', 's'),
                          ('grading_slices', '每个渐变层分段数', '2–100')])]
        self.setting_forms = []
        for title, fields in groups:
            scroll_tab = ScrollableFrame(settings_tabs, width=560, height=150)
            settings_tabs.add(scroll_tab, text=title)
            tab = scroll_tab.content
            rows = []
            for i, (key, label, unit) in enumerate(fields):
                item = ttk.Frame(tab)
                ttk.Label(item, text=label, width=19).pack(side='left', padx=3)
                var = tk.StringVar(value=f'{DEFAULT_SETTINGS[key]:g}')
                self.settings[key] = var
                entry = ttk.Entry(item, textvariable=var, width=11)
                entry.pack(side='left', padx=3)
                ttk.Label(item, text=unit).pack(side='left', padx=3)
                rows.append(item)
                var.trace_add('write', self.mark_dirty)
                self.input_widgets.append(entry)
            if title == '电极与电阻':
                self.left_anode = tk.BooleanVar(value=True)
                check = ttk.Checkbutton(tab, text='左侧为阳极（空穴接触）；取消勾选表示右侧为阳极', variable=self.left_anode)
                check.grid(row=20, column=0, columnspan=2, sticky='w', pady=4)
                self.left_anode.trace_add('write', self.mark_dirty)
                self.input_widgets.append(check)
                ttk.Label(tab, text='速度 0 表示阻挡；左阳极时通常左电子 0、左空穴 1e7，右侧相反。', wraplength=430,
                          foreground='#526779').grid(row=21, column=0, columnspan=2, sticky='w')
            if title == '光照与扫描':
                ttk.Label(tab, text='G 与入射功率需自行对应；默认值仅供演示。', wraplength=430,
                          foreground='#a15d14').grid(row=20, column=0, columnspan=2, sticky='w', pady=4)
            self.setting_forms.append((scroll_tab, rows))
            scroll_tab.canvas.bind('<Configure>', lambda e, items=rows: self.layout_settings(items, e.width), add='+')
        summary = ttk.Frame(right, padding=(4, 6))
        self.summary = summary
        summary.pack(fill='x')
        self.metric_vars = {}
        for i, (key, title, unit) in enumerate([('Jsc', 'Jsc', 'mA/cm²'), ('Voc', 'Voc', 'V'),
                                                ('FF', 'FF', '%'), ('PCE', 'PCE', '%')]):
            card = ttk.LabelFrame(summary, text=f'{title} · {unit}', padding=5)
            card.grid(row=0, column=i, sticky='ew', padx=4)
            summary.columnconfigure(i, weight=1)
            var = tk.StringVar(value='—')
            self.metric_vars[key] = var
            ttk.Label(card, textvariable=var, font=('Microsoft YaHei', 14, 'bold'), foreground='#245565').pack()
        self.mpp_text = tk.StringVar(value='尚未计算。输入支持科学计数法，例如 3e21。')
        self.mpp_label = ttk.Label(right, textvariable=self.mpp_text, wraplength=800)
        self.mpp_label.pack(anchor='w', padx=8)
        self.comparison_text = tk.StringVar()
        self.comparison_label = ttk.Label(right, textvariable=self.comparison_text, foreground='#805c16', wraplength=800)
        plot_controls = ttk.Frame(right)
        self.plot_controls = plot_controls
        plot_controls.pack(fill='x', padx=8, pady=4)
        self.full_scan = tk.BooleanVar(value=False)
        ttk.Checkbutton(plot_controls, text='显示完整扫描范围', variable=self.full_scan, command=self.plot).pack(side='left')
        self.show_previous = tk.BooleanVar(value=True)
        ttk.Checkbutton(plot_controls, text='叠加上次结果', variable=self.show_previous, command=self.plot).pack(side='left', padx=8)
        ttk.Button(plot_controls, text='清除对照', command=self.clear_comparison).pack(side='left')
        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().configure(width=250, height=140)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, right, pack_toolbar=False)
        self.canvas.get_tk_widget().pack_forget()
        self.toolbar.pack(side='bottom', fill='x')
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        footer = ttk.Frame(self)
        self.footer = footer
        footer.pack(side='bottom', fill='x', before=body)
        self.status = tk.StringVar(value=f'就绪 · SimSS {ENGINE_VERSION} · 参数已从能带页复制，可直接编辑。')
        self.status_label = ttk.Label(footer, textvariable=self.status, foreground='#245565', wraplength=1250)
        self.status_label.pack(fill='x', pady=(4, 0))
        self.warning = tk.StringVar()
        self.warning_label = ttk.Label(footer, textvariable=self.warning, foreground='#a15d14', wraplength=1250)
        self.warning_label.pack(fill='x')
        self.bind('<Configure>', self.resize_labels, add='+')
        right.bind('<Configure>', self.resize_right)
        self.right = right
        self.show_fields()
        self.refresh_list()
        self.loading = False
        self.plot()

    def mark_dirty(self, *_):
        if self.loading:
            return
        self.dirty = True
        self.status.set('参数已修改；当前曲线、指标及导出仍对应上次成功计算。点击右上方运行以更新。'
                        if self.result is not None else '参数已修改，请点击右上方「运行 J–V 模拟」。')

    def layout_settings(self, rows, width):
        columns = 2 if width >= 760 else 1
        for i, row in enumerate(rows):
            row.grid(row=i//columns, column=i % columns, sticky='w', pady=2)

    def resize_labels(self, event):
        if event.widget is self:
            for widget in (self.status_label, self.warning_label):
                widget.configure(wraplength=max(300, event.width-24))

    def resize_right(self, event):
        for widget in (self.mpp_label, self.comparison_label):
            widget.configure(wraplength=max(200, event.width-20))
        compact = event.height < 500
        if compact != self.compact:
            self.compact = compact
            self.toggle_settings(not compact)

    def toggle_settings(self, visible=None):
        visible = not self.settings_visible if visible is None else visible
        self.settings_visible = visible
        if visible:
            self.settings_tabs.pack(fill='x', before=self.summary)
        else:
            self.settings_tabs.pack_forget()
        self.settings_toggle.configure(text='收起模拟设置' if visible else '展开模拟设置')

    def clear_comparison(self):
        self.previous_result = None
        self.comparison_text.set('')
        self.comparison_label.pack_forget()
        self.plot()

    def refresh_list(self):
        self.listbox.delete(0, 'end')
        for a in self.layers:
            self.listbox.insert('end', f"{'☀ ' if a.get('absorber') else ''}{a['name']}  |  {a['thickness']:g} nm")
        self.listbox.selection_set(self.index)

    def show_fields(self):
        previous = self.loading
        self.loading = True
        a = self.layers[self.index]
        for key, var in self.vars.items():
            var.set(a[key] if key == 'name' or key.endswith('_mode') else f'{a[key]:.12g}')
        self.absorber.set(bool(a.get('absorber')))
        graded = [key for key in ('Eg', 'Nd', 'Na') if a.get(key+'_mode', '均匀') != '均匀']
        if a.get('affinity_mode') == '独立端点' and a['chi'] != a['chi_end']:
            graded.append('χ')
        self.grading_note.set('渐变：'+', '.join(graded)+'；运行时自动分段。' if graded else '均匀层；吸收层以 ☀ 标记。')
        self.loading = previous

    def commit_layer(self):
        a = deepcopy(self.layers[self.index])
        for key, var in self.vars.items():
            try:
                a[key] = var.get().strip() if key == 'name' or key.endswith('_mode') else float(var.get())
            except ValueError:
                raise ValueError(f'{key} 请输入数值，支持科学计数法') from None
        a['absorber'] = self.absorber.get()
        # Keep previously uniform endpoint metadata uniform when editing starts.
        for key in ('Eg', 'Nd', 'Na'):
            if a.get(key+'_mode', '均匀') == '均匀':
                a[key+'_end'] = a[key]
        if not a['name'] or any(not np.isfinite(a[k]) for k in self.vars if k != 'name' and not k.endswith('_mode')):
            raise ValueError('材料名称不能为空，参数必须为有限数值')
        self.layers[self.index] = a

    def collect(self):
        self.commit_layer()
        try:
            s = {key: float(var.get()) for key, var in self.settings.items()}
        except ValueError:
            raise ValueError('模拟设置请输入有效数值，支持科学计数法') from None
        s['left_anode'] = int(self.left_anode.get())
        validate_device(self.layers, s)
        self.refresh_list()
        return deepcopy(self.layers), s

    def select(self, event=None):
        chosen = self.listbox.curselection()
        if self.running or not chosen or chosen[0] == self.index:
            return
        try:
            self.commit_layer()
        except ValueError as e:
            messagebox.showerror('参数错误', str(e), parent=self)
            self.refresh_list()
            return
        self.index = chosen[0]
        self.show_fields()
        self.refresh_list()

    def add(self):
        try:
            self.commit_layer()
        except ValueError as e:
            messagebox.showerror('参数错误', str(e), parent=self)
            return
        a = import_layers([layer('新材料', 50, 10, 1.6, 4.)])[0]
        a['absorber'] = False
        self.layers.append(a)
        self.index = len(self.layers)-1
        self.show_fields()
        self.refresh_list()
        self.mark_dirty()

    def remove(self):
        if len(self.layers) <= 1:
            return
        self.layers.pop(self.index)
        self.index = min(self.index, len(self.layers)-1)
        self.show_fields()
        self.refresh_list()
        self.mark_dirty()

    def move(self, direction):
        target = self.index+direction
        if not 0 <= target < len(self.layers):
            return
        try:
            self.commit_layer()
        except ValueError as e:
            messagebox.showerror('参数错误', str(e), parent=self)
            return
        self.layers[self.index], self.layers[target] = self.layers[target], self.layers[self.index]
        self.index = target
        self.show_fields()
        self.refresh_list()
        self.mark_dirty()

    def import_from_band(self):
        try:
            self.app.cancel_drag()
            s = self.app.commit()
            layers = import_layers(self.app.layers)
        except Exception as e:
            messagebox.showerror('导入失败', '请先修正能带页参数：'+str(e), parent=self)
            return
        self.layers = layers
        self.index = 0
        self.loading = True
        for key in ('T', 'left', 'right'):
            self.settings[key].set(f'{s[key]:g}')
        anode = s['left'] >= s['right']
        self.left_anode.set(anode)
        for key, value in dict(sn_left=0 if anode else 1e7, sp_left=1e7 if anode else 0,
                               sn_right=1e7 if anode else 0, sp_right=0 if anode else 1e7).items():
            self.settings[key].set(f'{value:g}')
        self.show_fields()
        self.refresh_list()
        self.loading = False
        self.mark_dirty()
        self.status.set('已复制能带页当前参数；已按功函数初选电极方向，请核对接触设置和吸收层。')

    def set_running(self, running):
        self.running = running
        for widget in self.input_widgets:
            widget.configure(state='disabled' if running else ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
        self.run_button.configure(state='disabled' if running else 'normal')
        self.cancel_button.configure(state='normal' if running else 'disabled')
        for button in self.export_buttons:
            button.configure(state='normal' if self.result is not None and not running else 'disabled')

    def run(self):
        if self.running:
            return
        try:
            layers, settings = self.collect()
        except Exception as e:
            messagebox.showerror('无法开始模拟', str(e), parent=self)
            return
        self.set_running(True)
        if self.compact:
            self.toggle_settings(False)
        self.cancel_event = threading.Event()
        self.status.set('SimSS 正在后台计算… 上次结果保留至本次成功；可切换能带页或取消。')
        self.progress = ttk.Progressbar(self.footer, mode='indeterminate')
        self.progress.pack(fill='x', pady=3)
        self.progress.start(15)
        cancel = self.cancel_event

        def worker():
            try:
                result = run_simulation(layers, settings, cancel)
                self.events.put(('result', result))
            except SimulationCancelled as e:
                self.events.put(('cancel', str(e)))
            except Exception as e:
                self.events.put(('error', str(e)))

        threading.Thread(target=worker, daemon=True).start()
        self.poll_id = self.after(80, self.poll)

    def cancel(self):
        self.cancel_event.set()
        self.status.set('正在取消计算…')
        self.cancel_button.configure(state='disabled')

    def poll(self):
        self.poll_id = None
        if self.closed:
            return
        try:
            kind, payload = self.events.get_nowait()
        except queue.Empty:
            self.poll_id = self.after(80, self.poll)
            return
        self.progress.stop()
        self.progress.destroy()
        self.set_running(False)
        if kind == 'result' and not self.cancel_event.is_set():
            self.previous_result = self.result
            self.result = payload
            self.dirty = False
            for key, var in self.metric_vars.items():
                value = payload['metrics'][key]
                var.set('—' if value is None else f'{value:.3f}')
            m = payload['metrics']
            self.mpp_text.set('最大功率点未确认，请查看下方提示。' if m['Vmp'] is None else
                              f"最大功率点：Vmp {m['Vmp']:.4f} V  ·  Jmp {m['Jmp']:.3f} mA/cm²  ·  Pmax {m['Pmax']:.3f} mW/cm²")
            self.status.set(f"完成 · {payload['elapsed']:.2f} s · {len(payload['voltage'])} 个电压点 · "
                            f"光生电流参考 q∫Gdx = {payload['generated_current']:.3f} mA/cm² · "
                            f"电流不均匀度 {payload['relative_current_error']:.2%}")
            self.warning.set('；'.join(payload['warnings']))
            if self.previous_result is not None:
                old = self.previous_result['metrics']
                parts = []
                for key, unit in [('Jsc', 'mA/cm²'), ('Voc', 'V'), ('FF', '百分点'), ('PCE', '百分点')]:
                    if old[key] is not None and m[key] is not None:
                        parts.append(f"Δ{key} {m[key]-old[key]:+.3f} {unit}")
                self.comparison_text.set('相对上次：'+'  ·  '.join(parts))
                self.comparison_label.pack(anchor='w', padx=8, before=self.plot_controls)
            else:
                self.comparison_text.set('')
                self.comparison_label.pack_forget()
            for button in self.export_buttons:
                button.configure(state='normal')
            self.plot()
        elif kind == 'cancel' or self.cancel_event.is_set():
            self.status.set('模拟已取消，上次成功结果已保留。')
        else:
            self.status.set('模拟失败，上次成功结果已保留；请修正参数后重试。')
            self.warning.set('未采用未收敛的部分曲线。点击运行可重新计算。')
            messagebox.showerror('SimSS 计算失败', payload, parent=self)

    def plot(self):
        ax = self.ax
        ax.clear()
        ax.set(xlabel='端电压 V (V)', ylabel='输出电流密度 J (mA/cm²)')
        ax.axhline(0, color='#87929b', lw=.8)
        ax.axvline(0, color='#87929b', lw=.8)
        ax.grid(alpha=.2)
        if self.result is None:
            ax.set_title('输入器件参数后，点击「运行 J–V 模拟」', fontsize=11)
        else:
            r = self.result
            if self.show_previous.get() and self.previous_result is not None:
                prev = self.previous_result
                ax.plot(prev['voltage'], prev['current'], color='#a15d14', ls='--', lw=1.6, label='上次结果')
            ax.plot(r['voltage'], r['current'], color='#2279b5', lw=2, label='光照 J–V' if r['settings']['G'] else '暗态 J–V')
            m = r['metrics']
            if m['Vmp'] is not None:
                ax.plot(m['Vmp'], m['Jmp'], 'o', color='#cd6930', label='最大功率点')
            if not self.full_scan.get() and m['Jsc'] is not None and m['Jsc'] > 1e-8:
                shown = [r]+([self.previous_result] if self.show_previous.get() and self.previous_result else [])
                top = max(float(np.max(item['current'])) for item in shown)*1.15
                ax.set_ylim(-.15*top, top)
                if m['Voc'] is not None:
                    high = max(item['metrics']['Voc'] or m['Voc'] for item in shown)
                    ax.set_xlim(min(0., float(r['voltage'][0])), high*1.12)
            ax.set_title(f'稳态器件性能 · SimSS {ENGINE_VERSION}', fontsize=11)
            ax.legend(loc='best')
        self.fig.tight_layout(pad=2)
        self.canvas.draw_idle()

    def save(self):
        try:
            layers, settings = self.collect()
        except Exception as e:
            messagebox.showerror('参数错误', str(e), parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', filetypes=[('性能参数', '*.json')])
        if path:
            try:
                Path(path).write_text(json.dumps(dict(schema=1, model='simss', engine=ENGINE_VERSION,
                                                      layers=layers, settings=settings), ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError as e:
                messagebox.showerror('保存失败', str(e), parent=self)

    def load(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('性能参数', '*.json')])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if data.get('schema') != 1 or data.get('model') != 'simss' or data.get('engine') != ENGINE_VERSION:
                raise ValueError('请选择本工具保存的 SimSS 5.36 性能参数文件；能带项目请在能带页打开后导入。')
            data['settings'].setdefault('grading_slices', DEFAULT_SETTINGS['grading_slices'])
            from physics import normalize
            data['layers'] = [normalize(a) for a in data['layers']]
            validate_device(data['layers'], data['settings'])
            self.loading = True
            self.layers = data['layers']
            self.index = 0
            for key, var in self.settings.items():
                var.set(f"{data['settings'][key]:g}")
            self.left_anode.set(bool(data['settings']['left_anode']))
            self.show_fields()
            self.refresh_list()
            self.loading = False
            self.mark_dirty()
            self.status.set('性能参数已打开，请点击运行。')
        except Exception as e:
            self.loading = False
            messagebox.showerror('打开失败', str(e), parent=self)

    def export_csv(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', filetypes=[('J–V 数据', '*.csv')])
        if path:
            try:
                np.savetxt(path, np.column_stack([self.result['voltage'], self.result['current']]),
                           delimiter=',', header='V_terminal_V,J_output_mA_cm2', comments='')
            except OSError as e:
                messagebox.showerror('导出失败', str(e), parent=self)

    def export_result(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', filetypes=[('完整模拟结果', '*.json')])
        if path:
            data = {key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in self.result.items()}
            data.update(created_utc=datetime.now(timezone.utc).isoformat(), current_convention='positive delivered, mA/cm2')
            try:
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            except (OSError, ValueError) as e:
                messagebox.showerror('导出失败', str(e), parent=self)

    def export_image(self):
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.png', filetypes=[('PNG', '*.png'), ('SVG', '*.svg')])
        if path:
            try:
                self.fig.savefig(path, dpi=200, bbox_inches='tight')
            except OSError as e:
                messagebox.showerror('保存失败', str(e), parent=self)

    def shutdown(self):
        self.closed = True
        self.cancel_event.set()
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
            self.poll_id = None
