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


val = oe.kergoth.Value("${foo}", d)
assert(str(val) == "value of foo"
val = oe.kergoth.Value("${${foo}}", d)
assert(str(val) == "value of 'value of foo'"
val = oe.kergoth.Value("${@5*12}", d)
assert(str(val) == "60")