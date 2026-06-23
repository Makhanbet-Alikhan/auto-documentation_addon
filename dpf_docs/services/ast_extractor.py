# -*- coding: utf-8 -*-
"""Static source-code extraction helpers.

This module is intentionally free of any Odoo dependency so it can be unit
tested in isolation. It parses Python source files to recover:

* class / method docstrings (``ast.get_docstring``);
* the docstring-like prose attached to ``fields.*`` assignments;
* plain ``#`` comments, which are NOT available at runtime because the Python
  interpreter discards them. We recover them with ``tokenize`` and attach each
  comment to the nearest following line of real code.
* ValidationError messages raised inside @api.constrains methods (PROBLEM 4).

The output is a plain dictionary so it can be serialised to JSON and shipped
around (controller, worker, renderer) without any ORM object in the way.
"""
import ast
import io
import logging
import tokenize

_logger = logging.getLogger(__name__)


class SourceFileDoc:
    """Parsed documentation for a single ``.py`` source file."""

    def __init__(self, path):
        self.path = path
        self.module_docstring = None
        self.classes = {}   # class_name -> {"doc": str, "lineno": int}
        self.methods = {}   # "Class.method" -> {"doc": str, "lineno": int}
        self.fields = {}    # "Class.field" -> {"comment": str, "lineno": int}
        self.comments = {}  # lineno -> comment text (without leading "# ")
        # PROBLEM 4: method_name -> first ValidationError message string
        self.validation_errors = {}  # method_name -> str

    def to_dict(self):
        return {
            "path": self.path,
            "module_docstring": self.module_docstring,
            "classes": self.classes,
            "methods": self.methods,
            "fields": self.fields,
            "comments": self.comments,
            "validation_errors": self.validation_errors,
        }


def _field_name_from_assign(node):
    """Return the assigned name if the node is ``name = fields.X(...)``.

    Returns ``None`` when the assignment is not a field declaration.
    """
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    if not isinstance(target, ast.Name):
        return None
    value = node.value
    if not isinstance(value, ast.Call):
        return None
    func = value.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "fields":
            return target.id
    return None


def _collect_comments(source):
    """Map a 1-based line number to its trailing/standalone comment text."""
    comments = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                text = tok.string.lstrip("#").strip()
                if text:
                    comments[tok.start[0]] = text
    except (tokenize.TokenError, IndentationError) as exc:
        _logger.warning("Could not tokenize comments: %s", exc)
    return comments


def _nearest_comment_block(comments, lineno):
    """Join the contiguous comment lines that sit directly above ``lineno``."""
    collected = []
    cursor = lineno - 1
    while cursor in comments:
        collected.append(comments[cursor])
        cursor -= 1
    collected.reverse()
    if lineno in comments:
        collected.append(comments[lineno])
    return " ".join(collected).strip()


def _extract_validation_error_message(func_node):
    """Return the first ValidationError message string inside ``func_node``.

    Searches for AST nodes matching::

        raise ValidationError("some message")
        raise ValidationError(_("some message"))

    Returns the message string or ``""`` if none found.

    PROBLEM 4 fix.
    """
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if exc is None:
            continue
        # Support both ``raise ValidationError(...)`` and
        # ``raise ValidationError(_("..."))`
        call = None
        if isinstance(exc, ast.Call):
            call = exc
        elif isinstance(exc, ast.Attribute) and isinstance(exc.value, ast.Call):
            call = exc.value
        if call is None:
            continue
        # Check that the callee is named ValidationError
        callee = call.func
        callee_name = ""
        if isinstance(callee, ast.Name):
            callee_name = callee.id
        elif isinstance(callee, ast.Attribute):
            callee_name = callee.attr
        if callee_name != "ValidationError":
            continue
        # Extract first positional argument
        if not call.args:
            continue
        first_arg = call.args[0]
        # Direct string literal
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            return first_arg.value
        # _("...") translation call
        if isinstance(first_arg, ast.Call) and first_arg.args:
            inner = first_arg.args[0]
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                return inner.value
    return ""


def extract_from_source(source, path="<string>"):
    """Parse ``source`` and return a :class:`SourceFileDoc`.

    Never raises on malformed input: a best-effort partial result is returned
    so that one broken file does not abort documenting an entire module.
    """
    result = SourceFileDoc(path)
    comments = _collect_comments(source)
    result.comments = comments

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        _logger.warning("Could not parse %s: %s", path, exc)
        return result

    result.module_docstring = ast.get_docstring(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            result.classes[node.name] = {
                "doc": ast.get_docstring(node),
                "lineno": node.lineno,
            }
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    key = "%s.%s" % (node.name, child.name)
                    result.methods[key] = {
                        "doc": ast.get_docstring(child),
                        "lineno": child.lineno,
                    }
                    # PROBLEM 4: extract ValidationError message for constraint methods
                    msg = _extract_validation_error_message(child)
                    if msg:
                        result.validation_errors[child.name] = msg

                field_name = _field_name_from_assign(child)
                if field_name:
                    key = "%s.%s" % (node.name, field_name)
                    result.fields[key] = {
                        "comment": _nearest_comment_block(comments, child.lineno),
                        "lineno": child.lineno,
                    }
    return result


def extract_from_file(path):
    """Read ``path`` from disk and delegate to :func:`extract_from_source`."""
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except (IOError, OSError, UnicodeDecodeError) as exc:
        _logger.warning("Could not read %s: %s", path, exc)
        return SourceFileDoc(path)
    return extract_from_source(source, path=path)
