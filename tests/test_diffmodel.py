import random

from troll.diffmodel import build_rows, compute, diff_opcodes, visible_intervals


def apply_opcodes(a, b, ops):
    out = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            assert a[i1:i2] == b[j1:j2]
            out.extend(a[i1:i2])
        else:
            out.extend(b[j1:j2])
    return out


def test_opcodes_reconstruct_target():
    rng = random.Random(7)
    for _ in range(200):
        a = [rng.choice("abcde") for _ in range(rng.randrange(12))]
        b = list(a)
        for _ in range(rng.randrange(4)):
            op = rng.random()
            if op < 0.4 and b:
                del b[rng.randrange(len(b))]
            elif op < 0.8:
                b.insert(rng.randrange(len(b) + 1), rng.choice("xyz"))
            elif b:
                b[rng.randrange(len(b))] = "q"
        assert apply_opcodes(a, b, diff_opcodes(a, b)) == b


def test_compute_added_removed_and_ghosts():
    base = ["a", "b", "c", ""]
    cur = ["a", "B", "c", "d", ""]
    d = compute(base, cur, cur)
    assert d.added == {1, 3}
    assert d.ghosts == {1: ["b"]}
    assert d.hunks == [(1, 2), (3, 4)]
    assert (d.added_count, d.removed_count) == (2, 1)
    assert d.edited == set()


def test_compute_edited_relative_to_commit():
    base = ["x", ""]
    original = ["x", "# old comment", ""]
    cur = ["x", "# new comment", ""]
    d = compute(base, original, cur)
    assert d.added == {1}
    assert d.edited == {1}
    d = compute(base, original, ["x", ""])
    assert d.edit_deletions == {1} and d.hunks == []


def test_new_file_diff():
    d = compute([], ["a", "b"], ["a", "b"])
    assert d.added == {0, 1} and d.hunks == [(0, 2)]


def test_visible_intervals_merge_and_include_cursor():
    base = [str(i) for i in range(30)]
    cur = list(base)
    cur[5] = "X"
    cur[9] = "Y"
    cur[25] = "Z"
    d = compute(base, cur, cur)
    assert visible_intervals(d, 30, 2) == [(3, 12), (23, 28)]
    assert visible_intervals(d, 30, 2, always=(18,)) == [(3, 12), (18, 19), (23, 28)]


def test_build_rows_folds_and_ghosts():
    base = [str(i) for i in range(20)]
    cur = base[:10] + base[11:]  # delete line "10"
    d = compute(base, cur, cur)
    rows = build_rows(d, len(cur), fold=True, context=1, cursor_row=0)
    kinds = [(r.kind, r.row) for r in rows]
    assert kinds[0] == ("line", 0)
    assert ("fold", 1) in kinds
    ghost = [r for r in rows if r.kind == "ghost"]
    assert len(ghost) == 1 and ghost[0].text == "10" and ghost[0].row == 10
    lines_shown = [r.row for r in rows if r.kind == "line"]
    assert lines_shown == [0, 9, 10]
    folds = [r for r in rows if r.kind == "fold"]
    assert sum(f.count for f in folds) + len(lines_shown) == len(cur)


def test_build_rows_unfolded_and_trailing_ghosts():
    base = ["a", "b", "c"]
    cur = ["a"]
    d = compute(base, cur, cur)
    rows = build_rows(d, 1, fold=False, context=3, cursor_row=0)
    assert [(r.kind, r.text) for r in rows] == [("line", ""), ("ghost", "b"), ("ghost", "c")]


def test_side_by_side_rows_pair_changes():
    base = ["a", "old1", "old2", "old3", "z", ""]
    cur = ["a", "new1", "z", "added", ""]
    d = compute(base, cur, cur)
    rows = build_rows(d, len(cur), fold=False, context=3, cursor_row=0, side_by_side=True)
    pairs = [(r.kind, r.row, r.right) for r in rows]
    assert pairs == [
        ("line", 0, 0),      # a | a
        ("line", 1, 1),      # new1 | old1
        ("ghost", 2, 2),     #      | old2
        ("ghost", 2, 3),     #      | old3
        ("line", 2, 4),      # z | z
        ("line", 3, None),   # added |
        ("line", 4, 5),
    ]
    assert d.removed == {1, 2, 3}


def test_side_by_side_folding_matches_unified():
    base = [str(i) for i in range(30)]
    cur = list(base)
    cur[15] = "X"
    d = compute(base, cur, cur)
    side = build_rows(d, 30, fold=True, context=2, cursor_row=15, side_by_side=True)
    unified = build_rows(d, 30, fold=True, context=2, cursor_row=15)
    shown = lambda rows: [r.row for r in rows if r.kind == "line"]  # noqa: E731
    assert shown(side) == shown(unified) == [13, 14, 15, 16, 17]
    assert [r.count for r in side if r.kind == "fold"] == [13, 12]
    assert next(r for r in side if r.row == 15 and r.kind == "line").right == 15
