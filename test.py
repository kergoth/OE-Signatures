#!/usr/bin/env python
import sys
import os

basedir = os.path.dirname(sys.argv[0])
searchpath = [os.path.join(basedir, "bitbake", "lib"),
              os.path.join(basedir, "openembedded", "lib")]
sys.path[0:0] = searchpath

import bb.data
import oe.kergoth

d = bb.data.init()
d["foo"] = "value of foo"
d["bar"] = "value of bar"
d["value of foo"] = "value of 'value of foo'"

def direct_variable_refs(value):
    def search(value, want):
        for item in value.components:
            if want(item):
                yield item

            if hasattr(item, "components"):
                for otheritem in search(item, want):
                    yield otheritem

    for ref in search(value, lambda x: isinstance(x, oe.kergoth.VariableRef)):
        if all(isinstance(x, basestring) for x in ref.components):
            yield str(ref.components)

val = oe.kergoth.Value("${foo}", d)
assert(str(val) == "value of foo")
assert(list(direct_variable_refs(val)) == ["foo"])

val = oe.kergoth.Value("${${foo}}", d)
assert(str(val) == "value of 'value of foo'")
assert(list(direct_variable_refs(val)) == ["foo"])

val = oe.kergoth.Value("${${foo}} ${bar}", d)
assert(str(val) == "value of 'value of foo' value of bar")
assert(list(direct_variable_refs(val)) == ["foo", "bar"])

val = oe.kergoth.Value("${@5*12}", d)
assert(str(val) == "60")
assert(not list(direct_variable_refs(val)))