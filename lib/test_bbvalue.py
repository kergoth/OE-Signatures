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
        val = bbvalue.bbparse("${foo}", self.d)
        self.assertEqual(str(val), "value of foo")

    def test_indirect_one_var(self):
        val = bbvalue.bbparse("${${foo}}", self.d)
        self.assertEqual(str(val), "value of 'value of foo'")

    def test_indirect_and_another(self):
        val = bbvalue.bbparse("${${foo}} ${bar}", self.d)
        self.assertEqual(str(val), "value of 'value of foo' value of bar")

    def test_python_snippet(self):
        val = bbvalue.bbparse("${@5*12}", self.d)
        self.assertEqual(str(val), "60")

    def test_expand_in_python_snippet(self):
        val = bbvalue.bbparse("${@'boo ' + '${foo}'}", self.d)
        self.assertEqual(str(val), "boo value of foo")

    def test_python_snippet_getvar(self):
        val = bbvalue.bbparse("${@d.getVar('foo', True) + ' ${bar}'}", self.d)
        self.assertEqual(str(val), "value of foo value of bar")

    def test_python_snippet_syntax_error(self):
        self.d.setVar("FOO", "${@foo = 5}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(SyntaxError, val.resolve)

    def test_python_snippet_runtime_error(self):
        self.d.setVar("FOO", "${@int('test')}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(bbvalue.PythonExpansionError, val.resolve)

    def test_python_snippet_error_path(self):
        self.d.setVar("FOO", "foo value ${BAR}")
        self.d.setVar("BAR", "bar value ${@int('test')}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertRaises(bbvalue.PythonExpansionError, val.resolve)

    def test_value_containing_value(self):
        val = bbvalue.bbparse("${@d.getVar('foo', True) + ' ${bar}'}", self.d)
        self.assertEqual(str(val), "value of foo value of bar")

    def test_reference_undefined_var(self):
        val = bbvalue.bbparse("${undefinedvar} meh", self.d)
        self.assertEqual(str(val), "${undefinedvar} meh")

    def test_double_reference(self):
        self.d.setVar("BAR", "bar value")
        self.d.setVar("FOO", "${BAR} foo ${BAR}")
        val = bbvalue.bbvalue("FOO", self.d)
        self.assertEqual(str(val), "bar value foo bar value")

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
        val = bbvalue.shparse("sed -i -e 's:IP{:I${:g' $pc", self.d)
        self.assertEqual(str(val), "sed -i -e 's:IP{:I${:g' $pc")

    def test_nonstring(self):
        self.d.setVar("TEST", 5)
        val = bbvalue.bbvalue("TEST", self.d)
        self.assertEqual(str(val), "5")

class TestNestedExpansions(unittest.TestCase):
    def setUp(self):
        self.d = bb.data.init()
        self.d["foo"] = "foo"
        self.d["bar"] = "bar"
        self.d["value of foobar"] = "187"

    def test_refs(self):
        val = bbvalue.bbparse("${value of ${foo}${bar}}", self.d)
        self.assertEqual(str(val), "187")

    def test_python_refs(self):
        val = bbvalue.bbparse("${@${@3}**2 + ${@4}**2}", self.d)
        self.assertEqual(str(val), "25")

    def test_ref_in_python_ref(self):
        val = bbvalue.bbparse("${@'${foo}' + 'bar'}", self.d)
        self.assertEqual(str(val), "foobar")

    def test_python_ref_in_ref(self):
        val = bbvalue.bbparse("${${@'f'+'o'+'o'}}", self.d)
        self.assertEqual(str(val), "foo")

    def test_deep_nesting(self):
        depth = 100
        val = bbvalue.bbparse("${" * depth + "foo" + "}" * depth, self.d)
        self.assertEqual(str(val), "foo")

    def test_deep_python_nesting(self):
        depth = 50
        val = bbvalue.bbparse("${@" * depth + "1" + "+1}" * depth, self.d)
        self.assertEqual(str(val), str(depth + 1))

    def test_mixed(self):
        val = bbvalue.bbparse("${value of ${@('${foo}'+'bar')[0:3]}${${@'BAR'.lower()}}}", self.d)
        self.assertEqual(str(val), "187")

    def test_runtime(self):
        val = bbvalue.bbparse("${${@'value of' + ' f'+'o'+'o'+'b'+'a'+'r'}}", 
                            self.d)
        self.assertEqual(str(val), "187")

class TestMemoize(unittest.TestCase):
    def test_memoized(self):
        d = bb.data.init()
        d.setVar("FOO", "bar")
        self.assertTrue(bbvalue.bbvalue("FOO", d) is
                        bbvalue.bbvalue("FOO", d))

    def test_not_memoized(self):
        d1 = bb.data.init()
        d2 = bb.data.init()
        d1.setVar("FOO", "bar")
        d2.setVar("FOO", "bar")
        self.assertTrue(bbvalue.bbvalue("FOO", d1) is not
                        bbvalue.bbvalue("FOO", d2))

    def test_changed_after_memoized(self):
        d = bb.data.init()
        d.setVar("foo", "value of foo")
        val = bbvalue.bbparse("${foo}", d)
        self.assertEqual(str(bbvalue.bbvalue("foo", d)), "value of foo")
        d.setVar("foo", "second value of foo")
        self.assertEqual(str(bbvalue.bbvalue("foo", d)), "second value of foo")

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
        value = bbvalue.LazyCompound(self.metadata)
        value.append(bbvalue.bbvalue("VAL", self.metadata))
        value.lazy_prepend(bbvalue.bbparse("${FOO}:", self.metadata))
        self.assertEqual(value.resolve(), "foo:val")

    def test_append(self):
        value = bbvalue.LazyCompound(self.metadata)
        value.append(bbvalue.bbvalue("VAL", self.metadata))
        value.lazy_append(bbvalue.bbparse(":${BAR}", self.metadata))
        self.assertEqual(value.resolve(), "val:bar")

    def test_normal_append(self):
        value = bbvalue.LazyCompound(self.metadata)
        value.append(bbvalue.bbvalue("VAL", self.metadata))
        value.lazy_prepend(bbvalue.bbparse("${FOO}:", self.metadata))
        value.lazy_append(bbvalue.bbparse(":${BAR}", self.metadata))
        value.append(bbvalue.bbparse(":val2", self.metadata))
        self.assertEqual(value.resolve(), "foo:val:val2:bar")

class TestConditional(unittest.TestCase):
    def setUp(self):
        self.metadata = bb.data.init()
        self.metadata.setVar("OVERRIDES", "foo:bar:local")
        self.metadata.setVar("TEST", "testvalue")

    def test_no_condition(self):
        value = bbvalue.Conditional(self.metadata, None,
                                    [bbvalue.bbvalue("TEST", self.metadata)])
        self.assertEqual(value.resolve(), "testvalue")

    def test_true_condition(self):
        value = bbvalue.Conditional(self.metadata,
                                    lambda d: 'foo' in d.getVar("OVERRIDES", True).split(":"),
                                    [bbvalue.bbvalue("TEST", self.metadata)])
        self.assertEqual(value.resolve(), "testvalue")

    def test_false_condition(self):
        value = bbvalue.Conditional(self.metadata,
                                    lambda d: 'foo' in d.getVar("OVERRIDES", True).split(":"),
                                    [bbvalue.bbvalue("TEST", self.metadata)])
        self.metadata.setVar("OVERRIDES", "bar:local")
        self.assertEqual(value.resolve(), "")

if __name__ == "__main__":
    unittest.main()

