#!/usr/bin/env python2.6
"""BitBake variable reference tracking and signature generation"""

import re
import codegen
import ast
import hashlib
import pickle
from fnmatch import fnmatchcase
from itertools import chain
from collections import deque
from pysh import pyshyacc, pyshlex, sherrors
import bb.msg
import bb.utils

from pysh.sherrors import ShellSyntaxError

class RecursionError(RuntimeError):
    def __init__(self, variable, path = None):
        self.variable = variable
        self.path = path

    def __str__(self):
        msg = "Recursive variable reference for %s" % self.variable
        if self.path:
            msg += " via %s" % " -> ".join(stable_repr(v) for v in self.path)

        return msg

class PythonExpansionError(Exception):
    def __init__(self, exception, node, path):
        self.exception = exception
        self.node = node
        self.path = path

    def __str__(self):
        msg = "%s while resolving %s" % (self.exception, stable_repr(self.node))
        if self.path:
            msg += " via %s" % " -> ".join(stable_repr(v) for v in self.path)
        return msg

class Memoized(object):
    """Decorator that caches a function's return value each time it is called.
    If called later with the same arguments, the cached value is returned, and
    not re-evaluated.
    """
    def __init__(self, func):
        self.func = func
        self.cache = {}

    def __call__(self, *args):
        key = pickle.dumps(args)
        try:
            return self.cache[key]
        except KeyError:
            self.cache[key] = value = self.func(*args)
            return value

    def __repr__(self):
        """Return the function's docstring."""
        return self.func.__doc__


class Components(list):
    """A list of components, which concatenates itself upon str(), and runs
    str() on each component.  A given component is defined as being a
    string, python snippet, or variable reference"""

    def __str__(self):
        return self.resolve()

    def _resolve(self, path = None):
        for v in self:
            if hasattr(v, "resolve"):
                yield v.resolve(path)
            else:
                yield v

    def resolve(self, path = None):
        if path is None:
            path = []
        return "".join(self._resolve(path))

    def __hash__(self):
        return hash("Components(%s)" % ", ".join(repr(c) for c in self))

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
        return self.resolve()

    def resolve(self, path = None):
        if path is None:
            path = []

        name = "".join(str(v) for v in self.components.resolve(path))
        value = new_value(name, self.metadata)
        if value in path:
            raise RecursionError(name, path)

        if hasattr(value, "resolve"):
            return value.resolve(path)
        else:
            return value


class Value(object):
    """Parse a value from the OE metadata into a Components object, held
    internally.  Running str() on this is equivalent to doing the same to its
    internal Components."""

    variable_ref = re.compile(r"(\$\{|\})")

    def __init__(self, value, metadata):
        if not isinstance(value, basestring):
            self.components = Components(value)
            self.value = None
        else:
            self.value = value
            self.components = Components()
        self.metadata = metadata
        self.references = set()
        self.parse()
        self.update_references(self)

    def update_references(self, value):
        for item in value.components:
            if isinstance(item, VariableRef):
                if all(isinstance(x, basestring) for x in item.components):
                    self.references.add("".join(item.components))
                else:
                    self.update_references(item)
            elif isinstance(item, Value):
                self.references.update(item.references)

    def __eq__(self, other):
        return isinstance(other, type(self)) and \
               self.components == other.components and \
               self.metadata == other.metadata

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.components, id(self.metadata)))

    def __repr__(self):
        return "%s(%s, %s)" % (self.__class__.__name__, repr(self.components),
                               repr(self.metadata))

    def __str__(self):
        return self.resolve()

    def resolve(self, path = None):
        if path is None:
            path = []
        path.append(self)
        resolved = self.components.resolve(path)
        path.pop()
        return resolved

    def parse(self):
        """Parse a value from the OE metadata into a Components object"""

        if self.value is None:
            return

        if not isinstance(self.value, basestring) or \
           "${" not in self.value:
            self.components.append(self.value)
            return

        tokens = (var for var in self.variable_ref.split(self.value) if var)
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

    May raise: NotImplementedError, ShellSyntaxError
    """

    def __init__(self, value, metadata):
        self.funcdefs = set()
        self.execs = set()
        self.command_executions = set()
        Value.__init__(self, value, metadata)

    def parse(self):
        Value.parse(self)
        self.command_executions = self.parse_shell(str(self.components))
        for var in self.metadata.keys():
            flags = self.metadata.getVarFlags(var)
            if flags:
                if "export" in flags:
                    self.references.add(var)
                elif var in self.command_executions and \
                     "func" in flags and "python" not in flags:
                    self.references.add(var)

    def parse_shell(self, value):
        """Parse the supplied shell code in a string, returning the external
        commands it executes.
        """

        tokens, _ = pyshyacc.parse(value, eof=True, debug=False)
        for token in tokens:
            self.process_tokens(token)
        cmds = set(cmd for cmd in self.execs
                       if cmd not in self.funcdefs)
        return cmds

    def process_tokens(self, tokens):
        """Process a supplied portion of the syntax tree as returned by
        pyshyacc.parse.
        """

        def function_definition(value):
            self.funcdefs.add(value.name)
            return [value.body], None

        def case_clause(value):
            # Element 0 of each item in the case is the list of patterns, and
            # Element 1 of each item in the case is the list of commands to be
            # executed when that pattern matches.
            words = chain(*[item[0] for item in value.items])
            cmds  = chain(*[item[1] for item in value.items])
            return cmds, words

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
            "until_clause": lambda x: (chain(x.condition, x.cmds), None),
            "case_clause": case_clause,
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
                    bb.msg.debug(1, None, "Warning: execution of non-literal command '%s'" % word[1])
                elif cmd == "eval":
                    command = " ".join(word for _, word in words[1:])
                    self.parse_shell(command)
                else:
                    self.execs.add(cmd)


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

            try:
                funcstr = codegen.to_source(func)
                argstr = codegen.to_source(arg)
            except TypeError:
                bb.msg.debug(2, None, "Failed to convert function and argument to source form")
            else:
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
                    value = Value(node.args[0].s, bb.data.init())
                    self.var_references.update(value.references)
                elif isinstance(node.args[0], ast.Call) and \
                     self.compare_name(self.getvars, node.args[0].func):
                    pass
                else:
                    self.warn(node.func, node.args[0])
            elif isinstance(node.func, ast.Name):
                self.direct_func_calls.add(node.func.id)

    def __init__(self, value, metadata):
        self.visitor = self.ValueVisitor()
        self.calls = None

        Value.__init__(self, value, metadata)

    def parse(self):
        Value.parse(self)
        value = str(self.components)
        code = compile(value, "<string>", "exec", ast.PyCF_ONLY_AST)
        self.visitor.visit(code)

        self.references.update(self.visitor.var_references)
        self.calls = self.visitor.direct_func_calls

class PythonSnippet(PythonValue):
    """Lazy evaluation of a python snippet"""

    def resolve(self, path = None):
        code = PythonValue.resolve(self, path)
        codeobj = compile(code.strip(), "<expansion>", "eval")
        try:
            value = str(bb.utils.better_eval(codeobj, {"d": self.metadata}))
        except Exception, exc:
            raise PythonExpansionError(exc, self, path)
        return Value(value, self.metadata).resolve(path)


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

_value_cache = {}
def _new_value(variable, metadata, path):
    """Implementation of value creation factory"""

    strvalue = metadata.getVar(variable, False)
    if strvalue is None:
        return "${%s}" % variable

    cache_key = (strvalue, id(metadata))
    value = _value_cache.get(cache_key)
    if value is not None:
        return value

    if metadata.getVarFlag(variable, "func"):
        if metadata.getVarFlag(variable, "python"):
            value = PythonValue(dedent_python(strvalue.expandtabs()), metadata)
        else:
            value = ShellValue(strvalue, metadata)
    else:
        value = Value(strvalue, metadata)

    _value_cache[cache_key] = value
    return value

def new_value(variable, metadata):
    """Value creation factory for a variable in the metadata"""

    return _new_value(variable, metadata, [])

def stable_repr(value):
    """Produce a more stable 'repr' string for a value"""
    if isinstance(value, dict):
        return "{%s}" % ", ".join("%s: %s" % (stable_repr(key), stable_repr(value))
                                  for key, value in sorted(value.iteritems()))
    elif isinstance(value, (set, frozenset)):
        return "%s(%s)" % (value.__class__.__name__, stable_repr(sorted(value)))
    elif isinstance(value, list):
        return "[%s]" % ", ".join(stable_repr(value) for value in value)
    elif isinstance(value, tuple):
        return "(%s)" % ", ".join(stable_repr(value) for value in value)
    elif isinstance(value, (VariableRef, Value)):
        return "%s(%s)" % (value.__class__.__name__, stable_repr(value.components))
    return repr(value)

class Signature(object):
    """A signature is produced uniquely identifying part of the BitBake metadata.

    keys is the list of variable names to include in the signature (default is
    all current tasks).  blacklist is a list of globs which identify variables
    which should not be included at all, even when referenced by other
    variables.
    """

    def __init__(self, metadata, keys = None, blacklist = None):
        self._md5 = None
        self._data = None
        self._data_string = None
        self.metadata = metadata

        if keys:
            self.keys = keys
        else:
            self.keys = [key for key in self.metadata.keys()
                         if metadata.getVarFlag(key, "task")]

        if blacklist:
            self.blacklist = blacklist
        else:
            blacklist = metadata.getVar("BB_HASH_BLACKLIST", True)
            if blacklist:
                self.blacklist = blacklist.split()
            else:
                self.blacklist = None

    def __repr__(self):
        return "Signature(%s, %s, %s)" % (self.metadata, self.keys, self.blacklist)

    def __hash__(self):
        return hash((self.metadata, self.keys, self.blacklist))

    def __str__(self):
        from base64 import urlsafe_b64encode

        return urlsafe_b64encode(self.md5.digest()).rstrip("=")

    def hash(self):
        """Return an integer version of the signature"""
        return int(self.md5.hexdigest(), 16)

    @property
    def md5(self):
        """The underlying python 'md5' object"""

        value = self._md5
        if value is None:
            value = self._md5 = hashlib.md5(self.data_string)
        return value

    @property
    def data_string(self):
        """Stabilized string representation of the data to be hashed"""
        string = self._data_string
        if string is None:
            string = self._data_string = stable_repr(self.data)
        return string

    @property
    def data(self):
        """The object containing the data which will be converted to a string and then hashed"""

        if self._data:
            return self._data

        seen = set()
        def data_for_hash(key):
            """Returns an iterator over the variable names and their values, including references"""

            if key in seen:
                return
            seen.add(key)
            valstr = self.metadata.getVar(key, False)
            if valstr is not None:
                if not self.is_blacklisted(key):
                    value = self.transform_blacklisted(new_value(key, self.metadata))

                    yield key, value
                    for ref in value.references:
                        for other in data_for_hash(ref):
                            yield other

        if not self.keys:
            self.keys = [key for key in self.metadata.keys()
                         if self.metadata.getVarFlag(key, "task")]
        data = self._data = dict(chain(*[data_for_hash(key) for key in self.keys]))
        return data

    def is_blacklisted(self, item):
        """Determine if the supplied item is blacklisted"""

        if not self.blacklist:
            return

        if isinstance(item, basestring):
            valstr = item
        elif all(isinstance(c, basestring) for c in item.components):
            valstr = str(item.components)
        else:
            return

        for bl in self.blacklist:
            if fnmatchcase(valstr, bl):
                return "${%s}" % valstr

    def transform_blacklisted(self, item):
        """Transform the supplied item tree, changing all blacklisted objects
        into their unexpanded forms.
        """

        if not self.blacklist:
            return item

        if isinstance(item, Value):
            transformed = Components(self.transform_blacklisted(i) for i in item.components)
            if transformed != item.components:
                return item.__class__(transformed, self.metadata)
        elif isinstance(item, VariableRef):
            black = self.is_blacklisted(item)
            if black:
                return black
        return item
