#!/usr/bin/env python
#
# TODO:
#   Python:
#   - Convert to a Value subclass
#   - The current PythonValue class is more like VariableRef than a value, so
#     either change its superclass or at least rename it to make this more
#     clear, and to avoid confusion with the python function/task class.
#   - Think about checking imports to exclude more direct func calls
#   - Capture FunctionDef's to exclude them from the direct func calls list
#     - NOTE: This will be inaccurate, since it won't be accounting for
#             contexts initially.

import sys
import os
import ast
import codegen

basedir = os.path.dirname(sys.argv[0])
searchpath = [os.path.join(basedir, "bitbake", "lib"),
              os.path.join(basedir, "openembedded", "lib")]
sys.path[0:0] = searchpath

from pysh import pyshyacc, pyshlex
import bb.data
import oe.kergoth


def test_var_expansion():
    d = bb.data.init()
    d["foo"] = "value of foo"
    d["bar"] = "value of bar"
    d["value of foo"] = "value of 'value of foo'"

    val = oe.kergoth.Value("${foo}", d)
    assert(str(val) == "value of foo")
    assert(list(val.references()) == ["foo"])

    val = oe.kergoth.Value("${${foo}}", d)
    assert(str(val) == "value of 'value of foo'")
    assert(list(val.references()) == ["foo"])

    val = oe.kergoth.Value("${${foo}} ${bar}", d)
    assert(str(val) == "value of 'value of foo' value of bar")
    assert(list(val.references()) == ["foo", "bar"])

    val = oe.kergoth.Value("${@5*12}", d)
    assert(str(val) == "60")
    assert(not list(val.references()))

    val = oe.kergoth.Value("${@'boo ' + '${foo}'}", d)
    assert(str(val) == "boo value of foo")
    assert(list(val.references()) == ["foo"])


shelldata = """
    foo () {
        bar
    }
    {
        echo baz
        $(heh)
        eval `moo`
    }
    a=b
    c=d
    (
        true && false
        test -f foo
        testval=something
        $testval
    ) || aiee
    ! inverted
    echo ${somevar}
"""

def test_shell():
    d = bb.data.init()
    d.setVar("somevar", "heh")
    d.setVar("inverted", "echo inverted...")
    d.setVarFlag("inverted", "func", True)

    shellval = oe.kergoth.ShellValue(shelldata, d)
    assert(set(shellval.references()) == set(["somevar", "inverted"]))


def _compare_name(strparts, node):
    if not strparts:
        return True

    current, rest = strparts[0], strparts[1:]
    if isinstance(node, ast.Attribute):
        if current == node.attr:
            return _compare_name(rest, node.value)
    elif isinstance(node, ast.Name):
        if current == node.id:
            return True
    #else:
    #    return True
    return False

def compare_name(value, node):
    if isinstance(value, basestring):
        return _compare_name(tuple(reversed(value.split("."))), node)
    else:
        return any(compare_name(item, node) for item in value)

pydata = """
bb.data.getVar('somevar', d, True)
def test():
    foo = 'bar %s' % 'foo'
    def test2():
        d.getVar(foo, True)
    d.getVar('bar', False)
    test2()

def a():
    return "heh"

bb.data.expand(bb.data.getVar("something", False, d), d)
bb.data.expand("${inexpand} somethingelse", d)
bb.data.getVar(a(), d, False)
"""

def test_python():
    class Visit(ast.NodeVisitor):
        getvars = ("d.getVar", "bb.data.getVar", "data.getVar")
        expands = ("d.expand", "bb.data.expand", "data.expand")

        def __init__(self):
            self.var_references = set()
            self.direct_func_calls = set()

        def warn(self, func, arg):
            print("Warning: in call to '%s', argument '%s' is not a literal string, unable to track reference" %
                  (codegen.to_source(func), codegen.to_source(arg)))

        def visit_Call(self, node):
            ast.NodeVisitor.generic_visit(self, node)
            if compare_name(self.getvars, node.func):
                if isinstance(node.args[0], ast.Str):
                    self.var_references.add(node.args[0].s)
                else:
                    self.warn(node.func, node.args[0])
            elif compare_name(self.expands, node.func):
                if isinstance(node.args[0], ast.Str):
                    value = oe.kergoth.Value(node.args[0].s, bb.data.init())
                    for var in value.references():
                        self.var_references.add(var)
                elif isinstance(node.args[0], ast.Call) and \
                     compare_name(self.getvars, node.args[0].func):
                    pass
                else:
                    self.warn(node.func, node.args[0])
            elif isinstance(node.func, ast.Name):
                self.direct_func_calls.add(node.func.id)

    code = compile(pydata, "<string>", "exec", ast.PyCF_ONLY_AST)
    visitor = Visit()
    visitor.visit(code)
    assert(visitor.var_references == set(["bar", "somevar", "something", "inexpand"]))
    assert(visitor.direct_func_calls == set(["test2", "a"]))


if __name__ == "__main__":
    for name, value in globals().items():
        if name.startswith("test_") and \
           hasattr(value, "__call__"):
            value()