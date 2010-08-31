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

class TestSimpleExpansions(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()
        self.d["foo"] = "value of foo"
        self.d["bar"] = "value of bar"
        self.d["value of foo"] = "value of 'value of foo'"

    def test_one_var(self):
        val = bbvalue.bbparse("${foo}")
        self.assertEqual(bbvalue.resolve(val, self.d), "value of foo")

    def test_indirect_one_var(self):
        val = bbvalue.bbparse("${${foo}}")
        self.assertEqual(bbvalue.resolve(val, self.d), "value of 'value of foo'")

    def test_indirect_and_another(self):
        val = bbvalue.bbparse("${${foo}} ${bar}")
        self.assertEqual(bbvalue.resolve(val, self.d), "value of 'value of foo' value of bar")

    def test_python_snippet(self):
        val = bbvalue.bbparse("${@5*12}")
        self.assertEqual(bbvalue.resolve(val, self.d), "60")

    def test_expand_in_python_snippet(self):
        val = bbvalue.bbparse("${@'boo ' + '${foo}'}")
        self.assertEqual(bbvalue.resolve(val, self.d), "boo value of foo")

    def test_python_snippet_getvar(self):
        val = bbvalue.bbparse("${@d.getVar('foo', True) + ' ${bar}'}")
        self.assertEqual(bbvalue.resolve(val, self.d), "value of foo value of bar")

    def test_python_snippet_syntax_error(self):
        self.d.setVar("FOO", "${@foo = 5}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(SyntaxError, bbvalue.resolve, val, self.d)

    def test_python_snippet_runtime_error(self):
        self.d.setVar("FOO", "${@int('test')}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(bbvalue.PythonExpansionError, bbvalue.resolve, val, self.d)

    def test_python_snippet_error_path(self):
        self.d.setVar("FOO", "foo value ${BAR}")
        self.d.setVar("BAR", "bar value ${@int('test')}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(bbvalue.PythonExpansionError, bbvalue.resolve, val, self.d)

    def test_value_containing_value(self):
        val = bbvalue.bbparse("${@d.getVar('foo', True) + ' ${bar}'}")
        self.assertEqual(bbvalue.resolve(val, self.d), "value of foo value of bar")

    def test_reference_undefined_var(self):
        val = bbvalue.bbparse("${undefinedvar} meh")
        self.assertEqual(bbvalue.resolve(val, self.d), "${undefinedvar} meh")

    def test_double_reference(self):
        self.d.setVar("BAR", "bar value")
        self.d.setVar("FOO", "${BAR} foo ${BAR}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertEqual(bbvalue.resolve(val, self.d), "bar value foo bar value")

    def test_direct_recursion(self):
        self.d.setVar("FOO", "${FOO}")
        value = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(bbvalue.RecursionError, str, value)

    def test_indirect_recursion(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${BAZ}")
        self.d.setVar("BAZ", "${FOO}")
        value = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(bbvalue.RecursionError, str, value)

    def test_recursion_exception(self):
        self.d.setVar("FOO", "${BAR}")
        self.d.setVar("BAR", "${${@'FOO'}}")
        value = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(bbvalue.RecursionError, str, value)

    def test_incomplete_varexp_single_quotes(self):
        val = bbvalue.shparse("sed -i -e 's:IP{:I${:g' $pc")
        self.assertEqual(bbvalue.resolve(val, self.d), "sed -i -e 's:IP{:I${:g' $pc")

    def test_nonstring(self):
        self.d.setVar("TEST", 5)
        val = bbvalue.bbvalue("TEST", self.d)
        self.assertEqual(bbvalue.resolve(val, self.d), "5")

class TestNestedExpansions(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()
        self.d["foo"] = "foo"
        self.d["bar"] = "bar"
        self.d["value of foobar"] = "187"

    def test_refs(self):
        val = bbvalue.bbparse("${value of ${foo}${bar}}")
        self.assertEqual(bbvalue.resolve(val, self.d), "187")

    def test_python_refs(self):
        val = bbvalue.bbparse("${@${@3}**2 + ${@4}**2}")
        self.assertEqual(bbvalue.resolve(val, self.d), "25")

    def test_ref_in_python_ref(self):
        val = bbvalue.bbparse("${@'${foo}' + 'bar'}")
        self.assertEqual(bbvalue.resolve(val, self.d), "foobar")

    def test_python_ref_in_ref(self):
        val = bbvalue.bbparse("${${@'f'+'o'+'o'}}")
        self.assertEqual(bbvalue.resolve(val, self.d), "foo")

    def test_deep_nesting(self):
        depth = 100
        val = bbvalue.bbparse("${" * depth + "foo" + "}" * depth)
        self.assertEqual(bbvalue.resolve(val, self.d), "foo")

    def test_deep_python_nesting(self):
        depth = 50
        val = bbvalue.bbparse("${@" * depth + "1" + "+1}" * depth)
        self.assertEqual(bbvalue.resolve(val, self.d), str(depth + 1))

    def test_mixed(self):
        val = bbvalue.bbparse("${value of ${@('${foo}'+'bar')[0:3]}${${@'BAR'.lower()}}}")
        self.assertEqual(bbvalue.resolve(val, self.d), "187")

    def test_runtime(self):
        val = bbvalue.bbparse("${${@'value of' + ' f'+'o'+'o'+'b'+'a'+'r'}}")
        self.assertEqual(bbvalue.resolve(val, self.d), "187")

class TestMemoize(unittest.TestCase):
    def test_memoized(self):
        d = bb.data.init()
        d.setVar("FOO", "bar")
        self.assertTrue(bbvalue.bbvalue("FOO", d) is
                        bbvalue.bbvalue("FOO", d))

    def test_changed_after_memoized(self):
        d = bb.data.init()
        d.setVar("foo", "value of foo")
        val = bbvalue.bbparse("${foo}")
        self.assertEqual(bbvalue.resolve(bbvalue.bbvalue("foo", d), d), "value of foo")
        d.setVar("foo", "second value of foo")
        self.assertEqual(bbvalue.resolve(bbvalue.bbvalue("foo", d), d), "second value of foo")

    def test_same_value(self):
        d = bb.data.init()
        d.setVar("foo", "value of")
        d.setVar("bar", "value of")
        self.assertEqual(bbvalue.bbvalue("foo", d),
                         bbvalue.bbvalue("bar", d))

class TestLazy(unittest.TestCase):
    def setUp(self):
        self.metadata = bb.data.init()
        self.metadata.setVar("FOO", "foo")
        self.metadata.setVar("VAL", "val")
        self.metadata.setVar("BAR", "bar")

    def test_prepend(self):
        value = bbvalue.LazyCompound()
        value.append(bbvalue.bbvalue("VAL", self.metadata))
        value.lazy_prepend(bbvalue.bbparse("${FOO}:"))
        self.assertEqual(bbvalue.resolve(value, self.metadata), "foo:val")

    def test_append(self):
        value = bbvalue.LazyCompound()
        value.append(bbvalue.bbvalue("VAL", self.metadata))
        value.lazy_append(bbvalue.bbparse(":${BAR}"))
        self.assertEqual(bbvalue.resolve(value, self.metadata), "val:bar")

    def test_normal_append(self):
        value = bbvalue.LazyCompound()
        value.append(bbvalue.bbvalue("VAL", self.metadata))
        value.lazy_prepend(bbvalue.bbparse("${FOO}:"))
        value.lazy_append(bbvalue.bbparse(":${BAR}"))
        value.append(bbvalue.bbparse(":val2"))
        self.assertEqual(bbvalue.resolve(value, self.metadata), "foo:val:val2:bar")

class TestConditional(unittest.TestCase):
    def setUp(self):
        self.metadata = bb.data.init()
        self.metadata.setVar("OVERRIDES", "foo:bar:local")
        self.metadata.setVar("TEST", "testvalue")

    def test_no_condition(self):
        value = bbvalue.Conditional(None,
                                    [bbvalue.bbvalue("TEST", self.metadata)])
        self.assertEqual(bbvalue.resolve(value, self.metadata), "testvalue")

    def test_true_condition(self):
        value = bbvalue.Conditional(
                                    lambda d: 'foo' in d.getVar("OVERRIDES", True).split(":"),
                                    [bbvalue.bbvalue("TEST", self.metadata)])
        self.assertEqual(bbvalue.resolve(value, self.metadata), "testvalue")

    def test_false_condition(self):
        value = bbvalue.Conditional(
                                    lambda d: 'foo' in d.getVar("OVERRIDES", True).split(":"),
                                    [bbvalue.bbvalue("TEST", self.metadata)])
        self.metadata.setVar("OVERRIDES", "bar:local")
        self.assertEqual(bbvalue.resolve(value, self.metadata), "")

from fnmatch import fnmatchcase

class TestTransformer(unittest.TestCase):
    def setUp(self):
        self.metadata = bb.data.init()
        self.blacklist = ["bl*"]
        self.blacklister = bbvalue.Blacklister(self.metadata, self.is_blacklisted)

    def is_blacklisted(self, name):
        return any(fnmatchcase(name, bl) for bl in self.blacklist)

    def test_only_blacklisted(self):
        self.metadata.setVar("blfoo", "bar")
        value = bbvalue.bbparse("${blfoo}")
        self.assertEqual(bbvalue.resolve(value, self.metadata), "bar")
        blacklisted = self.blacklister.visit(value)
        self.assertEqual(bbvalue.resolve(blacklisted, self.metadata), "${blfoo}")

    def test_nested_blacklisted(self):
        self.metadata.setVar("blfoo", "bar")
        self.metadata.setVar("bar", "baz")
        value = bbvalue.bbparse("${${blfoo}}")
        self.assertEqual(bbvalue.resolve(value, self.metadata), "baz")
        blacklisted = self.blacklister.visit(value)
        self.assertEqual(bbvalue.resolve(blacklisted, self.metadata), "${${blfoo}}")

    def test_resolver_unexpanded(self):
        self.metadata.setVar("BAR", "beta")
        self.metadata.setVar("FOO", "alpha ${BAR} theta")
        resolver = bbvalue.Resolver(self.metadata, False)
        resolved = resolver.visit(bbvalue.bbvalue("FOO", self.metadata))
        self.assertEqual(resolved, "alpha ${BAR} theta")

    def test_resolver(self):
        self.metadata.setVar("BAR", "beta")
        self.metadata.setVar("FOO", "alpha ${BAR} theta")
        resolver = bbvalue.Resolver(self.metadata, True)
        resolved = resolver.visit(bbvalue.bbvalue("FOO", self.metadata))
        self.assertEqual(resolved, "alpha beta theta")

    def test_resolver_nested(self):
        self.metadata.setVar("FOO", "BAR")
        self.metadata.setVar("BAR", "alpha")
        resolver = bbvalue.Resolver(self.metadata, True)
        resolved = resolver.visit(bbvalue.bbparse("${${FOO}}"))
        self.assertEqual(resolved, "alpha")

if __name__ == "__main__":
    unittest.main()

