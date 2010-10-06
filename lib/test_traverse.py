import os.path
import sys
import unittest

basedir = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
sys.path[0:0] = [os.path.join(basedir, "lib")]

import bb.data
import bbvalue
import traverse

class TestTraversal(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()

    def assertExpanded(self, variable, desired):
        self.assertEqual(traverse.expand(variable, self.d), desired)

    def test_metadata_as_locals(self):
        self.d.setVar("foo", "bar")
        self.d.setVar("bar", "${@foo + '/baz'}")

        self.assertExpanded("bar", "bar/baz")

    def test_globals_accessible_in_snippet(self):
        self.d.setVar("foo", "${@os.path.join('foo', 'baz')}")

        self.assertExpanded("foo", "foo/baz")

    def test_metadata_accessible_in_snippet(self):
        self.d.setVar("foo", "bar")
        self.d.setVar("bar", "${@d.getVar('foo', True) + '/baz'}")

        self.assertExpanded("bar", "bar/baz")

    def test_direct_recursion(self):
        self.d.setVar("FOO", "${FOO}")
        self.assertRaises(traverse.RecursionError, traverse.expand, "FOO", self.d)

    def test_indirect_recursion(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${BAZ}")
        self.d.setVar("BAZ", "${FOO}")
        self.assertRaises(traverse.RecursionError, traverse.expand, "FOO", self.d)

    def test_recursion_exception(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${${@'FOO'}}")
        self.assertRaises(traverse.RecursionError, traverse.expand, "FOO", self.d)
