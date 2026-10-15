"""Compact, scrollable view of the current editable functional layers."""
from copy import deepcopy
import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from tkinter.font import Font
from .paths import RESOURCE_ROOT

STRUCTURES_DIR = RESOURCE_ROOT/'device_structures'


class DeviceStructure(ttk.Frame):
    COLORS = ('#e5eff9', '#f9ead5', '#e2f1e8', '#eee7f7', '#e2f0f2')

    def __init__(self, parent, direction_variable=None):
        super().__init__(parent, padding=(6, 0))
        header = ttk.Frame(self)
        header.pack(fill='x')
        ttk.Label(header, text='当前器件结构（示意）', font=('Microsoft YaHei', 10, 'bold')).pack(side='left')
        if direction_variable is not None:
            ttk.Combobox(header, textvariable=direction_variable, values=['左侧入射', '右侧入射'],
                         state='readonly', width=10).pack(side='right')
        self.direction_text = tk.StringVar()
        ttk.Label(header, textvariable=self.direction_text, foreground='#94671d',
                  font=('Microsoft YaHei', 9)).pack(side='left' if direction_variable is not None else 'right', padx=8)
        self.name_font = Font(self, family='Microsoft YaHei', size=9)
        self.canvas = tk.Canvas(self, height=80, bg='white', highlightthickness=0)
        self.canvas.pack(fill='x')
        self.scrollbar = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.scrollbar.set)
        self.layers, self.direction, self.selected, self.note = [], 'left', 0, ''
        self.compact = False
        self.canvas.bind('<Configure>', self.draw)
        self.canvas.bind('<Shift-MouseWheel>', self.scroll)

    def set_layers(self, layers, direction='left', selected=0, note=''):
        self.layers = deepcopy(layers)
        self.direction = 'right' if direction == 'right' else 'left'
        self.direction_text.set('← 右侧入射' if self.direction == 'right' else '左侧入射 →')
        self.selected, self.note = selected, note
        self.draw()

    def scroll(self, event):
        self.canvas.xview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def set_compact(self, compact):
        if self.compact != compact:
            self.compact = compact
            self.canvas.configure(height=40 if compact else 80)
            self.draw()

    def fit_height(self, height, left, right, left_before, right_before):
        """On very short screens use editor space to preserve the result chart."""
        host = left if height < 420 else right
        self.minimum_cell = 80 if host is left else 120
        if getattr(self, 'host', None) is not host:
            self.pack_forget()
            self.pack(in_=host, fill='x', pady=(0, 4), before=left_before if host is left else right_before)
            self.host = host
        self.set_compact(height < 540)

    @staticmethod
    def thickness_text(value):
        try:
            number = float(value)
            if math.isfinite(number) and number > 0:
                return f'{number:g} nm'
        except (TypeError, ValueError):
            pass
        return '厚度待填写'

    def draw(self, event=None):
        c = self.canvas
        c.delete('all')
        viewport = max(1, c.winfo_width())
        width = max(viewport, getattr(self, 'minimum_cell', 120)*len(self.layers)+20)
        if width > viewport+1:
            if not self.scrollbar.winfo_manager(): self.scrollbar.pack(fill='x')
        else:
            self.scrollbar.pack_forget()
            c.xview_moveto(0)
        c.configure(scrollregion=(0, 0, width, 40 if self.compact else 80))
        if not self.layers:
            c.create_text(12, 48, text='请先添加功能层', anchor='w', fill='#617580')
            return
        # Header direction remains visible even when the strip is scrolled.
        if not self.compact and self.direction == 'left':
            c.create_line(12, 13, 77, 13, arrow='last', fill='#d39425', width=2, tags='light-arrow')
            c.create_text(86, 13, text='入射光：左 → 右', anchor='w', fill='#94671d', font=('Microsoft YaHei', 9))
        elif not self.compact:
            c.create_line(width-12, 13, width-77, 13, arrow='last', fill='#d39425', width=2, tags='light-arrow')
            c.create_text(width-86, 13, text='入射光：右 → 左', anchor='e', fill='#94671d', font=('Microsoft YaHei', 9))
        cell = (width-20)/len(self.layers)
        for i, layer in enumerate(self.layers):
            x0, x1 = 10+i*cell, 10+(i+1)*cell-4
            c.create_rectangle(x0, 1 if self.compact else 23, x1, 39 if self.compact else 77, fill=self.COLORS[i % len(self.COLORS)],
                               outline='#087e8b' if i == self.selected else '#cbd9e2',
                               width=2 if i == self.selected else 1, tags=f'layer-{i}')
            # Names wrap within their own cell; never overlap the adjacent layer.
            name = str(layer.get('name', '')).strip() or '未命名层'
            available=cell-20
            lines=['']
            for character in name:
                if self.name_font.measure(lines[-1]+character)>available:
                    if len(lines)==(1 if self.compact else 2):
                        while lines[-1] and self.name_font.measure(lines[-1]+'…')>available:
                            lines[-1]=lines[-1][:-1]
                        lines[-1]+='…'
                        break
                    lines.append('')
                lines[-1]+=character
            c.create_text((x0+x1)/2, 11 if self.compact else 40, text='\n'.join(lines), justify='center',
                          font=self.name_font, fill='#223b4b', tags='layer-name')
            c.create_text((x0+x1)/2, 29 if self.compact else 67, text=self.thickness_text(layer.get('thickness')),
                          font=('Microsoft YaHei', 9, 'bold'), fill='#245565', tags='layer-thickness')


def preview_layers(layers, index, variables):
    """Read name/thickness without committing incomplete numerical form edits."""
    result = deepcopy(layers)
    if 0 <= index < len(result):
        for key in ('name', 'thickness'):
            if key in variables: result[index][key] = variables[key].get()
    return result
