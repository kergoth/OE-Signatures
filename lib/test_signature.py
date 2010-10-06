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

    def assertSignatureMatches(self, signature, values=None, **kwargs):
        if values:
            kwargs.update(values)

        self.assertEqual(signature.data, kwargs)

    def assertSetEquals(self, iterable, iterable2):
        self.assertEquals(set(iterable), set(iterable2))

    def setVars(self, dictvalues=None, **values):
        if dictvalues:
            values.update(dictvalues)

        for key, value in values.iteritems():
            self.d.setVar(key, value)

    def setVarFlags(self, variable, values=None, **flags):
        if values:
            flags.update(values)

        for key, value in flags.iteritems():
            self.d.setVarFlag(variable, key, value)

    def test_full_signature(self):
        variables = dict(
            alpha='echo ${TOPDIR}/foo "$@"',
            beta='test -f bar',
            theta='alpha baz',
        )

        self.setVars(variables)
        self.setVarFlags("alpha", task=True)
        self.setVarFlags("beta", task=True)
        self.setVarFlags("theta", task=True)

        sig = signature.Signature(self.d)
        self.assertSignatureMatches(sig, variables)

    def test_signature_blacklisted(self):
        variables = dict(
            blacklisted='value',
            testbl='${@5} foo ${blacklisted} bar',
        )

        self.setVars(variables)

        sig = signature.Signature(self.d, keys=["testbl"])
        self.assertSignatureMatches(sig, testbl='5 foo ${blacklisted} bar')

    def test_signature_only_blacklisted(self):
        variables = dict(anotherval='${blacklisted}')
        self.setVars(variables)

        sig = signature.Signature(self.d, keys=["anotherval"])
        self.assertSignatureMatches(sig, variables)

    def test_signature_undefined(self):
        variables = dict(someval='${undefinedvar} ${blacklisted}')
        self.setVars(variables)

        sig = signature.Signature(self.d, keys=["someval"])
        self.assertSignatureMatches(sig, variables)

    def test_signature_python_snippet(self):
        context = {}
        self.d.setVar("testvar", "${@x()}")
        bb.utils.simple_exec("globals()['x'] = lambda: 'alpha'", context)
        sig = signature.Signature(self.d, keys=["testvar"])
        self.assertTrue(sig.data)

        bb.utils.simple_exec("globals()['x'] = lambda: 'beta'", context)
        sig2 = signature.Signature(self.d, keys=["testvar"])
        self.assertTrue(sig2.data)
        self.assertNotEqual(sig.data, sig2.data)

    def test_signature_python_snippet_vars_as_locals(self):
        variables = dict(
            foo='bar',
            bar='baz',
            test='${@foo + "/baz"}',
        )
        self.setVars(variables)

        sig = signature.Signature(self.d, keys=["test"])
        self.assertSignatureMatches(sig, foo='bar', test='bar/baz')

    def test_reference_to_reference(self):
        variables = dict(
            FOO='-${BAR}-',
            BAR='+${BAZ}+',
            BAZ='alpha',
        )
        self.setVars(variables)

        sig = signature.Signature(self.d, keys=["FOO"])
        self.assertSignatureMatches(sig, variables)

    def test_reference_to_reference_shell(self):
        variables = dict(
            alpha='echo; beta',
            beta='theta; echo',
            theta='echo foo',
        )
        self.setVars(variables)
        self.setVarFlags("alpha", func=True)
        self.setVarFlags("beta", func=True)
        self.setVarFlags("theta", func=True)

        sig = signature.Signature(self.d, keys=["alpha"])
        self.assertSignatureMatches(sig, variables)

    def test_varrefs(self):
        variables = dict(
            alpha='${@bb.data.getVar("foo" + "5", d, True)}',
            foo5='test',
        )
        self.setVars(variables)
        self.setVarFlags("alpha", varrefs="foo5")

        sig = signature.Signature(self.d, keys=["alpha"])
        self.assertSignatureMatches(sig, alpha='test', foo5='test')


if __name__ == "__main__":
    unittest.main()
