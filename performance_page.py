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
from ui_helpers import ScrollableFrame, FlowToolbar
from optics import (NK_LIBRARY, OPTICS_DEFAULTS, get_optics, default_nk,
                    nk_table, read_csv, spectrum_table)
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
        actions = FlowToolbar(self, padding=(0, 2))
        actions.pack(fill='x', pady=(0, 7))
        for title, command in [('导入能带参数', self.import_from_band),
                               ('打开参数', self.load), ('保存参数', self.save)]:
            button = actions.button(title, command)
            self.input_widgets.append(button)
        self.run_button = actions.button('▶ 运行 J–V 模拟', self.run, style='Accent.TButton')
        self.cancel_button = actions.button('取消', self.cancel, state='disabled')
        self.export_buttons = [actions.menu('导出结果', [('当前曲线 CSV', self.export_csv),
                                  ('完整结果 JSON', self.export_result), ('当前图像 PNG / SVG', self.export_image)], state='disabled')]

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body, width=370)
        right = ttk.Frame(body)
        body.add(left, weight=0)
        body.add(right, weight=1)
        def initial_split(event):
            body.unbind('<Map>')
            self.after_idle(lambda: body.sashpos(0, 365))
        body.bind('<Map>', initial_split)
        ttk.Label(left, text='材料层（从左电极到右电极）', font=('Microsoft YaHei', 10, 'bold')).pack(anchor='w')
        self.listbox = tk.Listbox(left, height=3, exportselection=False, bg='#f6f9fc', fg='#223b4b',
                                  selectbackground='#087e8b', selectforeground='white', relief='flat',
                                  highlightthickness=1, highlightbackground='#dce5ec')
        self.listbox.pack(fill='x', pady=5)
        self.listbox.bind('<<ListboxSelect>>', self.select)
        self.input_widgets.append(self.listbox)
        row = ttk.Frame(left)
        row.pack(fill='x')
        for title, command in [('添加', self.add), ('删除', self.remove),
                               ('上移', lambda: self.move(-1)), ('下移', lambda: self.move(1))]:
            button = ttk.Button(row, text=title, width=5, command=command)
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
        check = ttk.Checkbutton(form, text='本层产生光生载流子', variable=self.absorber)
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

        optical_layer = ScrollableFrame(layer_tabs, width=345)
        layer_tabs.add(optical_layer, text='光学参数')
        of = optical_layer.content
        ttk.Label(of, text='本层折射率 n / 消光系数 k', font=('Microsoft YaHei', 10, 'bold')).pack(anchor='w', pady=(5, 8))
        self.nk_choice = tk.StringVar()
        self.nk_combo = ttk.Combobox(of, textvariable=self.nk_choice, values=list(NK_LIBRARY), state='readonly', width=22)
        self.nk_combo.pack(fill='x', pady=3)
        self.nk_combo.bind('<<ComboboxSelected>>', self.choose_layer_nk)
        self.input_widgets.append(self.nk_combo)
        button = ttk.Button(of, text='导入本层 n,k 数据…', command=self.import_layer_nk)
        button.pack(fill='x', pady=6)
        self.input_widgets.append(button)
        self.nk_note = tk.StringVar()
        ttk.Label(of, textvariable=self.nk_note, wraplength=320, foreground='#536c7c').pack(anchor='w', pady=5)
        check = ttk.Checkbutton(of, text='本层吸收光产生载流子', variable=self.absorber)
        check.pack(anchor='w', pady=5)
        self.input_widgets.append(check)
        ttk.Label(of, text='CSV：波长_nm,n,k；波长递增，n>0，k≥0。\n内置数据为示例，请按实际材料替换。\n电学渐变不会自动改变本层光学常数。', wraplength=320,
                  foreground='#805c16').pack(anchor='w', pady=8)

        self.settings_header = ttk.Frame(right)
        self.settings_header.pack(fill='x')
        self.settings_toggle = ttk.Button(self.settings_header, text='模拟设置 ▴', command=self.toggle_settings)
        self.settings_toggle.pack(side='left', pady=(0, 3))
        self.mode_badge = tk.StringVar(value='AM1.5G · TMM 光学耦合')
        ttk.Label(self.settings_header, textvariable=self.mode_badge, foreground='#536c7c').pack(side='left', padx=8)
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
                if key == 'G':
                    self.G_entry = entry
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
                ttk.Label(tab, text='光谱模式自动计算 G(x)；入射功率统一缩放完整光谱。', wraplength=430,
                          foreground='#a15d14').grid(row=20, column=0, columnspan=2, sticky='w', pady=4)
            self.setting_forms.append((scroll_tab, rows))
            scroll_tab.canvas.bind('<Configure>', lambda e, items=rows: self.layout_settings(items, e.width), add='+')
        optical_tab = ScrollableFrame(settings_tabs, width=560, height=150)
        settings_tabs.add(optical_tab, text='光谱与光学')
        self.build_optics_form(optical_tab.content)
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
        plot_controls = FlowToolbar(right)
        self.plot_controls = plot_controls
        plot_controls.pack(fill='x', padx=8, pady=4)
        self.full_scan = tk.BooleanVar(value=False)
        self.plot_view = tk.StringVar(value='J–V 曲线')
        choice = ttk.Combobox(plot_controls, textvariable=self.plot_view, state='readonly',
                              values=['J–V 曲线', '入射光谱', '光生分布'], width=11)
        plot_controls.add(choice)
        choice.bind('<<ComboboxSelected>>', lambda e: self.plot())
        plot_controls.add(ttk.Checkbutton(plot_controls, text='完整扫描', variable=self.full_scan, command=self.plot))
        self.show_previous = tk.BooleanVar(value=True)
        plot_controls.add(ttk.Checkbutton(plot_controls, text='上次结果', variable=self.show_previous, command=self.plot))
        plot_controls.button('清除对照', self.clear_comparison)
        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.fig.set_layout_engine('tight', pad=1.2)
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
        self.update_optical_mode()
        self.plot()

    def build_optics_form(self, form):
        self.optical_mode = tk.StringVar(value='光谱 · TMM')
        self.illumination = tk.StringVar(value='左侧入射')
        self.optical_specs = {key: deepcopy(OPTICS_DEFAULTS[key]) for key in ('spectrum', 'substrate', 'tco', 'back')}
        self.optical_choices = {}
        self.optical_combos = {}
        self.optical_numbers = {}
        for row, (label, var, values) in enumerate([
                ('光照模型', self.optical_mode, ['光谱 · TMM', '手动光生率 G']),
                ('照射方向', self.illumination, ['左侧入射', '右侧入射'])]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=4, pady=4)
            combo = ttk.Combobox(form, textvariable=var, values=values, state='readonly', width=20)
            combo.grid(row=row, column=1, sticky='ew', padx=4)
            self.input_widgets.append(combo)
            var.trace_add('write', self.mark_dirty)
        self.optical_mode.trace_add('write', self.update_optical_mode)
        for row, (key, label) in enumerate([('spectrum', '入射光谱'), ('substrate', '入射基底 n,k'),
                                            ('tco', '前透明电极 n,k'), ('back', '背电极 n,k')], 2):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=4, pady=4)
            var = tk.StringVar(value=self.optical_specs[key]['name'])
            self.optical_choices[key] = var
            combo = ttk.Combobox(form, textvariable=var, values=['AM1.5G'] if key == 'spectrum' else list(NK_LIBRARY),
                                 state='readonly', width=20)
            self.optical_combos[key] = combo
            combo.grid(row=row, column=1, sticky='ew', padx=4)
            combo.bind('<<ComboboxSelected>>', lambda e, key=key: self.choose_optical_source(key))
            button = ttk.Button(form, text='导入…', command=lambda key=key: self.import_optical_source(key))
            button.grid(row=row, column=2, padx=4)
            self.input_widgets.extend([combo, button])
        for row, (key, label, unit) in enumerate([
                ('wavelength_min', '光生波段起点', 'nm'), ('wavelength_max', '光生波段终点', 'nm'),
                ('tco_thickness', '前透明电极厚度', 'nm；0 表示不使用'), ('back_thickness', '背电极厚度', 'nm')], 6):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=4, pady=4)
            var = tk.StringVar(value=f'{OPTICS_DEFAULTS[key]:g}')
            self.optical_numbers[key] = var
            entry = ttk.Entry(form, textvariable=var, width=20)
            entry.grid(row=row, column=1, sticky='ew', padx=4)
            ttk.Label(form, text=unit).grid(row=row, column=2, sticky='w', padx=4)
            self.input_widgets.append(entry)
            var.trace_add('write', self.mark_dirty)
        ttk.Label(form, text='每层 n,k 在左侧「光学参数」中选择。光学基底及额外电极不改变电学层。\n'
                  '效率分母采用完整光谱；光生仅计算指定波段。CSV 光谱单位：nm、W/m²/nm。',
                  wraplength=450, foreground='#805c16').grid(row=10, column=0, columnspan=3, sticky='w', padx=4, pady=8)

    def update_optical_mode(self, *_):
        if not hasattr(self, 'optical_mode'):
            return
        spectral = self.optical_mode.get() == '光谱 · TMM'
        self.mode_badge.set('AM1.5G / 自定义光谱 · TMM' if spectral else '手动 G · 稳态输运')
        self.G_entry.configure(state='disabled' if spectral or self.running else 'normal')

    def choose_layer_nk(self, event=None):
        name = self.nk_choice.get()
        if name in NK_LIBRARY:
            self.layers[self.index]['optical_nk'] = dict(kind='builtin', name=name)
        self.update_nk_note()
        self.mark_dirty()

    def update_nk_note(self):
        a = self.layers[self.index]
        spec = a.get('optical_nk')
        try:
            values = nk_table(spec)
            self.nk_note.set(f"{spec['name']} · {len(values)} 个点\n覆盖 {values[0,0]:g}–{values[-1,0]:g} nm")
        except ValueError as e:
            self.nk_note.set(str(e))

    def import_layer_nk(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('n,k 数据（nm）', '*.csv *.txt')])
        if not path:
            return
        try:
            self.layers[self.index]['optical_nk'] = read_csv(path, 3)
            self.nk_combo.configure(values=list(NK_LIBRARY)+[self.layers[self.index]['optical_nk']['name']])
            self.nk_choice.set(self.layers[self.index]['optical_nk']['name'])
            self.update_nk_note()
            self.mark_dirty()
        except Exception as e:
            messagebox.showerror('导入失败', str(e), parent=self)

    def choose_optical_source(self, key):
        name = self.optical_choices[key].get()
        if name == 'AM1.5G' or name in NK_LIBRARY:
            self.optical_specs[key] = dict(kind='builtin', name=name)
        self.mark_dirty()

    def import_optical_source(self, key):
        path = filedialog.askopenfilename(parent=self, filetypes=[('光学数据', '*.csv *.txt')])
        if not path:
            return
        try:
            self.optical_specs[key] = read_csv(path, 2 if key == 'spectrum' else 3)
            values = (['AM1.5G'] if key == 'spectrum' else list(NK_LIBRARY))+[self.optical_specs[key]['name']]
            self.optical_combos[key].configure(values=values)
            self.optical_choices[key].set(self.optical_specs[key]['name'])
            self.mark_dirty()
        except Exception as e:
            messagebox.showerror('导入失败', str(e), parent=self)

    def collect_optics(self):
        o = deepcopy(self.optical_specs)
        o.update(mode='spectrum' if self.optical_mode.get() == '光谱 · TMM' else 'manual',
                 illumination='left' if self.illumination.get() == '左侧入射' else 'right')
        try:
            o.update({key: float(var.get()) for key, var in self.optical_numbers.items()})
        except ValueError:
            raise ValueError('光学波段和厚度请输入有效数值') from None
        return o

    def show_optical_settings(self, settings):
        o = get_optics(settings)
        self.optical_mode.set('光谱 · TMM' if o['mode'] == 'spectrum' else '手动光生率 G')
        self.illumination.set('左侧入射' if o['illumination'] == 'left' else '右侧入射')
        for key, var in self.optical_numbers.items():
            var.set(f'{o[key]:g}')
        for key in self.optical_specs:
            self.optical_specs[key] = deepcopy(o[key])
            values = ['AM1.5G'] if key == 'spectrum' else list(NK_LIBRARY)
            if o[key]['kind'] == 'custom':
                values.append(o[key]['name'])
            self.optical_combos[key].configure(values=values)
            self.optical_choices[key].set(o[key]['name'])
        self.update_optical_mode()

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
        self.settings_toggle.configure(text='模拟设置 ▴' if visible else '模拟设置 ▾')

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
        spec = a.get('optical_nk')
        if spec is None:
            absorber = min(range(len(self.layers)), key=lambda i: self.layers[i]['Eg'])
            spec = a['optical_nk'] = default_nk(self.index, absorber)
        self.nk_combo.configure(values=list(NK_LIBRARY)+([spec['name']] if spec['kind'] == 'custom' else []))
        self.nk_choice.set(spec['name'])
        self.update_nk_note()
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
        s['optics'] = self.collect_optics()
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
        self.update_optical_mode()

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
        if self.plot_view.get() != 'J–V 曲线':
            self.plot_optics()
            self.canvas.draw_idle()
            return
        if self.result is None:
            ax.set_title('输入器件参数后，点击「运行 J–V 模拟」', fontsize=11)
        else:
            r = self.result
            if self.show_previous.get() and self.previous_result is not None:
                prev = self.previous_result
                ax.plot(prev['voltage'], prev['current'], color='#a15d14', ls='--', lw=1.6, label='上次结果')
            light = r.get('optics', {}).get('mode') == 'spectrum' or r['settings']['G'] > 0
            ax.plot(r['voltage'], r['current'], color='#087e8b', lw=2, label='光照 J–V' if light else '暗态 J–V')
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
        self.canvas.draw_idle()

    def plot_optics(self):
        ax = self.ax
        view = self.plot_view.get()
        ax.set(xlabel='波长 λ (nm)' if view == '入射光谱' else '位置 x (nm)',
               ylabel='辐照度 (W/m²/nm)' if view == '入射光谱' else r'光生率 G (cm$^{-3}$ s$^{-1}$)')
        if self.result is None or self.result.get('optics', {}).get('mode') != 'spectrum':
            ax.set_title('运行光谱模式后查看光谱及光生分布', fontsize=11)
            return
        r = self.result
        o = r['optics']
        if view == '入射光谱':
            table = np.asarray(o['spectrum_nm_W_m2_nm'])
            ax.plot(table[:, 0], table[:, 1], color='#d18b37', lw=1.2, label=o['spectrum_name'])
            lo, hi = o['settings']['wavelength_min'], o['settings']['wavelength_max']
            ax.axvspan(lo, hi, color='#087e8b', alpha=.1, label='光生计算波段')
            ax.set_title(f"完整入射光谱 · {o['incident_power_mW_cm2']:g} mW/cm²", fontsize=11)
        else:
            ax.plot(o['x_nm'], o['G_cm3_s'], color='#087e8b', lw=2, label='当前 G(x)')
            x = 0.
            for a in r['layers']:
                ax.axvspan(x, x+a['thickness'], color='#087e8b' if a.get('absorber') else '#8096a7', alpha=.07)
                x += a['thickness']
            ax.set_title(f"光生分布 · q∫Gdx = {r['generated_current']:.3f} mA/cm²", fontsize=11)
            ax.ticklabel_format(axis='y', style='sci', scilimits=(0, 0))
            if self.show_previous.get() and self.previous_result is not None:
                previous = self.previous_result.get('optics', {})
                if previous.get('mode') == 'spectrum':
                    ax.plot(previous['x_nm'], previous['G_cm3_s'], color='#a15d14', ls='--', label='上次 G(x)')
        ax.set_ylim(bottom=0)
        ax.legend(loc='best')

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
            absorber = min(range(len(data['layers'])), key=lambda i: data['layers'][i]['Eg'])
            for i, a in enumerate(data['layers']):
                a.setdefault('optical_nk', default_nk(i, absorber))
            validate_device(data['layers'], data['settings'])
            self.loading = True
            self.layers = data['layers']
            self.index = 0
            for key, var in self.settings.items():
                var.set(f"{data['settings'][key]:g}")
            self.left_anode.set(bool(data['settings']['left_anode']))
            self.show_optical_settings(data['settings'])
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
        r = self.result
        if self.plot_view.get() == 'J–V 曲线':
            table = np.column_stack([r['voltage'], r['current']])
            header = 'V_terminal_V,J_output_mA_cm2'
        elif r.get('optics', {}).get('mode') != 'spectrum':
            messagebox.showinfo('尚无光学结果', '请运行光谱模式后导出光谱或光生分布。', parent=self)
            return
        elif self.plot_view.get() == '入射光谱':
            table = np.asarray(r['optics']['spectrum_nm_W_m2_nm'])
            header = 'wavelength_nm,irradiance_W_m2_nm'
        else:
            o = r['optics']
            table = np.column_stack([o['x_nm'], o['G_cm3_s'], o['layer_index']])
            header = 'x_nm,G_cm3_s,layer_index_zero_based'
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', filetypes=[('当前曲线', '*.csv')])
        if path:
            try:
                np.savetxt(path, table, delimiter=',', header=header, comments='')
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
