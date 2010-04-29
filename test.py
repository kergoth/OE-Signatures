#!/usr/bin/env python
import sys
import os
import ast


basedir = os.path.dirname(sys.argv[0])
searchpath = [os.path.join(basedir, "bitbake", "lib"),
              os.path.join(basedir, "openembedded", "lib")]
sys.path[0:0] = searchpath

import bb.data
import oe.kergoth
from pysh import pyshyacc, pyshlex, interp


def process_words(words):
    for word in list(words):
        wtree = pyshlex.make_wordtree(word[1])
        for part in wtree:
            if not isinstance(part, list):
                continue

            if part[0] in ('`', '$('):
                command = pyshlex.wordtree_as_string(part[1:-1])
                cmds, script = pyshyacc.parse(command, True, False)
                for cmd in cmds:
                    process(cmd)
                if word[0] in ("cmd_name", "cmd_word"):
                    if word in words:
                        words.remove(word)

    for word in words:
        if word[0] in ("cmd_name", "cmd_word"):
            cmd = word[1]
            if cmd.startswith("$"):
                print("Warning: ignoring execution of %s as it appears to be a shell variable expansion" % word[1])
            elif cmd == "eval":
                command = " ".join(word for _, word in words[1:])
                cmds, script = pyshyacc.parse(command, True, False)
                for cmd in cmds:
                    process(cmd)
            else:
                cmdnames.add(cmd)

def process(tokens):
    for token in tokens:
        (name, value) = token
        if name == "simple_command":
            process_words(value.words)
        elif name == "for_clause":
            process_words(value.items)
            process(value.cmds)
        elif name == "pipeline":
            process(value.commands)
        elif name == "if_clause":
            process(value.if_cmds)
            process(value.else_cmds)
        elif name == "and_or":
            process((value.left, value.right))
        elif name == "while_clause":
            process(value.condition)
            process(value.cmds)
        elif name == "function_definition":
            funcdefs.add(value.name)
            process((value.body,))
        elif name == "brace_group":
            process(value.cmds)
        elif name == "subshell":
            process(value.cmds)
        elif name == "async":
            process((value,))
        elif name == "redirect_list":
            process((value.cmd,))
        else:
            raise NotImplementedError("Unsupported token type " + name)


def _compare_name(strparts, node):
    if not strparts:
        return True

    current, rest = strparts[0], strparts[1:]
    if isinstance(node, ast.Attribute):
        if current == node.attr:
            return _compare_name(rest, node.value)
    elif isinstance(node, ast.Name):
        if current == node.id:
            return True
    #else:
    #    return True
    return False

def compare_name(strval, node):
    return _compare_name(tuple(reversed(strval.split("."))), node)

pydata = """
bb.data.getVar('somevar', d, True)
def test():
    foo = 'bar %s' % 'foo'
    def test2():
        d.getVar(foo, True)
    d.getVar('bar', False)
    test2()

bb.data.expand(bb.data.getVar("something", False, d), d)
bb.data.expand("${inexpand} somethingelse", d)
bb.data.getVar(foo, d, False)
"""

def test_python():
    class Visit(ast.NodeVisitor):
        def __init__(self):
            self.var_references = set()
            self.direct_func_calls = set()

        def visit_Call(self, node):
            ast.NodeVisitor.generic_visit(self, node)
            if compare_name("d.getVar", node.func) or \
               compare_name("bb.data.getVar", node.func):
               if node.args and \
                  isinstance(node.args[0], ast.Str):
                self.var_references.add(node.args[0].s)
            elif isinstance(node.func, ast.Name):
                self.direct_func_calls.add(node.func.id)

    code = compile(pydata, "<string>", "exec", ast.PyCF_ONLY_AST)
    visitor = Visit()
    visitor.visit(code)
    assert(visitor.var_references == set(["bar", "somevar", "something"]))
    assert(visitor.direct_func_calls == set(["test2"]))


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
"""

if __name__ == "__main__":
    tokens, script = pyshyacc.parse(shelldata, True, False)
    cmdnames = set()
    funcdefs = set()
    for token in tokens:
        process(token)
    cmds = set(cmd for cmd in cmdnames if cmd not in funcdefs)
    assert(cmds == set(["bar", "echo", "heh", "moo", "test", "aiee", "true",
                        "false", "inverted"]))

    d = bb.data.init()
    d["foo"] = "value of foo"
    d["bar"] = "value of bar"
    d["value of foo"] = "value of 'value of foo'"

    val = oe.kergoth.Value("${foo}", d)
    assert(str(val) == "value of foo")
    assert(list(val.references()) == ["foo"])

    val = oe.kergoth.Value("${${foo}}", d)
    assert(str(val) == "value of 'value of foo'")
    assert(list(val.references()) == ["foo"])

    val = oe.kergoth.Value("${${foo}} ${bar}", d)
    assert(str(val) == "value of 'value of foo' value of bar")
    assert(list(val.references()) == ["foo", "bar"])

    val = oe.kergoth.Value("${@5*12}", d)
    assert(str(val) == "60")
    assert(not list(val.references()))

    val = oe.kergoth.Value("${@'boo ' + '${foo}'}", d)
    assert(str(val) == "boo value of foo")
    assert(list(val.references()) == ["foo"])

    test_python()