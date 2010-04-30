"""Staging area for OE python bits for kergoth"""

# TODO:
#   - In the overridden references methods, use a uniq() utility function or a
#     set to drop duplicates between the superclass references and the extra
#     references gathered by the class.
#   - Sanitize the property names amongst the Value implementations
#   - Should the 'references' method become a property?
#   - Rename 'references', as it is specifically references to variables in
#     the metadata.  This isn't the only type of reference we have anymore, as
#     we'll also be tracking calls to the methods in the methodpool.
#   - Fix the PythonSnippet implementation to actually be a PythonValue
#     subclass, as it needs 1) regular var ref tracking, 2) python value
#     checking, and 3) execution of the python code at str() time.  1) and 2)
#     are already done by PythonValue.
#
#   Python:
#   - Move the direct function call list from the visitor into the main object
#     after parsing, so the caller doesn't need to poke into the visitor
#     directly.
#   - Think about checking imports to exclude more direct func calls
#   - Capture FunctionDef's to exclude them from the direct func calls list
#     - NOTE: This will be inaccurate, since it won't be accounting for
#             contexts initially.
#
#   NOTE: We should be able to utilize this way of expanding variables to let
#   us change things like 'FOO += "bar"' internally into the creation of a new
#   Value whose components are [FOO_old_Value_object, separator, "bar"].  This
#   will be a start to allowing us to retain information about what variables
#   were defined where through the parsing and expansion processes.

import re
import codegen
import ast
from itertools import chain
from collections import deque
from pysh import pyshyacc, pyshlex
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
        return "%s(%s, %s)" % (self.__class__.__name__, repr(self.value),
                               repr(self.metadata))

    def __str__(self):
        return str(self.components)

    def references(self):
        """Return an iterable of the variables this Value references"""

        def search(value, want):
            """Search a tree of values which meet a condition"""

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
    """Represents a block of shell code, initialized from a string.  First
    parses the string into a components object to gather information about
    regular variable references, then parses the resulting expanded shell code
    to extract calls to other shell functions in the metadata.
    """

    def __init__(self, value, metadata):
        self.shell_funcs = set()
        self.shell_execs = set()
        self.shell_external_execs = set()
        Value.__init__(self, value, metadata)

    def parse(self):
        Value.parse(self)
        self.shell_external_execs = self.parse_shell(str(self.components))

    def parse_shell(self, value):
        """Parse the supplied shell code in a string, returning the external
        commands it executes.
        """

        tokens, _ = pyshyacc.parse(value, True, False)
        for token in tokens:
            self.process_tokens(token)
        cmds = set(cmd for cmd in self.shell_execs
                       if cmd not in self.shell_funcs)
        return cmds

    def process_tokens(self, tokens):
        """Process a supplied portion of the syntax tree as returned by
        pyshyacc.parse.
        """

        def function_definition(value):
            self.shell_funcs.add(value.name)
            return ([value.body], None)

        token_handlers = {
          "simple_command": lambda x: (None, x.words),
          "for_clause": lambda x: (x.cmds, x.items),
          "pipeline": lambda x: (x.commands, None),
          "if_clause": lambda x: (chain(x.if_cmds, x.else_cmds), None),
          "and_or": lambda x: ((x.left, x.right), None),
          "while_clause": lambda x: (chain(x.condition, x.cmds), None),
          "function_definition": function_definition,
          "brace_group": lambda x: (x.cmds, None),
          "subshell": lambda x: (x.cmds, None),
          "async": lambda x: ([x], None),
          "redirect_list": lambda x: ([x.cmd], None),
        }

        for token in tokens:
            name, value = token
            try:
                more_tokens, words = token_handlers[name](value)
            except KeyError:
                raise NotImplementedError("Unsupported token type " + name)

            if more_tokens:
                self.process_tokens(more_tokens)

            if words:
                self.process_words(words)

    def process_words(self, words):
        """Process a set of 'words' in pyshyacc parlance, which includes
        extraction of executed commands from $() blocks, as well as grabbing
        the command name argument.
        """

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
                    print("Warning: ignoring execution of %s "
                          "as it appears to be a shell variable expansion" %
                          word[1])
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


class PythonValue(Value):
    """Represents a block of python code, initialized from a string.  First
    determines the variables referenced via normal variable expansion, then
    traverses the python syntax tree to extract variables references accessed
    via the usual bitbake metadata APIs, as well as the external functions
    called (to track usage of functions in the methodpool).
    """

    class ValueVisitor(ast.NodeVisitor):
        """Visitor to traverse a python abstract syntax tree and obtain
        the variables referenced via bitbake metadata APIs, and the external
        functions called.
        """

        getvars = ("d.getVar", "bb.data.getVar", "data.getVar")
        expands = ("d.expand", "bb.data.expand", "data.expand")

        @classmethod
        def _compare_name(cls, strparts, node):
            """Given a sequence of strings representing a python name,
            where the last component is the actual Name and the prior
            elements are Attribute nodes, determine if the supplied node
            matches.
            """

            if not strparts:
                return True

            current, rest = strparts[0], strparts[1:]
            if isinstance(node, ast.Attribute):
                if current == node.attr:
                    return cls._compare_name(rest, node.value)
            elif isinstance(node, ast.Name):
                if current == node.id:
                    return True
            return False

        @classmethod
        def compare_name(cls, value, node):
            """Convenience function for the _compare_node method, which
            can accept a string (which is split by '.' for you), or an
            iterable of strings, in which case it checks to see if any of
            them match, similar to isinstance.
            """

            if isinstance(value, basestring):
                return cls._compare_name(tuple(reversed(value.split("."))),
                                         node)
            else:
                return any(cls.compare_name(item, node) for item in value)

        def __init__(self):
            self.var_references = set()
            self.direct_func_calls = set()
            ast.NodeVisitor.__init__(self)

        @classmethod
        def warn(cls, func, arg):
            """Warn about calls of bitbake APIs which pass a non-literal
            argument for the variable name, as we're not able to track such
            a reference.
            """

            print("Warning: in call to '%s', argument '%s' is not a literal" %
                  (codegen.to_source(func), codegen.to_source(arg)))

        def visit_Call(self, node):
            ast.NodeVisitor.generic_visit(self, node)
            if self.compare_name(self.getvars, node.func):
                if isinstance(node.args[0], ast.Str):
                    self.var_references.add(node.args[0].s)
                else:
                    self.warn(node.func, node.args[0])
            elif self.compare_name(self.expands, node.func):
                if isinstance(node.args[0], ast.Str):
                    value = Value(node.args[0].s, bb.data.init())
                    for var in value.references():
                        self.var_references.add(var)
                elif isinstance(node.args[0], ast.Call) and \
                     self.compare_name(self.getvars, node.args[0].func):
                    pass
                else:
                    self.warn(node.func, node.args[0])
            elif isinstance(node.func, ast.Name):
                self.direct_func_calls.add(node.func.id)

    def __init__(self, value, metadata):
        self.visitor = self.ValueVisitor()
        Value.__init__(self, value, metadata)

    def parse(self):
        Value.parse(self)
        value = str(self.components)
        code = compile(value, "<string>", "exec", ast.PyCF_ONLY_AST)
        self.visitor.visit(code)

    def references(self):
        refs = Value.references(self)
        for ref in refs:
            yield ref

        for ref in self.visitor.var_references:
            yield ref