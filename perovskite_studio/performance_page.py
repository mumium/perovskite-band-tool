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

from .physics import FIELDS, layer
from .ui_helpers import ScrollableFrame, FlowToolbar
from .device_structure import DeviceStructure, preview_layers, STRUCTURES_DIR
from .optics import (NK_LIBRARY, OPTICS_DEFAULTS, get_optics, default_nk,
                    nk_table, read_csv, spectrum_table, optical_model)
from .performance import (DEFAULT_SETTINGS, EXTRA_FIELDS, ENGINE_VERSION,
                         SimulationCancelled, import_layers, run_simulation,
                         validate_device, INTERFACE_DEFAULTS, interface_parameters, EQE_DEFAULTS,
                         run_eqe, loss_at_voltage, validate_eqe)


def jv_export_figure(result, previous=None, limits=None):
    """A fixed-size report whose four metrics belong to the exported solid curve."""
    fig = Figure(figsize=(8.4, 6.4), facecolor='white')
    fig.text(.12, .945, 'J–V 模拟结果', fontsize=16, fontweight='bold', color='#223b4b')
    specs = [('Jsc', 'mA/cm²', 3), ('Voc', 'V', 4), ('FF', '%', 3), ('PCE', '%', 3)]
    for x, (key, unit, decimals) in zip((.12, .335, .55, .765), specs):
        value = result['metrics'][key]
        fig.text(x, .875, key, fontsize=11, color='#586d78')
        fig.text(x, .823, '—' if value is None else f'{value:.{decimals}f}',
                 fontsize=19, fontweight='bold', color='#087e8b')
        fig.text(x, .783, unit, fontsize=10, color='#586d78')
    ax = fig.add_axes([.12, .15, .83, .57])
    if previous is not None:
        ax.plot(previous['voltage'], previous['current'], color='#a15d14', ls='--', lw=1.6, label='上次结果')
    light = result.get('optics', {}).get('mode') == 'spectrum' or result['settings']['G'] > 0
    ax.plot(result['voltage'], result['current'], color='#087e8b', lw=2,
            label='本次光照 J–V' if light else '本次暗态 J–V')
    m = result['metrics']
    if m.get('Vmp') is not None:
        ax.plot(m['Vmp'], m['Jmp'], 'o', color='#cd6930', label='最大功率点')
    if limits is not None:
        ax.set_xlim(limits[0])
        ax.set_ylim(limits[1])
    ax.axhline(0, color='#87929b', lw=.8)
    ax.axvline(0, color='#87929b', lw=.8)
    ax.set(xlabel='端电压 V (V)', ylabel='输出电流密度 J (mA/cm²)')
    ax.grid(alpha=.2)
    ax.legend(loc='best', fontsize=9)
    s = result['settings']
    fig.text(.12, .058, f"Pin = {s['pin']:g} mW/cm²   ·   T = {s['T']:g} K", fontsize=9, color='#586d78')
    note = '四项参数对应本次实线曲线；— 表示尚未确认或不适用。'
    fig.text(.12, .025, note, fontsize=9, color='#586d78')
    return fig


class PerformancePage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.layers = import_layers(app.layers)
        self.index = 0
        self.loading = True
        self.result = None
        self.previous_result = None
        self.eqe_result = None
        self.previous_eqe = None
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
        self.run_button = actions.button('▶ 运行 J–V 模拟', self.run, style='Accent.TButton', width=17)
        self.eqe_button = actions.button('▶ 运行 EQE 模拟', self.run_eqe, style='EQE.Accent.TButton', width=17)
        self.cancel_button = actions.button('取消', self.cancel, state='disabled')
        self.export_buttons = [actions.menu('导出结果', [('当前曲线与参数 CSV', self.export_csv),
                                  ('完整结果 JSON', self.export_result), ('图像与参数 PNG / SVG', self.export_image)], state='disabled')]

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
        self.listbox = tk.Listbox(left, height=5, exportselection=False, font=('Microsoft YaHei', 10), bg='#f6f9fc', fg='#223b4b',
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
        self.layer_tabs = layer_tabs
        self.left = left
        layer_tabs.pack(fill='both', expand=True)
        self.parameter_scroll = ScrollableFrame(layer_tabs, width=345)
        layer_tabs.add(self.parameter_scroll, text='材料')
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
        layer_tabs.add(grading_scroll, text='渐变')
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
        layer_tabs.add(optical_layer, text='光学')
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
        self.edge_mode = tk.StringVar(value='固定 n,k')
        self.reference_gap = tk.StringVar(value='1.55')
        for title, var, values in [('吸收与带隙联动', self.edge_mode, ['固定 n,k', '吸收边联动（近似）']),
                                    ('原始 n,k 参考 Eg / eV', self.reference_gap, None)]:
            ttk.Label(of, text=title).pack(anchor='w', pady=(6, 2))
            widget = ttk.Combobox(of, textvariable=var, values=values, state='readonly') if values else ttk.Entry(of, textvariable=var)
            widget.pack(fill='x', pady=2)
            self.input_widgets.append(widget)
            var.trace_add('write', self.mark_dirty)
        ttk.Label(of, text='CSV：波长_nm,n,k；波长递增，n>0，k≥0。\n吸收边联动：局部 Eg 调整吸收边，n 保持原谱。\n参考 Eg 是原始 n,k 对应的带隙，修改材料 Eg 时不要跟着改。\n基础近似不代表真实组分光学常数；实测数据可选固定模式。', wraplength=320,
                  foreground='#805c16').pack(anchor='w', pady=8)

        interface_tab = ScrollableFrame(layer_tabs, width=345)
        layer_tabs.add(interface_tab, text='界面')
        self.build_interface_form(interface_tab.content)

        self.structure = DeviceStructure(body)
        self.structure.pack(in_=right, fill='x', pady=(0, 4))
        self.structure.host = right
        self.settings_header = ttk.Frame(right)
        self.settings_header.pack(fill='x')
        self.settings_toggle = ttk.Button(self.settings_header, text='模拟设置 ▾', width=11, command=self.toggle_settings)
        self.settings_toggle.pack(side='left', pady=(0, 3))
        self.mode_badge = tk.StringVar(value='AM1.5G · TMM 光学耦合')
        ttk.Label(self.settings_header, textvariable=self.mode_badge, foreground='#536c7c').pack(side='left', padx=8)
        self.settings_visible = False
        self.compact = None
        settings_tabs = ttk.Notebook(right, height=150)
        self.settings_tabs = settings_tabs
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
        eqe_tab = ScrollableFrame(settings_tabs, width=560, height=150)
        settings_tabs.add(eqe_tab, text='EQE')
        self.eqe_vars = {}
        for i, (key, title, unit) in enumerate([('step_nm', '波长步长', 'nm'),
                    ('photon_flux_cm2_s', '探测光子通量', 'cm⁻²/s'), ('background_scale', '背景光比例', '0 为暗态，1 为当前光谱')]):
            ttk.Label(eqe_tab.content, text=title).grid(row=i, column=0, sticky='w', padx=5, pady=4)
            var = tk.StringVar(value=f'{EQE_DEFAULTS[key]:g}')
            self.eqe_vars[key] = var
            entry = ttk.Entry(eqe_tab.content, textvariable=var, width=16)
            entry.grid(row=i, column=1, padx=5)
            ttk.Label(eqe_tab.content, text=unit).grid(row=i, column=2, sticky='w')
            self.input_widgets.append(entry)
            var.trace_add('write', self.mark_dirty)
        ttk.Label(eqe_tab.content, text='短路微分 EQE；使用光学波段，逐波长叠加 1 nm 探测光。\n计算包含输运/复合；EQE 单独保存，切回 J–V 查看其指标。',
                  foreground='#805c16').grid(row=3, column=0, columnspan=3, sticky='w', padx=5, pady=5)
        summary = ttk.Frame(right, padding=(4, 2))
        self.summary = summary
        summary.pack(fill='x')
        self.metric_vars = {}
        for i, (key, title, unit) in enumerate([('Jsc', 'Jsc', 'mA/cm²'), ('Voc', 'Voc', 'V'),
                                                ('FF', 'FF', '%'), ('PCE', 'PCE', '%')]):
            card = ttk.Frame(summary, padding=(8, 0))
            card.grid(row=0, column=i, sticky='ew', padx=4)
            summary.columnconfigure(i, weight=1)
            ttk.Label(card, text=f'{title} · {unit}', font=('Microsoft YaHei', 9),
                      foreground='#617580').pack(anchor='w')
            var = tk.StringVar(value='—')
            self.metric_vars[key] = var
            ttk.Label(card, textvariable=var, font=('Microsoft YaHei', 16, 'bold'), foreground='#245565').pack(anchor='w')
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
                              values=['J–V 曲线', '入射光谱', '光生分布', '损失分析', 'EQE'], width=11)
        plot_controls.add(choice)
        choice.bind('<<ComboboxSelected>>', lambda e: self.plot())
        plot_controls.add(ttk.Checkbutton(plot_controls, text='完整扫描', variable=self.full_scan, command=self.plot))
        self.show_previous = tk.BooleanVar(value=True)
        plot_controls.add(ttk.Checkbutton(plot_controls, text='上次结果', variable=self.show_previous, command=self.plot))
        plot_controls.button('清除对照', self.clear_comparison)
        self.loss_point = tk.StringVar(value='最大功率点')
        self.loss_choice = ttk.Combobox(plot_controls, textvariable=self.loss_point,
                                       values=['短路', '最大功率点', '开路'], width=10, state='disabled')
        plot_controls.add(self.loss_choice)
        self.loss_choice.bind('<<ComboboxSelected>>', lambda e: self.plot())
        self.analysis_note = tk.StringVar()
        self.analysis_label = ttk.Label(right, textvariable=self.analysis_note, wraplength=800, foreground='#536c7c')
        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.fig.set_layout_engine('tight', pad=1.2)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().bind('<Configure>', self.resize_plot, add='+')
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

    def build_interface_form(self, form):
        self.interface_note = tk.StringVar()
        ttk.Label(form, textvariable=self.interface_note, wraplength=320, foreground='#536c7c').grid(row=0, column=0, columnspan=3, sticky='w', pady=7)
        self.interface_vars = {}
        for i, (key, title, unit) in enumerate([('Nt', '界面缺陷浓度', 'cm⁻²'), ('Et', '陷阱真空以下深度', 'eV'),
                                               ('Cn', '电子捕获系数', 'cm³/s'), ('Cp', '空穴捕获系数', 'cm³/s')], 1):
            ttk.Label(form, text=title).grid(row=i, column=0, sticky='w', pady=5)
            var = tk.StringVar(value=f'{INTERFACE_DEFAULTS[key]:g}')
            self.interface_vars[key] = var
            entry = ttk.Entry(form, textvariable=var, width=12)
            entry.grid(row=i, column=1, padx=4)
            ttk.Label(form, text=unit).grid(row=i, column=2, sticky='w')
            self.input_widgets.append(entry)
            if key == 'Et':
                self.interface_energy_entry = entry
            var.trace_add('write', self.mark_dirty)
        self.interface_auto = tk.BooleanVar(value=True)
        check = ttk.Checkbutton(form, text='能级自动取两侧共同禁带中点', variable=self.interface_auto,
                                command=self.update_interface_controls)
        check.grid(row=5, column=0, columnspan=3, sticky='w', pady=5)
        self.input_widgets.append(check)
        self.interface_auto.trace_add('write', self.mark_dirty)
        self.interface_type = tk.StringVar(value='中性')
        ttk.Label(form, text='陷阱类型').grid(row=6, column=0, sticky='w', pady=5)
        combo = ttk.Combobox(form, textvariable=self.interface_type, values=['中性', '受主', '施主'], state='readonly', width=12)
        combo.grid(row=6, column=1, padx=4)
        self.input_widgets.append(combo)
        self.interface_type.trace_add('write', self.mark_dirty)
        ttk.Label(form, text='设置本层与右侧材料的真实界面。\nNt=0 关闭界面陷阱；最后一层忽略此设置。\n捕获系数独立于体寿命，单位 cm³/s。\n自动能级会随两侧能级更新；手动深度须在共同禁带内。\n数值渐变子层之间不添加陷阱。',
                  wraplength=320, foreground='#805c16').grid(row=7, column=0, columnspan=3, sticky='w', pady=7)

    def update_interface_controls(self):
        self.interface_energy_entry.configure(state='disabled' if self.running or self.interface_auto.get() else 'normal')

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
        self.update_structure()
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
        for widget in (self.mpp_label, self.comparison_label, self.analysis_label):
            widget.configure(wraplength=max(200, event.width-20))
        # Resizing after results arrive must not override the user's fold state.
        self.compact = event.height < 500
        self.structure.fit_height(event.height, self.left, self.right, self.layer_tabs, self.settings_header)

    def loss_row_limit(self):
        return max(2, min(8, (self.canvas.get_tk_widget().winfo_height()-100)//24))

    def resize_plot(self, event):
        # The wrapping toolbar briefly leaves a tiny canvas during first map.
        # Avoid layout warnings then, and use smaller margins on short screens.
        self.fig.set_layout_engine('tight' if event.height >= 100 else None,
                                   **({'pad': .5 if event.height < 230 else 1.2} if event.height >= 100 else {}))
        if self.plot_view.get() == '损失分析' and self.loss_row_limit() != getattr(self, 'loss_max_rows', None):
            self.after_idle(self.plot)

    def toggle_settings(self, visible=None):
        visible = not self.settings_visible if visible is None else visible
        self.settings_visible = visible
        if visible:
            before = self.summary if self.summary.winfo_manager() == 'pack' else self.plot_controls
            self.settings_tabs.pack(fill='x', before=before)
        else:
            self.settings_tabs.pack_forget()
        self.settings_toggle.configure(text='模拟设置 ▴' if visible else '模拟设置 ▾')

    def clear_comparison(self):
        self.previous_result = None
        self.previous_eqe = None
        self.comparison_text.set('')
        self.comparison_label.pack_forget()
        self.plot()

    def refresh_list(self):
        self.listbox.delete(0, 'end')
        for a in self.layers:
            self.listbox.insert('end', f"{'☀ ' if a.get('absorber') else ''}{a['name']}  |  {a['thickness']:g} nm")
        self.listbox.selection_set(self.index)
        self.listbox.see(self.index)
        self.update_structure()

    def update_structure(self):
        if hasattr(self, 'structure') and hasattr(self, 'illumination'):
            self.structure.set_layers(preview_layers(self.layers, self.index, self.vars),
                                      'right' if self.illumination.get() == '右侧入射' else 'left', self.index)

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
        model = optical_model(a)
        self.edge_mode.set('吸收边联动（近似）' if model['mode'] == 'edge' else '固定 n,k')
        self.reference_gap.set(f"{model['reference_gap']:g}")
        inter = interface_parameters(a)
        for key, var in self.interface_vars.items():
            var.set(f'{inter[key]:g}')
        self.interface_auto.set(inter['auto_energy'])
        self.interface_type.set({0: '中性', -1: '受主', 1: '施主'}[inter['trap_type']])
        self.interface_note.set('最后一层：无右侧材料界面，以下设置不参与计算。' if self.index == len(self.layers)-1 else
                                a['name']+' → '+self.layers[self.index+1]['name'])
        self.update_interface_controls()
        self.loading = previous
        self.update_structure()

    def commit_layer(self):
        a = deepcopy(self.layers[self.index])
        for key, var in self.vars.items():
            try:
                a[key] = var.get().strip() if key == 'name' or key.endswith('_mode') else float(var.get())
            except ValueError:
                raise ValueError(f'{key} 请输入数值，支持科学计数法') from None
        a['absorber'] = self.absorber.get()
        try:
            a['optical_model'] = dict(mode='edge' if self.edge_mode.get() == '吸收边联动（近似）' else 'fixed',
                                      reference_gap=float(self.reference_gap.get()))
            a['interface'] = {key: float(var.get()) for key, var in self.interface_vars.items()}
            a['interface'].update(auto_energy=self.interface_auto.get(), trap_type={'中性': 0, '受主': -1, '施主': 1}[self.interface_type.get()])
        except (ValueError, KeyError):
            raise ValueError('参考带隙和界面参数请输入有效数值') from None
        if not np.isfinite(a['optical_model']['reference_gap']) or any(not np.isfinite(a['interface'][k]) for k in self.interface_vars):
            raise ValueError('参考带隙与界面参数须为有限数值')
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
        try:
            s['eqe'] = {key: float(var.get()) for key, var in self.eqe_vars.items()}
        except ValueError:
            raise ValueError('EQE 设置请输入有效数值') from None
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
        self.eqe_button.configure(state='disabled' if running else 'normal')
        self.cancel_button.configure(state='normal' if running else 'disabled')
        for button in self.export_buttons:
            button.configure(state='normal' if (self.result is not None or self.eqe_result is not None) and not running else 'disabled')
        self.update_optical_mode()
        self.update_interface_controls()

    def run(self):
        if self.running:
            return
        try:
            layers, settings = self.collect()
        except Exception as e:
            messagebox.showerror('无法开始模拟', str(e), parent=self)
            return
        self.task_kind = 'jv'
        self.set_running(True)
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

    def run_eqe(self):
        if self.running:
            return
        try:
            layers, settings = self.collect()
            validate_eqe(settings)
        except Exception as e:
            messagebox.showerror('无法开始 EQE', str(e), parent=self)
            return
        self.task_kind = 'eqe'
        self.set_running(True)
        self.toggle_settings(False)
        self.cancel_event = threading.Event()
        self.status.set('正在计算 EQE 基线… J–V 与已有 EQE 结果保留至本次成功。')
        self.progress = ttk.Progressbar(self.footer, mode='determinate')
        self.progress.pack(fill='x', pady=3)
        cancel = self.cancel_event

        def worker():
            try:
                result = run_eqe(layers, settings, cancel, lambda i, n: self.events.put(('progress', (i, n))))
                self.events.put(('eqe_result', result))
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
        if kind == 'progress':
            count, total = payload
            self.progress.configure(maximum=total, value=count)
            if not self.cancel_event.is_set():
                self.status.set(f'EQE · {count}/{total} 个波长完成；可取消，上次成功结果保留。')
            self.poll_id = self.after(20, self.poll)
            return
        self.progress.stop()
        self.progress.destroy()
        self.set_running(False)
        if kind == 'eqe_result' and not self.cancel_event.is_set():
            self.previous_eqe = self.eqe_result
            self.eqe_result = payload
            self.plot_view.set('EQE')
            self.status.set(f"EQE 完成 · {payload['elapsed']:.2f} s · {len(payload['EQE'])} 个波长 · "
                            f"背景比例 {payload['eqe_settings']['background_scale']:g} · 光谱加权电流参考 {payload['weighted_current_mA_cm2']:.3f} mA/cm²；J–V 结果独立保留。")
            self.warning.set('；'.join(payload['warnings']))
            for button in self.export_buttons:
                button.configure(state='normal')
            self.plot()
        elif kind == 'result' and not self.cancel_event.is_set():
            self.previous_result = self.result
            self.result = payload
            if self.plot_view.get() == 'EQE':
                self.plot_view.set('J–V 曲线')
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
            self.status.set('本次模拟失败，上次成功结果已保留；请查看错误详情。')
            self.warning.set('未采用本次失败的部分结果；错误详情包含运行阶段和可用的诊断包位置。')
            messagebox.showerror('SimSS 计算失败', payload, parent=self)

    def plot(self):
        ax = self.ax
        ax.clear()
        self.analysis_label.pack_forget()
        if self.plot_view.get() in ('EQE', '损失分析'):
            self.summary.pack_forget()
            self.mpp_label.pack_forget()
            self.comparison_label.pack_forget()
        else:
            self.summary.pack(fill='x', before=self.plot_controls)
            self.mpp_label.pack(anchor='w', padx=8, before=self.plot_controls)
            if self.comparison_text.get():
                self.comparison_label.pack(anchor='w', padx=8, before=self.plot_controls)
        ax.set(xlabel='端电压 V (V)', ylabel='输出电流密度 J (mA/cm²)')
        ax.axhline(0, color='#87929b', lw=.8)
        ax.axvline(0, color='#87929b', lw=.8)
        ax.grid(alpha=.2)
        self.loss_choice.configure(state='readonly' if self.plot_view.get() == '损失分析' else 'disabled')
        if self.plot_view.get() == '损失分析':
            self.plot_losses()
            self.canvas.draw_idle()
            return
        if self.plot_view.get() == 'EQE':
            self.plot_eqe()
            self.canvas.draw_idle()
            return
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

    def show_analysis_note(self, text):
        self.analysis_note.set(text)
        self.analysis_label.pack(fill='x', padx=8, pady=3, before=self.canvas.get_tk_widget())

    def plot_losses(self):
        ax = self.ax
        ax.clear()
        ax.set_xlabel('净复合等效电流 (mA/cm²)')
        if self.result is None:
            ax.set_title('运行 J–V 后查看逐层 / 逐界面损失', fontsize=11)
            return
        r = self.result
        voltage = {'短路': 0., '最大功率点': r['metrics']['Vmp'], '开路': r['metrics']['Voc']}[self.loss_point.get()]
        if voltage is None:
            ax.set_title('当前扫描未确认此工作点，请扩大扫描范围', fontsize=11)
            return
        point = loss_at_voltage(r, voltage)
        loss = r['losses']
        labels = list(loss['layer_names'])+[a['name'] for a in loss['interfaces']]
        count = len(loss['interfaces'])
        bulk = np.r_[point['layer_currents']['bulk'], np.zeros(count)]
        direct = np.r_[point['layer_currents']['direct'], np.zeros(count)]
        interface = np.r_[np.zeros(len(loss['layer_names'])), point['interface_currents']]
        self.loss_max_rows = self.loss_row_limit()
        if len(labels) > self.loss_max_rows:
            order = np.argsort(np.abs(bulk)+np.abs(direct)+np.abs(interface))[::-1]
            chosen, other = order[:self.loss_max_rows-1], order[self.loss_max_rows-1:]
            labels = [labels[i] for i in chosen]+['其余层 / 界面合计']
            bulk, direct, interface = (np.r_[a[chosen], a[other].sum()] for a in (bulk, direct, interface))
        y = np.arange(len(labels))
        ax.barh(y, bulk, color='#087e8b', label='体 SRH')
        ax.barh(y, direct, left=bulk, color='#d18b37', label='双分子')
        ax.barh(y, interface, left=bulk+direct, color='#9871b6', label='界面 SRH')
        ax.set_yticks(y, [a if len(a) < 24 else a[:22]+'…' for a in labels], fontsize=8)
        ax.invert_yaxis()
        ax.grid(axis='x', alpha=.2)
        ax.set_title(f"{self.loss_point.get()} · {voltage:.4f} V · 净复合", fontsize=10)
        ax.legend(loc='best', fontsize=8)
        self.show_analysis_note(f"电流预算 / mA·cm⁻²：光生 {point['photo']:.3f} · 输出 {point['collected']:.3f} · "
            f"体 SRH {point['bulk']:.3f} · 双分子 {point['direct']:.3f} · 界面 {point['interface']:.3f} · "
            f"漏电 {point['shunt']:.3f} · 边界/离散残差 {point['residual']:.3f}。\n"
            f"串联电阻耗散 {point['series_power']:.4g} mW/cm²；以上为净等效电流，不是效率损失百分比。")

    def plot_eqe(self):
        ax = self.ax
        ax.clear()
        ax.set(xlabel='波长 λ (nm)', ylabel='外量子效率 / %')
        ax.grid(alpha=.2)
        if self.eqe_result is None:
            ax.set_title('选择光谱 TMM 模式后，点击「运行 EQE」', fontsize=11)
            return
        r = self.eqe_result
        ax.plot(r['wavelength_nm'], np.asarray(r['EQE'])*100, color='#087e8b', lw=2, label='EQE（含输运与复合）')
        ax.plot(r['wavelength_nm'], np.asarray(r['active_absorptance'])*100, color='#d18b37', ls=':', label='光生吸收参考')
        if self.show_previous.get() and self.previous_eqe is not None:
            p = self.previous_eqe
            ax.plot(p['wavelength_nm'], np.asarray(p['EQE'])*100, color='#9871b6', ls='--', label='上次 EQE')
        ax.axhline(100, lw=.7, color='#9fb1bd')
        ax.set_title(f"短路微分 EQE · 背景光比例 {r['eqe_settings']['background_scale']:g}", fontsize=11)
        ax.legend(loc='best', fontsize=8)
        self.show_analysis_note(f"此图对应最近成功 EQE 输入；J–V 指标切回 J–V 视图查看。光谱加权电流参考 "
            f"{r['weighted_current_mA_cm2']:.3f} mA/cm²；最大数值误差估计 {max(r['numerical_error_estimate'])*100:.3g} 个百分点。")

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
        path = filedialog.askopenfilename(parent=self, initialdir=str(STRUCTURES_DIR), filetypes=[('性能参数', '*.json')])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if data.get('schema') != 1 or data.get('model') != 'simss' or data.get('engine') != ENGINE_VERSION:
                raise ValueError('请选择本工具保存的 SimSS 5.36 性能参数文件；能带项目请在能带页打开后导入。')
            data['settings'].setdefault('grading_slices', DEFAULT_SETTINGS['grading_slices'])
            from .physics import normalize
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
            eqe = dict(EQE_DEFAULTS) | data['settings'].get('eqe', {})
            for key, var in self.eqe_vars.items():
                var.set(f'{eqe[key]:g}')
            self.show_fields()
            self.refresh_list()
            self.loading = False
            self.mark_dirty()
            self.status.set('性能参数已打开，请点击运行。')
        except Exception as e:
            self.loading = False
            messagebox.showerror('打开失败', str(e), parent=self)

    def export_csv(self):
        if self.plot_view.get() == 'EQE':
            r = self.eqe_result
            if r is None:
                return
            table = np.column_stack([r['wavelength_nm'], r['EQE'], r['active_absorptance'], r['numerical_error_estimate']])
            header = 'wavelength_nm,EQE_fraction,active_absorptance_fraction,numerical_error_estimate_fraction'
        elif self.result is None:
            return
        elif self.plot_view.get() == '损失分析':
            loss = self.result['losses']
            keys = ['photo', 'collected', 'bulk', 'direct', 'interface', 'shunt', 'residual', 'series_power']
            columns = [loss['voltage']]+[loss[k] for k in keys]
            names = ['V_terminal_V']+[k+('_mW_cm2' if k == 'series_power' else '_mA_cm2') for k in keys]
            for key in ('photo', 'bulk', 'direct'):
                for i, a in enumerate(loss['layer_currents'][key]):
                    columns.append(a)
                    names.append(f'layer_{i+1}_{key}_mA_cm2')
            for a in loss['interfaces']:
                columns.append(a['current'])
                names.append(f"interface_{a['left_index']+1}_mA_cm2")
            table, header = np.column_stack(columns), ','.join(names)
        elif self.plot_view.get() == 'J–V 曲线':
            r = self.result
            parameters = [np.full(len(r['voltage']), np.nan if r['metrics'][key] is None else r['metrics'][key])
                          for key in ('Jsc', 'Voc', 'FF', 'PCE')]
            table = np.column_stack([r['voltage'], r['current']]+parameters)
            header = 'V_terminal_V,J_output_mA_cm2,Jsc_mA_cm2,Voc_V,FF_percent,PCE_percent'
        elif self.result.get('optics', {}).get('mode') != 'spectrum':
            messagebox.showinfo('尚无光学结果', '请运行光谱模式后导出光谱或光生分布。', parent=self)
            return
        elif self.plot_view.get() == '入射光谱':
            table = np.asarray(self.result['optics']['spectrum_nm_W_m2_nm'])
            header = 'wavelength_nm,irradiance_W_m2_nm'
        else:
            o = self.result['optics']
            table = np.column_stack([o['x_nm'], o['G_cm3_s'], o['layer_index']])
            header = 'x_nm,G_cm3_s,layer_index_zero_based'
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', filetypes=[('当前曲线', '*.csv')])
        if path:
            try:
                np.savetxt(path, table, delimiter=',', header=header, comments='')
            except OSError as e:
                messagebox.showerror('导出失败', str(e), parent=self)

    def export_result(self):
        result = self.eqe_result if self.plot_view.get() == 'EQE' else self.result
        if result is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', filetypes=[('完整模拟结果', '*.json')])
        if path:
            data = {key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in result.items()}
            data.update(created_utc=datetime.now(timezone.utc).isoformat(), current_convention='positive delivered, mA/cm2')
            try:
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            except (OSError, ValueError) as e:
                messagebox.showerror('导出失败', str(e), parent=self)

    def export_image(self):
        if (self.eqe_result if self.plot_view.get() == 'EQE' else self.result) is None:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.png', filetypes=[('PNG', '*.png'), ('SVG', '*.svg')])
        if path:
            try:
                figure = self.fig
                if self.plot_view.get() == 'J–V 曲线':
                    previous = self.previous_result if self.show_previous.get() else None
                    figure = jv_export_figure(self.result, previous,
                                              limits=(self.ax.get_xlim(), self.ax.get_ylim()))
                figure.savefig(path, dpi=200, bbox_inches='tight', facecolor='white')
            except OSError as e:
                messagebox.showerror('保存失败', str(e), parent=self)

    def shutdown(self):
        self.closed = True
        self.cancel_event.set()
        if self.poll_id is not None:
            self.after_cancel(self.poll_id)
            self.poll_id = None
