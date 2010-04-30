#!/usr/bin/env python

import sys
import os

basedir = os.path.dirname(sys.argv[0])
searchpath = [os.path.join(basedir, "bitbake", "lib"),
              os.path.join(basedir, "openembedded", "lib")]
sys.path[0:0] = searchpath

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

    val = oe.kergoth.Value("${@d.getVar('foo', True) + ' ${bar}'}", d)
    assert(str(val) == "value of foo value of bar")
    assert(set(val.references()) == set(["foo", "bar"]))

    val = oe.kergoth.Value(oe.kergoth.Components([val, " test"]), d)
    assert(str(val) == "value of foo value of bar test")
    assert(set(val.references()) == set(["foo", "bar"]))


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
    d = bb.data.init()
    value = oe.kergoth.PythonValue(pydata, d)
    assert(set(value.references()) == set(["somevar", "bar", "something", "inexpand"]))
    assert(value.visitor.direct_func_calls == set(["test2", "a"]))


if __name__ == "__main__":
    for name, value in globals().items():
        if name.startswith("test_") and \
           hasattr(value, "__call__"):
            value()