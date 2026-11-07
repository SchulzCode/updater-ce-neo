"""User-facing GUI text may not be an English literal.

Every visible label, button, tooltip, window title, file-dialog filter and
message-box body has to go through ``tr()`` so it can have a catalog entry in
every language. This test walks the source and inspects the text-bearing Qt
APIs; markup-only fragments such as ``"<b>"`` are ignored.
"""

import ast
import re
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parents[1] / "src"

TEXT_METHODS = {
    "setText",
    "setHtml",
    "setTitle",
    "setWindowTitle",
    "setToolTip",
    "setStatusTip",
    "setWhatsThis",
    "setPlaceholderText",
    "setAccessibleName",
    "setAccessibleDescription",
    "setItemText",
    "setItem",
    "setTabText",
    "setHorizontalHeaderLabels",
    "setHeaderLabels",
    "setVerticalHeaderLabels",
    "addItem",
    "addItems",
    "addAction",
    "addMenu",
    "addTab",
    "addButton",
    "setLabelText",
    "setInformativeText",
    "setDetailedText",
    "setFilter",
    "setNameFilter",
    "showText",
    "setSection",
    "addSection",
    "_show_temp_status",
}

TEXT_CONSTRUCTORS = {
    "QLabel",
    "QPushButton",
    "QCheckBox",
    "QRadioButton",
    "QGroupBox",
    "QAction",
    "QToolButton",
    "QTableWidgetItem",
    "QListWidgetItem",
    "QTreeWidgetItem",
    "QStandardItem",
}

#: Static Qt helpers where the title/text/filter live at fixed positions.
STATIC_ARGS = {
    "getOpenFileName": (1, 3),
    "getSaveFileName": (1, 3),
    "getExistingDirectory": (1,),
    "getText": (1, 2),
    "getItem": (1, 2),
    "getMultiLineText": (1, 2),
    "information": (1, 2),
    "warning": (1, 2),
    "critical": (1, 2),
    "question": (1, 2),
    "about": (1, 2),
}

_TAG = re.compile(r"<[^>]*>")
_INCOMPLETE_TAG = re.compile(r"<[^>]*$")
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|\w+);")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
#: CSS/style fragments that an f-string splits before or after a placeholder.
_STYLE = re.compile(
    r"(?:font-|margin\s*:|padding\s*:|color\s*:|text-decoration\s*:|style\s*=)",
    re.IGNORECASE,
)


def _texts(node) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
    if isinstance(node, (ast.List, ast.Tuple)):
        found = []
        for element in node.elts:
            found.extend(_texts(element))
        return found
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _texts(node.left) + _texts(node.right)
    return []


def _plain(fragment: str) -> str:
    if _STYLE.search(fragment):
        return ""
    # A file-dialog wildcard fragment is not a word: the translated label is
    # passed separately and the pattern is assembled in Python.
    if "*." in fragment:
        return ""
    text = _INCOMPLETE_TAG.sub("", fragment)
    text = _TAG.sub("", text)
    text = _ENTITY.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _hardcoded_literals():
    problems = []
    for path in sorted(SOURCE_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            args = node.args
            fragments = []
            if name in TEXT_METHODS and args:
                fragments = _texts(args[0])
            elif name in TEXT_CONSTRUCTORS and args:
                fragments = _texts(args[0])
            elif name in STATIC_ARGS and isinstance(node.func, ast.Attribute):
                for index in STATIC_ARGS[name]:
                    if len(args) > index:
                        fragments.extend(_texts(args[index]))
            for fragment in fragments:
                text = _plain(fragment)
                if not re.search(r"[A-Za-z]{2,}", text):
                    continue
                if _IDENTIFIER.fullmatch(text):
                    continue
                problems.append(f"{path.relative_to(SOURCE_DIR.parent)}:{node.lineno}: {text[:100]!r}")
    return problems


def test_user_facing_text_is_never_a_hardcoded_literal():
    problems = _hardcoded_literals()
    assert not problems, (
        "hardcoded GUI text must be added to src/i18n.py and wrapped in tr(): "
        + "\n".join(problems[:30])
    )
