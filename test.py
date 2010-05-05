#!/usr/bin/env python

import sys
import os

basedir = os.path.abspath(os.path.dirname(__file__))
oedir = os.path.dirname(basedir)
searchpath = [os.path.join(basedir, "lib"),
              os.path.join(oedir, "openembedded", "lib"),
              os.path.join(oedir, "bitbake", "lib")]
sys.path[0:0] = searchpath

import bb.data
import kergoth


def test_var_expansion():
    d = bb.data.init()
    d["foo"] = "value of foo"
    d["bar"] = "value of bar"
    d["value of foo"] = "value of 'value of foo'"

    val = kergoth.Value("${foo}", d)
    assert(str(val) == "value of foo")
    assert(list(val.references()) == ["foo"])

    val = kergoth.Value("${${foo}}", d)
    assert(str(val) == "value of 'value of foo'")
    assert(list(val.references()) == ["foo"])

    val = kergoth.Value("${${foo}} ${bar}", d)
    assert(str(val) == "value of 'value of foo' value of bar")
    assert(list(val.references()) == ["foo", "bar"])

    val = kergoth.Value("${@5*12}", d)
    assert(str(val) == "60")
    assert(not list(val.references()))

    val = kergoth.Value("${@'boo ' + '${foo}'}", d)
    assert(str(val) == "boo value of foo")
    assert(list(val.references()) == ["foo"])

    val = kergoth.Value("${@d.getVar('foo', True) + ' ${bar}'}", d)
    assert(str(val) == "value of foo value of bar")
    assert(set(val.references()) == set(["foo", "bar"]))

    val = kergoth.Value(kergoth.Components([val, " test"]), d)
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

    case foo in
        bar)
            echo bar
            ;;
        baz)
            echo baz
            ;;
        foo*)
            echo foo
            ;;
    esac
"""

def test_shell():
    d = bb.data.init()
    d.setVar("somevar", "heh")
    d.setVar("inverted", "echo inverted...")
    d.setVarFlag("inverted", "func", True)

    shellval = kergoth.ShellValue(shelldata, d)
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
        \"\"\"some
stuff
        \"\"\"
        return "heh"

    bb.data.expand(bb.data.getVar("something", False, d), d)
    bb.data.expand("${inexpand} somethingelse", d)
    bb.data.getVar(a(), d, False)
"""

def test_python():
    d = bb.data.init()
    d.setVar("somevar", pydata)
    d.setVarFlag("somevar", "func", True)
    d.setVarFlag("somevar", "python", True)
    value = kergoth.new_value("somevar", d)
    assert(set(value.references()) == set(["somevar", "bar", "something", "inexpand"]))
    assert(value.visitor.direct_func_calls == set(["test2", "a"]))

def test_signature():
    d = bb.data.init()
    d.setVar("alpha", "echo ${TOPDIR}/foo \"$@\"")
    d.setVarFlags("alpha", {"func": True, "task": True})
    d.setVar("beta", "test -f bar")
    d.setVarFlags("beta", {"func": True, "task": True})
    d.setVar("theta", "alpha baz")
    d.setVarFlags("theta", {"func": True, "task": True})
    print(kergoth.Signature(d))

import pickle
def test_oedata():
    import bb.fetch
    import bb.parse
    import bb.msg
    import bb.utils
    import os.path

    if not os.path.exists("shasum-native-1.0-r1.vars"):
        return

    d = bb.data.init()
    d.setVar("__RECIPEDATA", d)
    d.setVar("BB_HASH_BLACKLIST", "__* *DIR *_DIR_* PATH PWD BBPATH FILE PARALLEL_MAKE")
    vars = pickle.load(open("shasum-native-1.0-r1.vars", "rb"))
    flags = pickle.load(open("shasum-native-1.0-r1.flags", "rb"))
    for key, val in vars.iteritems():
        d.setVar(key, val)
        varflags = flags[key]
        if varflags:
            d.setVarFlags(key, flags[key])
    print(kergoth.Signature(d))

if __name__ == "__main__":
    for name, obj in globals().items():
        if name.startswith("test_") and \
           hasattr(obj, "__call__"):
            obj()