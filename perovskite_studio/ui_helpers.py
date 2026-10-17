"""Small-screen Tk scrolling without process-wide event bindings."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk


def refresh_combobox_on_map(event):
    """Reattach hidden-tab text after native geometry settles, without writes."""
    widget = event.widget
    def refresh():
        if widget.winfo_exists() and widget.winfo_ismapped():
            variable = widget.cget('textvariable')
            if variable:
                widget.configure(textvariable=variable)
            widget.xview_moveto(0)
    widget.after_idle(refresh)


def configure_theme(root):
    """A consistent, quiet palette shared by both simulation pages."""
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(background='#edf2f7')
    style.configure('.', font=('Microsoft YaHei', 10), foreground='#223b4b')
    style.configure('TFrame', background='#ffffff')
    style.configure('TLabel', background='#ffffff')
    style.configure('TCheckbutton', background='#ffffff', padding=(2, 3))
    style.map('TCheckbutton', background=[('active', '#f0f6f8')])
    style.configure('TButton', width=0, padding=(8, 4), background='#f3f6fa', bordercolor='#d6e0e8', relief='flat', shiftrelief=0)
    style.map('TButton', background=[('active', '#e3edf2')], foreground=[('disabled', '#93a3b1')])
    style.configure('Accent.TButton', font=('Microsoft YaHei', 11, 'bold'), background='#087e8b',
                    foreground='white', padding=(16, 10), bordercolor='#087e8b')
    style.map('Accent.TButton', background=[('disabled', '#91b8bd'), ('active', '#056773')],
              foreground=[('disabled', '#e9f3f4')])
    style.configure('EQE.Accent.TButton', background='#7651a8', bordercolor='#7651a8')
    style.map('EQE.Accent.TButton', background=[('disabled', '#b8a5d0'), ('active', '#5d398c')],
              foreground=[('disabled', '#f0eaf7')])
    style.configure('TMenubutton', width=0, padding=(8, 4), background='#f3f6fa', bordercolor='#d6e0e8', relief='flat')
    style.configure('TEntry', padding=(5, 4), fieldbackground='#fbfdff', bordercolor='#d6e0e8')
    style.configure('TCombobox', padding=(4, 4), fieldbackground='#fbfdff', bordercolor='#d6e0e8')
    style.map('TCombobox', fieldbackground=[('readonly', '#fbfdff')])
    root.bind_class('TCombobox', '<Map>', refresh_combobox_on_map, add='+')
    style.configure('TLabelframe', background='#ffffff', bordercolor='#dce5ec', relief='solid')
    style.configure('TLabelframe.Label', background='#ffffff', foreground='#536c7c', font=('Microsoft YaHei', 9))
    style.configure('TNotebook', background='#ffffff', borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab', background='#edf3f7', padding=(12, 7), borderwidth=0)
    style.map('TNotebook.Tab', background=[('selected', '#ffffff')], foreground=[('selected', '#087e8b')])
    style.configure('Main.TNotebook', background='#edf2f7', tabmargins=(10, 8, 10, 0))
    style.configure('Main.TNotebook.Tab', font=('Microsoft YaHei', 12, 'bold'), padding=(22, 9))
    style.map('Main.TNotebook.Tab', background=[('selected', '#ffffff')], foreground=[('selected', '#087e8b')])
    images = []
    for fill in ('#eef3f7', '#dceff0'):
        image = Image.new('RGBA', (24, 24))
        ImageDraw.Draw(image).rounded_rectangle((0, 0, 23, 23), radius=7, fill=fill)
        images.append(ImageTk.PhotoImage(image, master=root))
    style.element_create('PDS.Tab', 'image', images[0], ('selected', images[1]), border=7, sticky='nsew')
    tab_layout = [('PDS.Tab', dict(sticky='nswe', children=[('Notebook.padding', dict(sticky='nswe',
                   children=[('Notebook.label', dict(sticky='nswe'))]))]))]
    for name in ('TNotebook.Tab', 'Main.TNotebook.Tab'):
        style.layout(name, tab_layout)
        padding = (22, 9) if name == 'Main.TNotebook.Tab' else (12, 7)
        # clam otherwise substitutes smaller padding for the selected tab.
        style.map(name, padding=[('selected', padding), ('!selected', padding)],
                  expand=[('selected', (0, 0, 0, 0))])
    checks = []
    for selected in (False, True):
        image = Image.new('RGBA', (18, 18))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, 15, 15), radius=3, fill='#087e8b' if selected else 'white',
                               outline='#087e8b' if selected else '#9fb1bd')
        if selected:
            draw.line([(4, 8), (7, 11), (12, 5)], fill='white', width=2)
        checks.append(ImageTk.PhotoImage(image, master=root))
    style.element_create('PDS.Check', 'image', checks[0], ('selected', checks[1]), sticky='')
    style.layout('TCheckbutton', [('Checkbutton.padding', dict(sticky='nswe', children=[
        ('PDS.Check', dict(side='left', sticky='w')), ('Checkbutton.label', dict(side='left', sticky='w'))]))])
    root._style_images = images+checks
    return style


class FlowToolbar(ttk.Frame):
    """Left-aligned controls that wrap together when a window becomes narrow."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.items = []
        self.bind('<Configure>', self._layout)

    def add(self, widget):
        self.items.append(widget)
        self.after_idle(self._layout)
        return widget

    def button(self, text, command, **kwargs):
        return self.add(ttk.Button(self, text=text, command=command, **kwargs))

    def menu(self, text, commands, **kwargs):
        button = ttk.Menubutton(self, text=text, **kwargs)
        menu = tk.Menu(button, tearoff=False)
        for label, command in commands:
            menu.add_command(label=label, command=command)
        button.configure(menu=menu)
        return self.add(button)

    def _layout(self, event=None):
        if not self.winfo_exists():
            return
        available = max(1, self.winfo_width())
        row = col = used = 0
        for widget in self.items:
            width = widget.winfo_reqwidth()+6
            if used and used+width > available:
                row += 1
                col = used = 0
            widget.grid(row=row, column=col, padx=(0, 6), pady=2, sticky='w')
            col += 1
            used += width


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, width=340, height=180, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, width=width, height=height, background='#ffffff')
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = ttk.Frame(self.canvas, padding=4)
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', self._content_changed)
        self.canvas.bind('<Configure>', self._resized)

    def _content_changed(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self._bind_wheel(self.content)

    def _resized(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _bind_wheel(self, widget):
        if getattr(widget, '_scroll_owner', None) is not self:
            widget.bind('<MouseWheel>', self._wheel, add='+')
            widget._scroll_owner = self
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _wheel(self, event):
        if self.canvas.yview() != (0., 1.):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'
