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
        self.assertEqual(val.references, set(["foo"]))

    def test_indirect_one_var(self):
        val = kergoth.Value("${${foo}}", self.d)
        self.assertEqual(str(val), "value of 'value of foo'")
        self.assertEqual(val.references, set(["foo"]))

    def test_indirect_and_another(self):
        val = kergoth.Value("${${foo}} ${bar}", self.d)
        self.assertEqual(str(val), "value of 'value of foo' value of bar")
        self.assertEqual(val.references, set(["foo", "bar"]))

    def test_python_snippet(self):
        val = kergoth.Value("${@5*12}", self.d)
        self.assertEqual(str(val), "60")
        self.assertFalse(val.references)

    def test_expand_in_python_snippet(self):
        val = kergoth.Value("${@'boo ' + '${foo}'}", self.d)
        self.assertEqual(str(val), "boo value of foo")
        self.assertEqual(val.references, set(["foo"]))

    def test_python_snippet_getvar(self):
        val = kergoth.Value("${@d.getVar('foo', True) + ' ${bar}'}", self.d)
        self.assertEqual(str(val), "value of foo value of bar")
        self.assertEqual(val.references, set(["foo", "bar"]))

    def test_python_snippet_syntax_error(self):
        self.d.setVar("FOO", "${@foo = 5}")
        val = kergoth.new_value("FOO", self.d)
        self.assertRaises(SyntaxError, val.resolve)

    def test_python_snippet_runtime_error(self):
        self.d.setVar("FOO", "${@int('test')}")
        val = kergoth.new_value("FOO", self.d)
        self.assertRaises(kergoth.PythonExpansionError, val.resolve)

    def test_python_snippet_error_path(self):
        self.d.setVar("FOO", "foo value ${BAR}")
        self.d.setVar("BAR", "bar value ${@int('test')}")
        val = kergoth.new_value("FOO", self.d)
        try:
            val.resolve()
        except kergoth.PythonExpansionError, exc:
            self.assertEqual(len(exc.path), 2)
        else:
            raise Exception("Did not raise expected PythonExpansionError")

    def test_value_containing_value(self):
        otherval = kergoth.Value("${@d.getVar('foo', True) + ' ${bar}'}", self.d)
        val = kergoth.Value(kergoth.Components([otherval, " test"]), self.d)
        self.assertEqual(str(val), "value of foo value of bar test")
        self.assertEqual(val.references, set(["foo", "bar"]))

    def test_reference_undefined_var(self):
        val = kergoth.Value("${undefinedvar} meh", self.d)
        self.assertEqual(str(val), "${undefinedvar} meh")
        self.assertEqual(val.references, set(["undefinedvar"]))

    def test_double_reference(self):
        self.d.setVar("BAR", "bar value")
        self.d.setVar("FOO", "${BAR} foo ${BAR}")
        val = kergoth.new_value("FOO", self.d)
        val.resolve()

    def test_direct_recursion(self):
        self.d.setVar("FOO", "${FOO}")
        value = kergoth.new_value("FOO", self.d)
        self.assertRaises(kergoth.RecursionError, str, value)

    def test_indirect_recursion(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${BAZ}")
        self.d.setVar("BAZ", "${FOO}")
        value = kergoth.new_value("FOO", self.d)
        self.assertRaises(kergoth.RecursionError, str, value)

    def test_recursion_exception(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${${@'FOO'}}")
        value = kergoth.new_value("FOO", self.d)
        try:
            str(value)
        except kergoth.RecursionError, exc:
            self.assertEqual(exc.variable, "FOO")
            self.assertTrue(kergoth.new_value("BAR", self.d) in exc.path)
        else:
            raise Exception("RecursionError not raised")


def test_memoize():
    d = bb.data.init()
    d.setVar("FOO", "bar")
    assert(kergoth.new_value("FOO", d) is kergoth.new_value("FOO", d))

class TestShell(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()

    def test_quotes_inside_assign(self):
        value = kergoth.ShellValue('foo=foo"bar"baz', self.d)

    def test_quotes_inside_arg(self):
        value = kergoth.ShellValue('sed s#"bar baz"#"alpha beta"#g', self.d)
        self.assertEqual(value.execs, set(["sed"]))

    def test_arg_continuation(self):
        value = kergoth.ShellValue("sed -i -e s,foo,bar,g \\\n *.pc", self.d)
        self.assertEqual(value.execs, set(["sed"]))

    def test_dollar_in_quoted(self):
        value = kergoth.ShellValue('sed -i -e "foo$" *.pc', self.d)
        self.assertEqual(value.execs, set(["sed"]))

    def test_quotes_inside_arg_continuation(self):
        value = kergoth.ShellValue("""
        sed -i -e s#"moc_location=.*$"#"moc_location=${bindir}/moc4"# \\
               -e s#"uic_location=.*$"#"uic_location=${bindir}/uic4"# \\
               ${D}${libdir}/pkgconfig/*.pc
        """, self.d)
        self.assertEqual(value.references, set(["bindir", "D", "libdir"]))

    def test_subshell_expansion(self):
        value = kergoth.ShellValue("foo=$(echo bar)", self.d)
        self.assertEqual(value.execs, set(["echo"]))

    def test_shell_unexpanded(self):
        value = kergoth.ShellValue('echo "${QT_BASE_NAME}"', self.d)
        self.assertEqual(value.execs, set(["echo"]))
        self.assertEqual(value.references, set(["QT_BASE_NAME"]))

    def test_incomplete_varexp_single_quotes(self):
        value = kergoth.ShellValue("sed -i -e 's:IP{:I${:g' $pc", self.d)
        self.assertEqual(value.execs, set(["sed"]))

    def test_until(self):
        shellval = kergoth.ShellValue("until false; do echo true; done", self.d)
        self.assertEquals(shellval.execs, set(["false", "echo"]))
        self.assertEquals(shellval.references, set())

    def test_case(self):
        script = """
case $foo in
    *)
        bar
        ;;
esac
        """
        shellval = kergoth.ShellValue(script, self.d)
        self.assertEquals(shellval.execs, set(["bar"]))
        self.assertEquals(shellval.references, set())

    def test_assign_exec(self):
        value = kergoth.ShellValue("a=b c='foo bar' alpha 1 2 3", self.d)
        self.assertEquals(value.execs, set(["alpha"]))

    def test_assign_newlines_exec(self):
        value = kergoth.ShellValue("a=b\nc='foo bar'\n alpha 1 2 3", self.d)
        self.assertEquals(value.execs, set(["alpha"]))

    def test_redirect_to_file(self):
        value = kergoth.ShellValue("echo foo >${foo}/bar", self.d)
        self.assertEquals(value.execs, set(["echo"]))
        self.assertEquals(value.references, set(["foo"]))

    def test_heredoc(self):
        script = """
        cat <<END
alpha
beta
theta
END
        """
        value = kergoth.ShellValue(script, self.d)

    def test_redirect_from_heredoc(self):
        script = """
    cat <<END >${B}/cachedpaths
shadow_cv_maildir=${SHADOW_MAILDIR}
shadow_cv_mailfile=${SHADOW_MAILFILE}
shadow_cv_utmpdir=${SHADOW_UTMPDIR}
shadow_cv_logdir=${SHADOW_LOGDIR}
shadow_cv_passwd_dir=${bindir}
END
        """
        value = kergoth.ShellValue(script, self.d)
        self.assertEquals(value.references, set(["B", "SHADOW_MAILDIR",
                                                 "SHADOW_MAILFILE", "SHADOW_UTMPDIR",
                                                 "SHADOW_LOGDIR", "bindir"]))
        self.assertEquals(value.execs, set(["cat"]))

    def test_incomplete_command_expansion(self):
        from pysh import pyshlex
        self.assertRaises(pyshlex.NeedMore, kergoth.ShellValue, "cp foo`", self.d)

    def test_rogue_dollarsign(self):
        self.d.setVar("D", "/tmp")
        value = kergoth.ShellValue("install -d ${D}$", self.d)
        self.assertEqual(value.references, set(["D"]))
        self.assertEqual(value.execs, set(["install"]))

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
        self.assertEquals(value.references, set(["somevar", "bar", "something", "inexpand"]))
        self.assertEquals(value.calls, set(["test2", "a"]))

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
        self.assertEquals(shellval.references, set(["somevar", "inverted"]))
        self.assertEquals(shellval.execs, set(["bar", "echo", "heh", "moo",
                                               "true", "false", "test", "aiee",
                                               "inverted"]))

    def test_varrefs(self):
        self.d.setVar("FOO", "foo=oe_libinstall; eval $foo")
        self.d.setVarFlag("FOO", "varrefs", "oe_libinstall")
        value = kergoth.new_value("FOO", self.d)
        self.assertEqual(set(["oe_libinstall"]), value.references)

    def test_varrefs_expand(self):
        self.d.setVar("FOO", "foo=oe_libinstall; eval $foo")
        self.d.setVarFlag("FOO", "varrefs", "${@'oe_libinstall'}")
        value = kergoth.new_value("FOO", self.d)
        self.assertEqual(set(["oe_libinstall"]), value.references)

class TestPython(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()

    def test_getvar_reference(self):
        value = kergoth.PythonValue("bb.data.getVar('foo', d, True)", self.d)
        self.assertEqual(value.references, set(["foo"]))
        self.assertEqual(value.calls, set())

    def test_var_reference(self):
        value = kergoth.PythonValue("print('${FOO}')", self.d)
        self.assertEqual(value.references, set(["FOO"]))
        self.assertEqual(value.calls, set(["print"]))

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

    def test_signature_oe_devshell(self):
        self.d.setVar("do_devshell", "devshell_do_devshell")
        self.d.setVarFlag("do_devshell", "func", True)
        devshell = """
                export TERMWINDOWTITLE="Bitbake Developer Shell"
                ${TERMCMD}
                if [ $? -ne 0 ]; then
                    echo "Fatal: '${TERMCMD}' not found. Check TERMCMD variable."
                    exit 1
                fi
        """
        self.d.setVar("devshell_do_devshell", devshell)
        self.d.setVarFlag("devshell_do_devshell", "func", True)
        self.d.setVar("GNOME_TERMCMD", "gnome-terminal --disable-factory -t \"$TERMWINDOWTITLE\"")
        self.d.setVar("TERMCMD", "${GNOME_TERMCMD}")
        signature = kergoth.Signature(self.d, keys=["do_devshell"])
        signature.data_string


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