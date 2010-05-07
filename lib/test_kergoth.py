#!/usr/bin/env python

import unittest
import sys
import os

basedir = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
oedir = os.path.dirname(basedir)
searchpath = [os.path.join(basedir, "lib"),
              os.path.join(oedir, "openembedded", "lib"),
              os.path.join(oedir, "bitbake", "lib")]
sys.path[0:0] = searchpath

import bb.data
import kergoth

class TestExpansions(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()
        self.d["foo"] = "value of foo"
        self.d["bar"] = "value of bar"
        self.d["value of foo"] = "value of 'value of foo'"

    def test_one_var(self):
        val = kergoth.Value("${foo}", self.d)
        self.assertEqual(str(val), "value of foo")
        self.assertEqual(set(val.references()), set(["foo"]))

    def test_indirect_one_var(self):
        val = kergoth.Value("${${foo}}", self.d)
        self.assertEqual(str(val), "value of 'value of foo'")
        self.assertEqual(set(val.references()), set(["foo"]))

    def test_indirect_and_another(self):
        val = kergoth.Value("${${foo}} ${bar}", self.d)
        self.assertEqual(str(val), "value of 'value of foo' value of bar")
        self.assertEqual(set(val.references()), set(["foo", "bar"]))

    def test_python_snippet(self):
        val = kergoth.Value("${@5*12}", self.d)
        self.assertEqual(str(val), "60")
        self.assertFalse(set(val.references()))

    def test_expand_in_python_snippet(self):
        val = kergoth.Value("${@'boo ' + '${foo}'}", self.d)
        self.assertEqual(str(val), "boo value of foo")
        self.assertEqual(set(val.references()), set(["foo"]))

    def test_python_snippet_getvar(self):
        val = kergoth.Value("${@d.getVar('foo', True) + ' ${bar}'}", self.d)
        self.assertEqual(str(val), "value of foo value of bar")
        self.assertEqual(set(val.references()), set(["foo", "bar"]))

    def test_value_containing_value(self):
        otherval = kergoth.Value("${@d.getVar('foo', True) + ' ${bar}'}", self.d)
        val = kergoth.Value(kergoth.Components([otherval, " test"]), self.d)
        self.assertEqual(str(val), "value of foo value of bar test")
        self.assertEqual(set(val.references()), set(["foo", "bar"]))

    def test_reference_undefined_var(self):
        val = kergoth.Value("${undefinedvar} meh", self.d)
        self.assertEqual(str(val), "${undefinedvar} meh")
        self.assertEqual(set(val.references()), set(["undefinedvar"]))

    def test_direct_recursion(self):
        self.d.setVar("FOO", "${FOO}")
        self.assertRaises(kergoth.RecursionError, kergoth.new_value, "FOO", self.d)

    def test_indirect_recursion(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${BAZ}")
        self.d.setVar("BAZ", "${FOO}")
        self.assertRaises(kergoth.RecursionError, kergoth.new_value, "FOO", self.d)

    def test_recursion_exception(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${BAZ}")
        self.d.setVar("BAZ", "${FOO}")
        try:
            value = kergoth.new_value("FOO", self.d)
        except kergoth.RecursionError, exc:
            self.assertEqual(exc.variable, "FOO")
            self.assertEqual(list(exc.path), ["FOO", "BAR", "BAZ"])

    def test_recursion_exception_expansion_time(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${${@'FOO'}}")
        value = kergoth.new_value("FOO", self.d)
        try:
            str(value)
        except kergoth.RecursionError, exc:
            self.assertEqual(exc.variable, "FOO")
            self.assertTrue(kergoth.new_value("BAR", self.d) in exc.path)


def test_memoize():
    d = bb.data.init()
    d.setVar("FOO", "bar")
    assert(kergoth.new_value("FOO", d) is kergoth.new_value("FOO", d))

class TestContentsTracking(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()

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

    def test_python(self):
        self.d.setVar("FOO", self.pydata)
        self.d.setVarFlags("FOO", {"func": True, "python": True})

        value = kergoth.new_value("FOO", self.d)
        self.assertEquals(set(value.references()), set(["somevar", "bar", "something", "inexpand"]))
        self.assertEquals(set(value.calls), set(["test2", "a"]))

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

    def test_shell(self):
        self.d.setVar("somevar", "heh")
        self.d.setVar("inverted", "echo inverted...")
        self.d.setVarFlag("inverted", "func", True)

        shellval = kergoth.ShellValue(self.shelldata, self.d)
        self.assertEquals(set(shellval.references()), set(["somevar", "inverted"]))

class TestSignatureGeneration(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()
        self.d["BB_HASH_BLACKLIST"] = "blacklisted*"

    def test_full_signature(self):
        self.d.setVar("alpha", "echo ${TOPDIR}/foo \"$@\"")
        self.d.setVarFlags("alpha", {"func": True, "task": True})
        self.d.setVar("beta", "test -f bar")
        self.d.setVarFlags("beta", {"func": True, "task": True})
        self.d.setVar("theta", "alpha baz")
        self.d.setVarFlags("theta", {"func": True, "task": True})
        signature = kergoth.Signature(self.d)
        self.assertEquals(signature.data_string, "{'alpha': ShellValue(['echo ', VariableRef(['TOPDIR']), '/foo \"$@\"']), 'beta': ShellValue(['test -f bar']), 'theta': ShellValue(['alpha baz'])}")

    def test_signature_blacklisted(self):
        self.d["blacklistedvar"] = "blacklistedvalue"
        self.d["testbl"] = "${@5} foo ${blacklistedvar} bar"
        signature = kergoth.Signature(self.d, keys=["testbl"])
        self.assertEqual(signature.data_string, "{'testbl': Value([PythonSnippet(['5']), ' foo ', '${blacklistedvar}', ' bar'])}")

    def test_signature_only_blacklisted(self):
        self.d["anotherval"] = "${blacklistedvar}"
        signature = kergoth.Signature(self.d, keys=["anotherval"])
        self.assertEquals(signature.data_string, "{'anotherval': Value(['${blacklistedvar}'])}")

    def test_signature_undefined(self):
        self.d["someval"] = "${undefinedvar} ${blacklistedvar} meh"
        signature = kergoth.Signature(self.d, keys=["someval"])
        self.assertEquals(signature.data_string, "{'someval': Value([VariableRef(['undefinedvar']), ' ', '${blacklistedvar}', ' meh'])}")


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