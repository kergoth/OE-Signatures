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
import bbvalue
import reftracker

class TestRefTracking(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()

    def assertReferences(self, value, refs):
        self.assertEqual(reftracker.references(value, self.d), refs)

    def assertExecs(self, value, execs):
        self.assertEqual(reftracker.execs(value, self.d), execs)

    def assertCalls(self, value, calls):
        self.assertEqual(reftracker.calls(value, self.d), calls)

    def assertFunctionReferences(self, value, refs):
        self.assertEqual(
            reftracker.function_references(value, self.d), refs)

class TestShell(TestRefTracking):
    def setUp(self):
        super(TestShell, self).setUp()

    def assertReferences(self, value, refs):
        super(TestShell, self).assertReferences(
            bbvalue.shparse(value, self.d), refs)

    def assertExecs(self, value, execs):
        super(TestShell, self).assertExecs(
            bbvalue.shparse(value, self.d), execs)

    def test_quotes_inside_assign(self):
        self.assertReferences('foo=foo"bar"baz', set([]))

    def test_quotes_inside_arg(self):
        self.assertExecs('sed s#"bar baz"#"alpha beta"#g', set(["sed"]))

    def test_arg_continuation(self):
        self.assertExecs("sed -i -e s,foo,bar,g \\\n *.pc", set(["sed"]))

    def test_dollar_in_quoted(self):
        self.assertExecs('sed -i -e "foo$" *.pc', set(["sed"]))

    def test_quotes_inside_arg_continuation(self):
        self.assertReferences("""
        sed -i -e s#"moc_location=.*$"#"moc_location=${bindir}/moc4"# \\
               -e s#"uic_location=.*$"#"uic_location=${bindir}/uic4"# \\
               ${D}${libdir}/pkgconfig/*.pc
        """, set(["bindir", "D", "libdir"]))

    def test_assign_subshell_expansion(self):
        self.assertExecs("foo=$(echo bar)", set(["echo"]))

    def test_shell_unexpanded(self):
        shstr = 'echo "${QT_BASE_NAME}"'
        self.assertExecs(shstr, set(["echo"]))
        self.assertReferences(shstr, set(["QT_BASE_NAME"]))

    def test_incomplete_varexp_single_quotes(self):
        self.assertExecs("sed -i -e 's:IP{:I${:g' $pc", set(["sed"]))

    def test_until(self):
        shstr = "until false; do echo true; done"
        self.assertExecs(shstr, set(["false", "echo"]))
        self.assertReferences(shstr, set())

    def test_case(self):
        shstr = """
case $foo in
    *)
        bar
        ;;
esac
        """
        self.assertExecs(shstr, set(["bar"]))
        self.assertReferences(shstr, set())

    def test_assign_exec(self):
        self.assertExecs("a=b c='foo bar' alpha 1 2 3", set(["alpha"]))

    def test_redirect_to_file(self):
        shstr = "echo foo >${foo}/bar"
        self.assertExecs(shstr, set(["echo"]))
        self.assertReferences(shstr, set(["foo"]))

    def test_heredoc(self):
        shstr = """
        cat <<END
alpha
beta
${theta}
END
        """
        self.assertReferences(shstr, set(["theta"]))

    def test_redirect_from_heredoc(self):
        shstr = """
    cat <<END >${B}/cachedpaths
shadow_cv_maildir=${SHADOW_MAILDIR}
shadow_cv_mailfile=${SHADOW_MAILFILE}
shadow_cv_utmpdir=${SHADOW_UTMPDIR}
shadow_cv_logdir=${SHADOW_LOGDIR}
shadow_cv_passwd_dir=${bindir}
END
        """
        self.assertReferences(shstr, 
                          set(["B", "SHADOW_MAILDIR",
                               "SHADOW_MAILFILE", "SHADOW_UTMPDIR",
                               "SHADOW_LOGDIR", "bindir"]))
        self.assertExecs(shstr, set(["cat"]))

    def test_incomplete_command_expansion(self):
        self.assertRaises(reftracker.ShellSyntaxError, reftracker.execs,
                          bbvalue.shparse("cp foo`", self.d), self.d)

    def test_rogue_dollarsign(self):
        self.d.setVar("D", "/tmp")
        shstr = "install -d ${D}$"
        self.assertReferences(shstr, set(["D"]))
        self.assertExecs(shstr, set(["install"]))


class TestBasic(TestRefTracking):
    def assertReferences(self, value, refs):
        super(TestBasic, self).assertReferences(
            bbvalue.bbparse(value, self.d), refs)

    def test_simple_reference(self):
        self.assertReferences("${FOO}", set(["FOO"]))

    def test_nested_reference(self):
        self.d.setVar("FOO", "BAR")
        self.assertReferences("${${FOO}}", set(["FOO", "BAR"]))

    def test_python_reference(self):
        self.assertReferences("${@bb.data.getVar('BAR', d, True) + 'foo'}", set(["BAR"]))


class TestContentsTracking(TestRefTracking):
    def setUp(self):
        super(TestContentsTracking, self).setUp()

    pydata = """
        bb.data.getVar('somevar', d, True)
        def test(d):
            foo = 'bar %s' % 'foo'
            def test2(d):
                d.getVar(foo, True)
            d.getVar('bar', False)
            test2(d)

        def a():
            \"\"\"some
    stuff
            \"\"\"
            return "heh"

        test(d)

        bb.data.expand(bb.data.getVar("something", False, d), d)
        bb.data.expand("${inexpand} somethingelse", d)
        bb.data.getVar(a(), d, False)
"""

    def test_python(self):
        self.d.setVar("FOO", self.pydata)
        self.d.setVarFlags("FOO", {"func": True, "python": True})

        value = bbvalue.bbvalue("FOO", self.d)
        self.assertEquals(reftracker.references(value, self.d),
                          set(["somevar", "bar", "something", "inexpand"]))
        self.assertEquals(reftracker.calls(value, self.d), 
                          set(["test", "test2", "a"]))

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

        shellval = bbvalue.shparse(self.shelldata, self.d)
        self.assertEquals(reftracker.references(shellval, self.d), 
                          set(["somevar", "inverted"]))
        self.assertEquals(reftracker.execs(shellval, self.d), 
                          set(["bar", "echo", "heh", "moo",
                               "true", "false", "test", "aiee",
                               "inverted"]))

    def test_varrefs(self):
        self.d.setVar("oe_libinstall", "echo test")
        self.d.setVar("FOO", "foo=oe_libinstall; eval $foo")
        self.d.setVarFlag("FOO", "varrefs", "oe_libinstall")
        self.assertEqual(reftracker.referencesFromName("FOO", self.d),
                         set(["oe_libinstall"]))

    def test_varrefs_expand(self):
        self.d.setVar("oe_libinstall", "echo test")
        self.d.setVar("FOO", "foo=oe_libinstall; eval $foo")
        self.d.setVarFlag("FOO", "varrefs", "${@'oe_libinstall'}")
        self.assertEqual(reftracker.referencesFromName("FOO", self.d),
                         set(["oe_libinstall"]))

    def test_varrefs_wildcards(self):
        self.d.setVar("oe_libinstall", "echo test")
        self.d.setVar("FOO", "foo=oe_libinstall; eval $foo")
        self.d.setVarFlag("FOO", "varrefs", "oe_*")
        self.assertEqual(reftracker.referencesFromName("FOO", self.d),
                         set(["oe_libinstall"]))

class TestPython(TestRefTracking):
    def setUp(self):
        super(TestPython, self).setUp()
        if hasattr(bb.utils, "_context"):
            self.context = bb.utils._context
        else:
            import __builtin__
            self.context = __builtin__.__dict__
  
    def assertReferences(self, value, refs):
        super(TestPython, self).assertReferences(
            bbvalue.pyparse(value, self.d), refs)

    def assertExecs(self, value, execs):
        super(TestPython, self).assertExecs(
            bbvalue.pyparse(value, self.d), execs)

    def assertCalls(self, value, calls):
        super(TestPython, self).assertCalls(
            bbvalue.pyparse(value, self.d), calls)

    def assertFunctionReferences(self, value, refs):
        super(TestPython, self).assertFunctionReferences(
            bbvalue.pyparse(value, self.d), refs)

    def test_getvar_reference(self):
        pystr = "bb.data.getVar('foo', d, True)"
        self.assertReferences(pystr, set(["foo"]))
        self.assertCalls(pystr, set())

    def test_getvar_computed_reference(self):
        pystr = "bb.data.getVar('f' + 'o' + 'o', d, True)"
        self.assertReferences(pystr, set())
        self.assertCalls(pystr, set())

    def test_getvar_exec_reference(self):
        pystr = "eval('bb.data.getVar(\"foo\", d, True)')"
        self.assertReferences(pystr, set())
        self.assertCalls(pystr, set(["eval"]))

    def test_var_reference(self):
        self.context["foo"] = lambda x: x
        pystr = "foo('${FOO}')"
        self.assertReferences(pystr, set(["FOO"]))
        self.assertCalls(pystr, set(["foo"]))
        del self.context["foo"]

    def test_var_exec(self):
        for etype in ("func", "task"):
            self.d.setVar("do_something", "echo 'hi mom! ${FOO}'")
            self.d.setVarFlag("do_something", etype, True)
            pystr = "bb.build.exec_func('do_something', d)"
            self.assertReferences(pystr, set(["do_something"]))

    def test_function_reference(self):
        self.context["testfunc"] = lambda msg: bb.msg.note(1, None, msg)
        self.d.setVar("FOO", "Hello, World!")
        pystr = "testfunc('${FOO}')"
        self.assertReferences(pystr, set(["FOO"]))
        self.assertFunctionReferences(pystr, 
            set([("testfunc", self.context["testfunc"])]))
        del self.context["testfunc"]

    def test_qualified_function_reference(self):
        pystr = "time.time()"
        self.assertFunctionReferences(pystr, 
            set([("time.time", self.context["time"].time)]))

    def test_qualified_function_reference_2(self):
        pystr = "os.path.dirname('/foo/bar')"
        self.assertFunctionReferences(pystr,
            set([("os.path.dirname", self.context["os"].path.dirname)]))

    def test_qualified_function_reference_nested(self):
        pystr = "time.strftime('%Y%m%d',time.gmtime())"
        self.assertFunctionReferences(pystr, 
            set([("time.strftime", self.context["time"].strftime), 
                 ("time.gmtime", self.context["time"].gmtime)]))

    def test_function_reference_chained(self):
        self.context["testget"] = lambda: "\tstrip me     "
        pystr = "testget().strip()"
        self.assertFunctionReferences(pystr, 
            set([("testget", self.context["testget"])]))
        del self.context["testget"]


if __name__ == "__main__":
    unittest.main()
