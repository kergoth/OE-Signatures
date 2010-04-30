"""Staging area for OE python bits for kergoth"""

import re
import shlex
from pysh import pyshyacc, pyshlex
from StringIO import StringIO
from collections import deque
import bb.msg
import bb.utils

class Components(list):
    """A list of components, which concatenates itself upon str(), and runs
    str() on each component.  A given component is defined as being a
    string, python snippet, or variable reference"""

    def __str__(self):
        return "".join(str(v) for v in self)


class VariableRef(object):
    """Reference to a variable.  The variable name is supplied as a Components
    object, as we allow nested variable references, so the inside of a
    reference can be any number of components"""

    def __init__(self, components, metadata):
        self.components = components
        self.metadata = metadata

    def __repr__(self):
        return "VariableRef(%s, %s)" % (repr(self.components),
                                        repr(self.metadata))

    def __str__(self):
        name = str(self.components)
        variables = self.metadata.getVar("__variables", False)
        if variables and name in variables:
            var = variables[name]
        else:
            value = self.metadata.getVar(name, False)
            if value is None:
                return "${%s}" % name

            var = Value(value, self.metadata)
        return str(var)


class PythonSnippet(object):
    """Lazy evaluation of a python snippet in the form of a Components object"""

    def __init__(self, components, metadata):
        self.components = components
        self.metadata = metadata

    def __str__(self):
        code = str(self.components)
        codeobj = compile(code.strip(), "<expansion>", "eval")
        try:
            value = str(bb.utils.better_eval(codeobj, {"d": self.metadata}))
        except Exception, exc:
            bb.msg.note(1, bb.msg.domain.Data,
                        "%s:%s while evaluating:\n%s" % (type(exc), exc,
                                                         code))
            return "<invalid>"
        return str(Value(value, self.metadata))


class Value(object):
    """Parse a value from the OE metadata into a Components object, held
    internally.  Running str() on this is equivalent to doing the same to its
    internal Components."""

    var_re = re.compile(r"(\$\{|\})")

    def __init__(self, value, metadata):
        self.value = value
        self.metadata = metadata
        self.components = Components()
        self.parse()

    def __repr__(self):
        return "%s(%s, %s)" % (self.__class__.__name__, repr(self.value), repr(self.metadata))

    def __str__(self):
        return str(self.components)

    def references(self):
        def search(value, want):
            for item in value.components:
                if want(item):
                    yield item

                if hasattr(item, "components"):
                    for otheritem in search(item, want):
                        yield otheritem

        for ref in search(self, lambda x: isinstance(x, VariableRef)):
            if all(isinstance(x, basestring) for x in ref.components):
                yield str(ref.components)

    def parse(self):
        """Parse a value from the OE metadata into a Components object"""

        if not isinstance(self.value, basestring) or \
           "${" not in self.value:
            self.components.append(self.value)
            return

        tokens = (var for var in self.var_re.split(self.value) if var)
        result = Components()
        current = None
        stack = deque()
        for token in tokens:
            if token == "${":
                stack.append(current)
                current = Components()
            elif current is not None:
                if token == "}":
                    if hasattr(current[0], "startswith") and \
                       current[0].startswith("@"):
                        current[0] = current[0][1:]
                        value = PythonSnippet(current, self.metadata)
                    else:
                        value = VariableRef(current, self.metadata)

                    current = stack.pop()
                    if current is None:
                        result.append(value)
                    else:
                        current.append(value)
                else:
                    current.append(token)
            else:
                result.append(token)
        self.components = result

class ShellValue(Value):
    def __init__(self, value, metadata):
        self.shell_funcs = set()
        self.shell_execs = set()
        self.shell_external_execs = set()
        Value.__init__(self, value, metadata)

    def parse(self):
        Value.parse(self)
        self.shell_external_execs = self.parse_shell(str(self.components))

    def parse_shell(self, value):
        tokens, script = pyshyacc.parse(value, True, False)
        for token in tokens:
            self.process_tokens(token)
        cmds = set(cmd for cmd in self.shell_execs if cmd not in self.shell_funcs)
        return cmds

    def process_tokens(self, tokens):
        for token in tokens:
            (name, value) = token
            if name == "simple_command":
                self.process_words(value.words)
            elif name == "for_clause":
                self.process_words(value.items)
                self.process_tokens(value.cmds)
            elif name == "pipeline":
                self.process_tokens(value.commands)
            elif name == "if_clause":
                self.process_tokens(value.if_cmds)
                self.process_tokens(value.else_cmds)
            elif name == "and_or":
                self.process_tokens((value.left, value.right))
            elif name == "while_clause":
                self.process_tokens(value.condition)
                self.process_tokens(value.cmds)
            elif name == "function_definition":
                self.shell_funcs.add(value.name)
                self.process_tokens((value.body,))
            elif name == "brace_group":
                self.process_tokens(value.cmds)
            elif name == "subshell":
                self.process_tokens(value.cmds)
            elif name == "async":
                self.process_tokens((value,))
            elif name == "redirect_list":
                self.process_tokens((value.cmd,))
            else:
                raise NotImplementedError("Unsupported token type " + name)

    def process_words(self, words):
        for word in list(words):
            wtree = pyshlex.make_wordtree(word[1])
            for part in wtree:
                if not isinstance(part, list):
                    continue

                if part[0] in ('`', '$('):
                    command = pyshlex.wordtree_as_string(part[1:-1])
                    self.parse_shell(command)

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
                    self.parse_shell(command)
                else:
                    self.shell_execs.add(cmd)

    def references(self):
        refs = Value.references(self)
        for ref in refs:
            yield ref

        for func in self.shell_external_execs:
            if self.metadata.getVar(func, False) is not None:
                yield func