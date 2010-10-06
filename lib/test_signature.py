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
import signature

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
        sig = signature.Signature(self.d)
        self.assertEquals(sig.data_string, "{'alpha': ShellSnippet([Compound([Literal('echo '), VariableRef([Literal('TOPDIR')]), Literal('/foo \"$@\"')])]), 'beta': ShellSnippet([Compound([Literal('test -f bar')])]), 'theta': ShellSnippet([Compound([Literal('alpha baz')])])}")

    def test_signature_blacklisted(self):
        self.d["blacklistedvar"] = "blacklistedvalue"
        self.d["testbl"] = "${@5} foo ${blacklistedvar} bar"
        sig = signature.Signature(self.d, keys=["testbl"])
        self.assertEqual(sig.data_string, "{'testbl': Compound([Literal('5'), Literal(' foo '), VariableRef([Literal('blacklistedvar')]), Literal(' bar')])}")

    def test_signature_only_blacklisted(self):
        self.d["anotherval"] = "${blacklistedvar}"
        sig = signature.Signature(self.d, keys=["anotherval"])
        self.assertEquals(sig.data_string, "{'anotherval': Compound([VariableRef([Literal('blacklistedvar')])])}")

    def test_signature_undefined(self):
        self.d["someval"] = "${undefinedvar} ${blacklistedvar} meh"
        sig = signature.Signature(self.d, keys=["someval"])
        self.assertEquals(sig.data_string, "{'someval': Compound([VariableRef([Literal('undefinedvar')]), Literal(' '), VariableRef([Literal('blacklistedvar')]), Literal(' meh')])}")

    def test_signature_python_snippet(self):
        locals = {}
        self.d.setVar("testvar", "${@x()}")
        bb.utils.simple_exec("globals()['x'] = lambda: 'alpha'", locals)
        sig = signature.Signature(self.d, keys=["testvar"])
        self.assertTrue(sig.data)
        bb.utils.simple_exec("globals()['x'] = lambda: 'beta'", locals)
        sig2 = signature.Signature(self.d, keys=["testvar"])
        self.assertTrue(sig2.data)
        self.assertNotEqual(sig.data, sig2.data)

    def test_signature_python_snippet_vars_as_locals(self):
        self.d.setVar("foo", "bar")
        self.d.setVar("bar", "baz")
        self.d.setVar("test", "${@foo + '/baz'}")
        sig = signature.Signature(self.d, keys=["test"])
        self.assertEqual(sig.data, dict(foo="bar", test="bar/baz"))

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
        sig = signature.Signature(self.d, keys=["do_devshell"])
        self.assertEquals(sig.md5.digest(),
                          'x\xa3\xa6-\x11H:#\x1c\xe9\x82\xc2q\x19\xab\x9f')

    def test_reference_to_reference(self):
        self.d.setVar("FOO", "-${BAR}-")
        self.d.setVar("BAR", "+${BAZ}+")
        self.d.setVar("BAZ", "alpha")
        sig = signature.Signature(self.d, keys=["FOO"])
        self.assertEquals(set(sig.data.keys()), set(["FOO", "BAR", "BAZ"]))

    def test_reference_to_reference_shell(self):
        self.d.setVar("alpha", "echo; beta")
        self.d.setVarFlag("alpha", "func", True)
        self.d.setVar("beta", "theta; echo")
        self.d.setVarFlag("beta", "func", True)
        self.d.setVar("theta", "echo foo")
        self.d.setVarFlag("theta", "func", True)
        sig = signature.Signature(self.d, keys=["alpha"])
        self.assertEquals(set(sig.data.keys()), set(["alpha", "beta", "theta"]))

    def test_varrefs(self):
        self.d.setVar("alpha", "${@bb.data.getVar('foo' + '5', d, True)}")
        self.d.setVarFlag("alpha", "varrefs", "foo5")
        self.d.setVar("foo5", "test")
        sig = signature.Signature(self.d, keys=["alpha"])
        self.assertEquals(set(sig.data), set(["alpha", "foo5"]))


if __name__ == "__main__":
    unittest.main()
