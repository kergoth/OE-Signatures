"""This module defines a set of classes and free functions for building
   BitBake values.  Values can either be created directly or parsed from
   a string."""

import re
from collections import deque
from itertools import chain
import bb.data
from bb import msg, utils

class RecursionError(RuntimeError):
    def __init__(self, variable):
        self.variable = variable

    def __str__(self):
        return "Recursive variable reference for '%s'" % self.variable

class PythonExpansionError(Exception):
    def __init__(self, exception, node):
        self.exception = exception
        self.node = node

    def __str__(self):
        return "'%s' while resolving '%s'" % (self.exception, str(self.node))


# The following Visitor infrastructure was heavily inspired by the one
# implemented in the Python standard library module 'ast'.

def iter_fields(node):
    """
    Yield a tuple of ``(fieldname, value)`` for each field in ``node._fields``
    that is present on *node*.
    """

    if hasattr(node, "_fields"):
        fields = ("field_%s" % field for field in node._fields)
    else:
        fields = (field for field in dir(node) if field.startswith("field_"))

    for field in fields:
        try:
            yield field, getattr(node, field)
        except AttributeError:
            pass

class Visitor(object):
    def visit(self, node):
        """Visit a node."""

        method = 'visit_' + node.__class__.__name__
        visitor = getattr(self, method, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node):
        """Called if no explicit visitor function exists for a node."""

        for field, value in iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, Value):
                        self.visit(item)
            elif isinstance(value, Value):
                self.visit(value)

from copy import copy

class Transformer(Visitor):
    def generic_visit(self, node):
        newdata = []
        for field, value in iter_fields(node):
            if isinstance(value, list):
                newvalue = []
                for item in value:
                    if isinstance(item, Value):
                        item = self.visit(item)
                    newvalue.append(item)
            elif isinstance(value, Value):
                newvalue = self.visit(value)
            else:
                continue

            if newvalue != value:
                newdata.append((field, newvalue))

        if newdata:
            newnode = copy(node)
            for field, value in newdata:
                setattr(newnode, field, value)
            return newnode
        else:
            return node

class Resolver(Transformer):
    def __init__(self, metadata, crossref=True):
        self.metadata = metadata
        self.crossref = crossref
        super(Resolver, self).__init__()

    def values(self, node):
        for _, value in iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    yield self.visit(item)
            elif isinstance(value, Value):
                yield self.visit(value)

    def generic_visit(self, node):
        node = super(Resolver, self).generic_visit(node)
        return "".join(self.values(node))

    def visit_str(self, node):
        return node

    def visit_Literal(self, node):
        return str(node.value)

    def visit_VariableRef(self, node):
        name = self.generic_visit(node)
        if self.crossref:
            value = bbvalue(name, self.metadata)
            if value is None:
                return "${%s}" % name
            else:
                return self.visit(value)
        else:
            return "${%s}" % name

    def visit_PythonValue(self, node):
        code = self.generic_visit(node)
        codeobj = compile(code.strip(), "<expansion>", "eval")
        try:
            value = str(utils.better_eval(codeobj, {"d": self.metadata}))
        except Exception, exc:
            raise PythonExpansionError(exc, self)
        return self.visit(bbparse(value))

    def visit_Conditional(self, node):
        if node.condition is None or node.condition(self.metadata):
            return self.generic_visit(node)
        else:
            return ""

class Blacklister(Transformer):
    def __init__(self, metadata, is_blacklisted):
        self.metadata = metadata
        self.is_blacklisted = is_blacklisted
        self.resolver = Resolver(metadata, True)
        super(Blacklister, self).__init__()

    def generic_visit(self, node):
        newnode = super(Blacklister, self).generic_visit(node)
        if node != newnode:
            newnode.blacklisted = True
        return newnode

    def visit_VariableRef(self, node):
        node = self.generic_visit(node)
        name = self.resolver.generic_visit(node)
        if hasattr(node, "blacklisted") or self.is_blacklisted(name):
            return Literal("${%s}" % name)
        else:
            return node

class Value(object):
    """A simple value that is meant as a base class for all other values."""
    def __eq__(self, other):
        return isinstance(other, type(self))

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(id(self.metadata))

class Literal(Value):
    """A simple value that resolves to whatever object it was initialized
       with."""

    def __init__(self, value):
        Value.__init__(self)
        self.value = value

    def __eq__(self, other):
        return Value.__eq__(self, other) and self.value == other.value

    def __hash__(self):
        return hash(self.value)

    def __repr__(self):
        return "Literal(%s)" % repr(self.value)

class Compound(Value):
    """Compound values are composed of other compound values
       and literals.  The value of a compound value resolves to the
       concatenation of all its component values."""

    def __init__(self, components=[]):
        Value.__init__(self)
        self.field_components = components[:]

    def __eq__(self, other):
        return Value.__eq__(self, other) and \
               self.field_components == other.field_components

    def __hash__(self):
        return hash(repr(self))

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__,
                           repr(self.field_components))

    @staticmethod
    def _append(value, field):
        """Append a new value to a field, coalescing adjacent Literals"""

        def can_coalesce(a, b):
            return isinstance(a, Literal) and isinstance(b, Literal)

        if len(field) > 0 and can_coalesce(field[-1], value):
            field[-1].value += value.value
        else:
            field.append(value)

    def append(self, value):
        """Append a new value to the compound value."""
        return self._append(value, self.field_components)

    def extend(self, values):
        """Extend the compound value with a sequence of values."""

        for value in values:
            self.append(value)

class LazyCompound(Compound):
    """A Compound value which composes 3 independent lists of components:
       prepended, normal, and appended.  This is specifically to facilitate
       OpenEmbedded's _append/_prepend, which are not evaluated until the
       end of the processing.  In this implementation, they're applied at
       resolve time."""

    def __init__(self, components=[], append=[], prepend=[]):
        Compound.__init__(self, components)
        self.field_prepend = prepend[:]
        self.field_append = append[:]
        self._fields = ["prepend", "components", "append"]

    def __eq__(self, other):
        return Compound.__eq__(self, other) and \
               self.field_prepend == other.field_prepend and \
               self.field_append == other.field_append

    def __repr__(self):
        return "%s(%s, %s, %s)" % (self.__class__.__name__,
                                   repr(self.field_components),
                                   repr(self.field_append),
                                   repr(self.field_prepend))

    def lazy_prepend(self, value):
        """Add a value to the list of values to be prepended"""
        return self._append(value, self.field_prepend)

    def lazy_append(self, value):
        """Add a value to the list of values to be appended"""
        return self._append(value, self.field_append)

class PythonValue(Compound):
    """A compound value that represents a value to be evaluated in Python.
       The resolution of a PythonValue takes the resolution of its
       components and returns that resolution as evaluated by Python."""

class VariableRef(Compound):
    """A compound value which holds a reference to another value.  The
       resolution of a CompundValue dereferences the value referenced and
       returns the resolution of the dereferenced value."""

class ShellSnippet(Compound):
    """A compound value which holds shell code"""

class PythonSnippet(Compound):
    """A compound value which holds python code"""

class Conditional(Compound):
    """A Compound which resolves to its components only when the associated
       condition is true.  The condition is a function which is passed the
       metadata instance, and returns a boolean result.  A condition of 'None'
       is equivalent to an unconditional value."""

    def __init__(self, condition=None, components=[]):
        super(Conditional, self).__init__(components)
        self.condition = condition

def bbvalue(varname, metadata):
    """Constructs a new value from a variable defined in the BitBake
       metadata."""

    strvalue = metadata.getVar(varname, False)
    if strvalue is None:
        return None

    sigtup = (varname, strvalue, id(metadata))

    if sigtup in bbvalue.memory:
        return bbvalue.memory[sigtup]

    if not isinstance(strvalue, basestring):
        return Literal(strvalue)

    value = bbparse(strvalue)
    if metadata.getVarFlag(varname, "func"):
        if metadata.getVarFlag(varname, "python"):
            value = PythonSnippet([value])
        else:
            value = ShellSnippet([value])

    bbvalue.memory[sigtup] = value

    return value

bbvalue.memory = {}

def bbparse(str):
    """Parses a string into a value Abstract Syntax Tree (AST) which
       represents the structure of that string."""

    class Tokenizer(object):
        variable_ref = re.compile(r"(\$\{@|\$\{|\})")

        def __init__(self, str):
            self.tokens = [var for var in Tokenizer.variable_ref.split(str)                                    if var]
            self.i = 0

        def next(self):
            self.i += 1
            return self

        @property
        def current(self):
            if self.i < len(self.tokens):
                return self.tokens[self.i]
            else:
                return None

    def _parse(toker, parent):
        clsmap = {'${': VariableRef, '${@': PythonValue}
        while toker.current:
            token = toker.current
            if token in clsmap:
                # Attempt to specutively parse the reference.  If the
                # reference never closes, then revert to a literal.
                value = _parse(toker.next(), clsmap[token]())
                if toker.current == "}":
                    parent.append(value)
                else:
                    parent.extend(
                        [Literal(token)] + value.field_components)
            elif toker.current == "}" and \
                 isinstance(parent, (VariableRef, PythonValue)):
                return parent
            else:
                parent.append(Literal(toker.current))
            toker.next()
        return parent

    return _parse(Tokenizer(str), Compound())

def shparse(str):
    """Constructs a new shell value"""

    return ShellSnippet([bbparse(str)])

def pyparse(str):
    """Constructs a new Python value"""

    return PythonSnippet([bbparse(str)])

def resolve(value, metadata, crossref=True):
    """Resolve a value using the supplied BitBake metadata"""
    return Resolver(metadata, crossref).visit(value)

#  vim: set et fenc=utf-8 sts=4 sw=4 ts=4 :
