"""Colors: style name -> (256-color fg, 8-color fg, attributes); bg names likewise."""

from __future__ import annotations

BOLD, DIM, REVERSE, UNDERLINE, ITALIC = 1, 2, 4, 8, 16

# fg styles: (fg256, fg8, attrs)   -1 = terminal default
STYLES: dict[str, tuple[int, int, int]] = {
    "text": (-1, -1, 0),
    "keyword": (204, 5, BOLD),
    "type": (81, 6, 0),
    "builtin": (117, 6, 0),
    "constant": (141, 5, 0),
    "number": (141, 5, 0),
    "string": (186, 3, 0),
    "comment": (245, 2, ITALIC),
    "function": (149, 2, 0),
    "decorator": (214, 3, 0),
    "preproc": (208, 1, 0),
    "heading": (214, 3, BOLD),
    "emph": (180, 3, ITALIC),
    "code": (186, 3, 0),
    "link": (75, 4, UNDERLINE),
    "diff_add": (114, 2, 0),
    "diff_del": (203, 1, 0),
    "diff_hunk": (75, 6, 0),
    "diff_header": (229, 3, BOLD),
    "tag": (204, 5, 0),
    "attr": (149, 2, 0),
    "key": (81, 6, 0),
    "variable": (214, 3, 0),
    "error": (231, 7, BOLD),
    "control": (203, 1, REVERSE),
    "whitespace": (239, 0, 0),
    "label": (245, 7, 0),
    # chrome
    "gutter": (240, 7, DIM),
    "gutter.current": (250, 7, BOLD),
    "gutter.add": (114, 2, BOLD),
    "gutter.del": (203, 1, BOLD),
    "gutter.edit": (220, 3, BOLD),
    "ghost": (210, 1, 0),
    "fold": (244, 4, ITALIC),
    "title": (16, 0, 0),
    "title.brand": (16, 0, BOLD),
    "title.name": (16, 0, BOLD),
    "title.flag": (16, 0, 0),
    "status.info": (16, 0, BOLD),
    "status.error": (231, 7, BOLD),
    "prompt": (-1, -1, 0),
    "prompt.label": (-1, -1, BOLD),
    "help.key": (16, 0, BOLD),
    "help.label": (-1, -1, 0),
    "palette": (252, 7, 0),
    "palette.prompt": (81, 6, BOLD),
    "palette.input": (231, 7, BOLD),
    "palette.placeholder": (243, 7, ITALIC),
    "palette.title": (244, 7, 0),
    "palette.label": (231, 7, BOLD),
    "palette.detail": (248, 7, 0),
    "palette.hint": (81, 6, 0),
    "palette.border": (238, 4, 0),
}

# bg styles: (bg256, bg8)
BACKGROUNDS: dict[str, tuple[int, int]] = {
    "add": (22, -1),
    "del": (52, -1),
    "selection": (24, 4),
    "match": (136, 3),
    "bracket": (240, 6),
    "trailing": (52, 1),
    "title": (252, 7),
    "prompt": (236, -1),
    "help.keybg": (252, 7),
    "palette": (236, 0),
    "palette.sel": (24, 4),
}

# styles whose fg must contrast with a light status bar in 8-color mode
STATUS_BG = {"status.info": (252, 7), "status.error": (160, 1)}
