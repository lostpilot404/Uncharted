"""A headless Tk double for launcher layout tests.

It implements only the widget surface the launcher uses. It is deliberately not a Tk emulator:
tests built on it verify structure, navigation, persistence and wiring, never visual appearance.
"""
import types


class TclError(Exception):
    pass


class Var:
    def __init__(self, master=None, value=None, **_kwargs):
        self._value = value
        self._traces = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for callback in list(self._traces):
            callback('', '', 'write')

    def trace_add(self, _mode, callback):
        self._traces.append(callback)
        return f'trace{len(self._traces)}'


class Widget:
    def __init__(self, master=None, **kwargs):
        self.master = master
        self.options = dict(kwargs)
        self.bindings = {}
        self.placed = None
        self.rows = 0
        self.visible = False
        self.destroyed = False
        self.children = []
        if master is not None and hasattr(master, 'children'):
            master.children.append(self)
        self.clipboard = ''

    def __getattr__(self, name):
        # Tk exposes many geometry/query helpers; a no-op keeps the double focused on structure.
        if name.startswith('_'):
            raise AttributeError(name)
        return lambda *_args, **_kwargs: None

    # Tk option access -----------------------------------------------------------------
    def __getitem__(self, key):
        return self.options.get(key, 0)

    def __setitem__(self, key, value):
        self.options[key] = value

    def configure(self, *args, **kwargs):
        if args:
            self.options['selector'] = args[0]
        self.options.update(kwargs)

    config = configure

    def cget(self, key):
        return self.options.get(key)

    # Geometry -------------------------------------------------------------------------
    def pack(self, **kwargs):
        self.placed = ('pack', kwargs)
        self.visible = True

    def pack_forget(self):
        self.visible = False

    def grid(self, **kwargs):
        self.placed = ('grid', kwargs)
        self.visible = True
        row = kwargs.get('row', 0)
        self.rows = max(self.rows, (row if isinstance(row, int) else 0) + 1)
        parent = kwargs.get('in_', self.master)
        if parent is not self and hasattr(parent, 'rows'):
            parent.rows = max(parent.rows, self.rows)

    def grid_size(self):
        return (2, self.rows)

    def columnconfigure(self, *_args, **_kwargs):
        pass

    def rowconfigure(self, *_args, **_kwargs):
        pass

    def lift(self, *_args):
        pass

    def winfo_width(self):
        return 900

    def winfo_height(self):
        return 700

    def winfo_reqheight(self):
        return 1200

    def winfo_y(self):
        return 100

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0

    def winfo_ismapped(self):
        return self.visible

    def winfo_children(self):
        return list(self.children)

    def winfo_pointerxy(self):
        return (0, 0)

    def winfo_fpixels(self, _distance):
        return 96.0

    def focus_displayof(self):
        return self

    # Events ---------------------------------------------------------------------------
    def bind(self, sequence, callback=None, **_kwargs):
        self.bindings[sequence] = callback

    def bind_all(self, _sequence, _callback=None):
        pass

    def unbind_all(self, _sequence):
        pass

    def bind_class(self, _name, _sequence, _callback=None):
        pass

    def after(self, _delay, _callback=None, *_args):
        return 1

    def after_idle(self, _callback=None, *_args):
        return 1

    def after_cancel(self, _identifier):
        pass

    def update_idletasks(self):
        pass

    def protocol(self, _name, _callback):
        pass

    def title(self, _text):
        pass

    def geometry(self, _text):
        pass

    def minsize(self, *_args):
        pass

    def iconbitmap(self, **_kwargs):
        pass

    def option_add(self, *_args):
        pass

    def transient(self, _master):
        pass

    def grab_set(self):
        pass

    def resizable(self, *_args):
        pass

    def wait_window(self):
        pass

    def mainloop(self):
        pass

    def destroy(self):
        self.destroyed = True

    # Canvas / text ---------------------------------------------------------------------
    def create_window(self, *_args, **_kwargs):
        return 1

    def create_image(self, *_args, **_kwargs):
        return 2

    def create_rectangle(self, *_args, **_kwargs):
        return 3

    def create_text(self, *_args, **_kwargs):
        return 4

    def create_line(self, *_args, **_kwargs):
        return 5

    def itemconfigure(self, *_args, **_kwargs):
        pass

    def tag_bind(self, *_args, **_kwargs):
        pass

    def tag_lower(self, *_args):
        pass

    def delete(self, *_args):
        pass

    def insert(self, _index, text, *_args):
        self.options['contents'] = self.options.get('contents', '') + text

    def get(self, *_args):
        return self.options.get('contents', '')

    def index(self, *_args):
        return '1.0'

    def see(self, *_args):
        pass

    def yview(self, *_args):
        pass

    def yview_moveto(self, fraction):
        self.options['view'] = fraction

    def yview_scroll(self, *_args):
        pass

    def current(self, index=None):
        if index is not None:
            self.options['current'] = index
        return self.options.get('current', 0)

    def clipboard_clear(self):
        self.clipboard = ''

    def clipboard_append(self, text):
        self.clipboard += text


class PhotoImage(Widget):
    def put(self, *_args, **_kwargs):
        pass

    def width(self):
        return 24

    def height(self):
        return 16


class Style(Widget):
    def theme_use(self, _name):
        pass

    def element_create(self, *_args, **_kwargs):
        pass

    def layout(self, *_args):
        pass

    def map(self, *_args, **_kwargs):
        pass


def toolkit():
    """(tk, ttk) namespace doubles suitable for ``Launcher(root, tk, ttk, ...)``."""
    tk = types.SimpleNamespace(Frame=Widget, Label=Widget, Canvas=Widget, Button=Widget,
                               Text=Widget, Scale=Widget, Entry=Widget, Toplevel=Widget,
                               PhotoImage=PhotoImage, StringVar=Var, BooleanVar=Var,
                               DoubleVar=Var, TclError=TclError)
    ttk = types.SimpleNamespace(Frame=Widget, Label=Widget, Combobox=Widget, Scrollbar=Widget,
                                Entry=Widget, Button=Widget, Style=Style)
    return tk, ttk


class FakeCard:
    def __init__(self, tk, parent, _colors, _px, title=None, _font=None):
        self.canvas = tk.Canvas(parent)
        self.inner = tk.Frame(self.canvas)
        self.title = title

    def refit(self):
        pass


class FakeControl:
    def __init__(self, _tk, parent, *args, **kwargs):
        self.widget = Widget(parent, text=kwargs.get('text', ''))
        self.kind = kwargs.get('kind', 'secondary')
        self.command = args[1] if len(args) > 1 and callable(args[1]) else None

    def __getattr__(self, name):
        return getattr(self.widget, name)

    def draw(self):
        pass


def widget_module():
    """A stand-in for uncharted_widgets, whose real widgets need a display and PIL/Tk images."""
    module = types.ModuleType('uncharted_widgets')
    module.Card = FakeCard
    module.Toggle = FakeControl
    module.Slider = FakeControl
    module.PillButton = FakeControl
    return module


def fonts(launcher):
    """Replace Launcher.load_fonts: it registers real Windows fonts through tkinter.font."""
    launcher.brand_family = 'Cinzel'
    for name in ('font_brand_xl', 'font_brand_lg', 'font_brand_md', 'font_brand_sm',
                 'font_ui', 'font_ui_sm', 'font_ui_md', 'font_button', 'font_play',
                 'font_title', 'font_card'):
        setattr(launcher, name, ('Segoe UI', 10))
    launcher.font_icon = launcher.font_icon_sm = None
