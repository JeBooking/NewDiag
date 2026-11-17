"""最小桌面应用：Tkinter 窗口 + MVC。

运行：python -m desktop_app（工作目录为 examples/desktop-mvc/app）
"""

try:
    import tkinter as tk
    from tkinter import ttk
except ModuleNotFoundError:
    # 托管解释器常把 Tk 裁掉；流程侧不需要它，只有开窗口才要
    raise SystemExit('当前 Python 没有 Tkinter。换一个带 Tk 的解释器再启动，例如：\n'
                     '  "C:/Users/60273/AppData/Local/Programs/Python/Python39/python.exe" -m desktop_app')

from .controller import Controller
from .model import Model
from .source import Sensor
from .view import View


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('温度监控 · MVC 示例')
        self.geometry('760x520')

        self.view = View(width=720, height=240)
        self.controller = Controller(Model(), self.view, Sensor())

        toolbar = ttk.Frame(self, padding=12)
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text='重新采样', command=self.refresh).pack(side='left')
        self.status = ttk.Label(toolbar, text='')
        self.status.pack(side='left', padx=12)

        self.image_label = ttk.Label(self)
        self.image_label.pack(padx=12, pady=(0, 8))

        self.tree = ttk.Treeview(self, columns=('t', 'celsius'), show='headings', height=8)
        self.tree.heading('t', text='采样点')
        self.tree.heading('celsius', text='温度')
        self.tree.column('t', width=120, anchor='center')
        self.tree.column('celsius', width=120, anchor='center')
        self.tree.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        self.photo = None
        self.refresh()

    def refresh(self) -> None:
        data = self.controller.reload()
        series, summary = data['series'], data['summary']

        self.photo = tk.PhotoImage(data=self.view.render_png(series))
        self.image_label.configure(image=self.photo)

        self.tree.delete(*self.tree.get_children())
        for row in series:
            self.tree.insert('', 'end', values=(row['t'], row['celsius']))

        self.status.configure(text=self.view.text(summary))


def main() -> None:
    Application().mainloop()


if __name__ == '__main__':
    main()
