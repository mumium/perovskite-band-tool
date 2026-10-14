"""Small-screen Tk scrolling without process-wide event bindings."""
import tkinter as tk
from tkinter import ttk


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, width=340, height=180, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, width=width, height=height)
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
