from diffedit.tui.keys import KeyDecoder, parse_csi


def decoder(*inputs, names=None):
    queue = list(inputs)

    def getch(timeout):
        return queue.pop(0) if queue else None

    names = names or {}
    return KeyDecoder(getch, lambda code: names.get(code, f"UNKNOWN{code}"), {})


def read_all(d):
    out = []
    while True:
        k = d.read(0)
        if k is None:
            return out
        out.append(k)


def test_plain_and_control_characters():
    d = decoder("a", " ", "\x0b", "\r", "\n", "\t", "\x7f", "\x08", "\x00", "\x1f", "\x1c", "é")
    assert read_all(d) == ["a", " ", "C-k", "Enter", "C-j", "Tab", "Backspace", "C-h", "C-Space", "C-_", "C-\\", "é"]


def test_alt_keys_via_escape_prefix():
    d = decoder("\x1b", "u", "\x1b", "U", "\x1b", "\x7f", "\x1b", " ", "\x1b", "\x0b")
    assert read_all(d) == ["M-u", "M-U", "M-Backspace", "M-Space", "M-C-k"]


def test_lone_escape():
    d = decoder("\x1b")
    assert d.read(0) == "Esc"


def test_csi_sequences():
    assert parse_csi("A") == "Up"
    assert parse_csi("1;3B") == "M-Down"
    assert parse_csi("1;5C") == "C-Right"
    assert parse_csi("1;2H") == "S-Home"
    assert parse_csi("3~") == "Delete"
    assert parse_csi("3;5~") == "C-Delete"
    assert parse_csi("200~") == "PasteStart"
    assert parse_csi("201~") == "PasteEnd"
    assert parse_csi("Z") == "S-Tab"
    assert parse_csi("99~") is None


def test_escape_sequences_from_the_stream():
    d = decoder("\x1b", "[", "2", "0", "0", "~", "x", "\x1b", "[", "2", "0", "1", "~", "\x1b", "O", "P")
    assert read_all(d) == ["PasteStart", "x", "PasteEnd", "F1"]


def test_alt_bracket_without_sequence():
    d = decoder("\x1b", "[")
    assert d.read(0) == "M-["


def test_curses_keycodes_and_extended_names():
    names = {300: "kUP3", 301: "kRIT5", 302: "KEY_F(13)", 303: "KEY_BTAB", 304: "kDC5", 305: "kDN4"}
    d = decoder(300, 301, 302, 303, 304, 305, 999, names=names)
    assert [d.read(0) for _ in range(7)] == ["M-Up", "C-Right", "F13", "S-Tab", "C-Delete", "M-S-Down", None]


def test_escape_then_keycode_is_alt():
    d = decoder("\x1b", 300, names={300: "KEY_UP"})
    assert d.read(0) == "M-Up"
