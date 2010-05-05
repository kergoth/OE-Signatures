#!/usr/bin/env python2.6
"""Staging area for OE python bits for kergoth"""

import re
import codegen
import ast
import hashlib
from fnmatch import fnmatchcase
from itertools import chain
from collections import deque
from pysh import pyshyacc, pyshlex, sherrors
import bb.msg
import bb.utils


class Memoized(object):
    """Decorator that caches a function's return value each time it is called.
    If called later with the same arguments, the cached value is returned, and
    not re-evaluated.
    """
    def __init__(self, func):
        self.func = func
        self.cache = {}

    def __call__(self, *args):
        try:
            return self.cache[args]
        except KeyError:
            self.cache[args] = value = self.func(*args)
            return value
        except TypeError:
            # uncachable -- for instance, passing a list as an argument.
            # Better to not cache than to blow up entirely.
            return self.func(*args)

    def __repr__(self):
        """Return the function's docstring."""
        return self.func.__doc__


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
        return str(value(name, self.metadata))


class Value(object):
    """Parse a value from the OE metadata into a Components object, held
    internally.  Running str() on this is equivalent to doing the same to its
    internal Components."""

    var_re = re.compile(r"(\$\{|\})")

    def __init__(self, val, metadata):
        if not isinstance(val, basestring):
            self.components = Components(val)
            self.value = None
        else:
            self.value = val
            self.components = Components()
        self.metadata = metadata
        self.parse()

    def __eq__(self, other):
        return isinstance(other, type(self)) and \
               self.components == other.components and \
               self.metadata == other.metadata

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.components, self.metadata))

    def __repr__(self):
        return "%s(%s, %s)" % (self.__class__.__name__, repr(self.value),
                               repr(self.metadata))

    def __str__(self):
        return str(self.components)

    def references(self):
        """Return an iterable of the variables this Value references"""

        def search(val):
            for item in val.components:
                if isinstance(item, VariableRef) and \
                   all(isinstance(x, basestring) for x in item.components):
                    yield str(item.components)

                if hasattr(item, "references"):
                    for ref in item.references():
                        yield ref
                elif hasattr(item, "components"):
                    for otheritem in search(item):
                        yield otheritem
        return search(self)

    def parse(self):
        """Parse a value from the OE metadata into a Components object"""

        if self.value is None:
            return

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
                        val = PythonSnippet(current, self.metadata)
                    else:
                        val = VariableRef(current, self.metadata)

                    current = stack.pop()
                    if current is None:
                        result.append(val)
                    else:
                        current.append(val)
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

    def __init__(self, val, metadata):
        self.funcdefs = set()
        self.execs = set()
        self.external_execs = set()
        Value.__init__(self, val, metadata)

    def parse(self):
        Value.parse(self)
        self.external_execs = self.parse_shell(str(self.components))

    def parse_shell(self, val):
        """Parse the supplied shell code in a string, returning the external
        commands it executes.
        """

        try:
            tokens, _ = pyshyacc.parse(val, True, False)
        except sherrors.ShellSyntaxError:
            bb.msg.note(1, None, "Shell syntax error when parsing:\n%s" % val)
            return ()

        for token in tokens:
            self.process_tokens(token)
        cmds = set(cmd for cmd in self.execs
                       if cmd not in self.funcdefs)
        return cmds

    def process_tokens(self, tokens):
        """Process a supplied portion of the syntax tree as returned by
        pyshyacc.parse.
        """

        def function_definition(val):
            self.funcdefs.add(val.name)
            return [val.body], None

        token_handlers = {
            "and_or": lambda x: ((x.left, x.right), None),
            "async": lambda x: ([x], None),
            "brace_group": lambda x: (x.cmds, None),
            "for_clause": lambda x: (x.cmds, x.items),
            "function_definition": function_definition,
            "if_clause": lambda x: (chain(x.if_cmds, x.else_cmds), None),
            "pipeline": lambda x: (x.commands, None),
            "redirect_list": lambda x: ([x.cmd], None),
            "simple_command": lambda x: (None, x.words),
            "subshell": lambda x: (x.cmds, None),
            "while_clause": lambda x: (chain(x.condition, x.cmds), None),
        }

        for token in tokens:
            name, val = token
            try:
                more_tokens, words = token_handlers[name](val)
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
                    bb.msg.debug(1, None, "Warning: execution of non-literal command '%s'" % word[1])
                elif cmd == "eval":
                    command = " ".join(word for _, word in words[1:])
                    self.parse_shell(command)
                else:
                    self.execs.add(cmd)

    def references(self):
        refs = Value.references(self)
        for ref in refs:
            yield ref

        for var in self.metadata.keys():
            if self.metadata.getVarFlag(var, "export"):
                yield var

        for func in self.external_execs:
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
        def compare_name(cls, val, node):
            """Convenience function for the _compare_node method, which
            can accept a string (which is split by '.' for you), or an
            iterable of strings, in which case it checks to see if any of
            them match, similar to isinstance.
            """

            if isinstance(val, basestring):
                return cls._compare_name(tuple(reversed(val.split("."))),
                                         node)
            else:
                return any(cls.compare_name(item, node) for item in val)

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

            try:
                funcstr = codegen.to_source(func)
            except Exception:
                bb.msg.debug(1, None, "codegen failed to convert %s to a string" %
                                     ast.dump(func))
                return

            try:
                argstr = codegen.to_source(arg)
            except Exception, exc:
                bb.msg.debug(1, None, "codegen failed to convert %s to a string" %
                                      ast.dump(arg))
                return

            bb.msg.debug(1, None, "Warning: in call to '%s', argument '%s' is not a literal" %
                                 (funcstr, argstr))

        def visit_Call(self, node):
            ast.NodeVisitor.generic_visit(self, node)
            if self.compare_name(self.getvars, node.func):
                if isinstance(node.args[0], ast.Str):
                    self.var_references.add(node.args[0].s)
                else:
                    self.warn(node.func, node.args[0])
            elif self.compare_name(self.expands, node.func):
                if isinstance(node.args[0], ast.Str):
                    val = Value(node.args[0].s, bb.data.init())
                    for var in val.references():
                        self.var_references.add(var)
                elif isinstance(node.args[0], ast.Call) and \
                     self.compare_name(self.getvars, node.args[0].func):
                    pass
                else:
                    self.warn(node.func, node.args[0])
            elif isinstance(node.func, ast.Name):
                self.direct_func_calls.add(node.func.id)

    def __init__(self, val, metadata):
        self.visitor = self.ValueVisitor()
        self.var_references = None
        self.calls = None

        Value.__init__(self, val, metadata)

    def parse(self):
        Value.parse(self)
        val = str(self.components)
        try:
            code = compile(val, "<string>", "exec", ast.PyCF_ONLY_AST)
        except Exception, exc:
            import traceback
            bb.msg.note(1, None, "Failed to compile %s" % val)
            bb.msg.note(1, None, str(traceback.format_exc(exc)))
        else:
            self.visitor.visit(code)

        self.var_references = self.visitor.var_references
        self.calls = self.visitor.direct_func_calls

    def references(self):
        refs = Value.references(self)
        for ref in refs:
            yield ref

        for ref in self.var_references:
            yield ref

class PythonSnippet(PythonValue):
    """Lazy evaluation of a python snippet"""

    def __str__(self):
        code = str(self.components)
        codeobj = compile(code.strip(), "<expansion>", "eval")
        try:
            val = str(bb.utils.better_eval(codeobj, {"d": self.metadata}))
        except Exception, exc:
            bb.msg.note(1, bb.msg.domain.Data,
                        "%s:%s while evaluating:\n%s" % (type(exc), exc,
                                                         code))
            return "<invalid>"
        return str(Value(val, self.metadata))


from tokenize import generate_tokens, untokenize, INDENT, DEDENT
try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

def dedent_python(codestr):
    """Remove the first level of indentation from a block of python code"""

    indent = None
    level = 0
    tokens = []
    for toknum, tokval, _, _, _ in generate_tokens(StringIO(codestr).readline):
        if toknum == INDENT:
            level += 1
            if level == 1:
                indent = tokval
                continue
            elif indent:
                tokval = tokval[len(indent):]
        elif toknum == DEDENT:
            level -= 1
            if level == 0:
                indent = None
                continue
        tokens.append((toknum, tokval))
    return untokenize(tokens)

@Memoized
def value(variable, metadata):
    """Value creation factory for a variable in the metadata"""

    val = metadata.getVar(variable, False)
    if val is None:
        return "${%s}" % variable

    if metadata.getVarFlag(variable, "func"):
        if metadata.getVarFlag(variable, "python"):
            try:
                val = dedent_python(val.expandtabs())
            except Exception, exc:
                from traceback import format_exc
                bb.msg.note(1, None, "Failed to dedent %s:" % variable)
                bb.msg.note(1, None, val)
                bb.msg.note(1, None, str(format_exc(exc)))
            return PythonValue(val, metadata)
        else:
            return ShellValue(val, metadata)
    else:
        return Value(val, metadata)

def stable_repr(val):
    """Produce a more stable 'repr' string for a value"""
    if isinstance(val, dict):
        return "{%s}" % ", ".join("%s: %s" % (stable_repr(key), stable_repr(val))
                                  for key, val in sorted(val.iteritems()))
    elif isinstance(val, (set, frozenset)):
        return "%s(%s)" % (val.__class__.__name__, stable_repr(sorted(val)))
    elif isinstance(val, list):
        return "[%s]" % ", ".join(stable_repr(val) for val in val)
    elif isinstance(val, tuple):
        return "(%s)" % ", ".join(stable_repr(val) for val in val)
    elif isinstance(val, (VariableRef, Value)):
        return "%s(%s)" % (val.__class__.__name__, stable_repr(val.components))
    return repr(val)

class Signature(object):
    def __init__(self, metadata, vars = None, blacklist = None):
        self._md5 = None
        self._data = None
        self.metadata = metadata

        if vars:
            self.vars = vars
        else:
            self.vars = [var for var in self.metadata.keys()
                         if metadata.getVarFlag(var, "task")]

        if blacklist:
            self.blacklist = blacklist
        else:
            blacklist = metadata.getVar("BB_HASH_BLACKLIST", True)
            if blacklist:
                self.blacklist = blacklist.split()
            else:
                self.blacklist = None

    def __repr__(self):
        return "Signature(%s, %s, %s)" % (self.metadata, self.vars, self.blacklist)

    def __hash__(self):
        return hash((self.metadata, self.vars, self.blacklist))

    def __str__(self):
        from base64 import urlsafe_b64encode

        return urlsafe_b64encode(self.md5.digest()).rstrip("=")

    def hash(self):
        return int(self.md5.hexdigest(), 16)

    @property
    def md5(self):
        value = self._md5
        if value is None:
            string = stable_repr(self.data)
            value = self._md5 = hashlib.md5(string)
        return value

    @property
    def data(self):
        if self._data:
            return self._data

        def data_for_hash(var):
            valstr = self.metadata.getVar(var, False)
            if valstr is not None:
                if not self.is_blacklisted(var):
                    val = self.transform_blacklisted(value(var, self.metadata))

                    yield var, val
                    if hasattr(val, "references"):
                        for ref in val.references():
                            for other in data_for_hash(ref):
                                yield other

        if not self.vars:
            self.vars = [var for var in self.metadata.keys()
                         if self.metadata.getVarFlag(var, "task")]
        data = self._data = dict(chain(*[data_for_hash(var) for var in self.vars]))
        return data

    def is_blacklisted(self, val):
        if not self.blacklist:
            return

        for bl in self.blacklist:
            if isinstance(val, Value):
                if isinstance(val.value, basestring) and \
                   fnmatchcase(val.value, bl):
                    return val.value
                elif all(isinstance(c, basestring) for c in val.components):
                    valstr = str(val.components)
                    if fnmatchcase(valstr, bl):
                        return valstr
            elif isinstance(val, VariableRef):
                if all(isinstance(c, basestring) for c in val.components):
                    valstr = str(val.components)
                    if fnmatchcase(valstr, bl):
                        return valstr
            elif isinstance(val, basestring):
                if fnmatchcase(val, bl):
                    return val

    def transform_blacklisted(self, item):
        if not self.blacklist:
            return item

        black = self.is_blacklisted(item)
        if black:
            return "${%s}" % black
        elif isinstance(item, Value):
            transformed = self.transform_blacklisted(item.components)
            if transformed != item.components:
                return item.__class__(transformed, self.metadata)
        elif isinstance(item, Components):
            transformed = self.transform_blacklisted(tuple(item))
            if transformed != item:
                return Components(transformed)
        elif isinstance(item, tuple):
            return (self.transform_blacklisted(i) for i in item)
        return item
