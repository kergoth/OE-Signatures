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

    for field in dir(node):
        if field.startswith('field_'):
            try:
                yield field, getattr(node, field)
            except AttributeError:
                pass

class Vistor(object):
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

class Value(object):
    """A simple value that is meant as a base class for all other values."""

    def __init__(self, metadata):
        self.metadata = metadata

    def __eq__(self, other):
        return isinstance(other, type(self)) and \
               self.metadata == other.metadata

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(id(self.metadata))

    def __str__(self):
        return self.resolve()

class Literal(Value):
    """A simple value that resolves to whatever string it was initialized
       with."""

    def __init__(self, metadata, value):
        Value.__init__(self, metadata)
        self.value = value

    def __eq__(self, other):
        return Value.__eq__(self, other) and self.value == other.value

    def __hash__(self):
        return hash((self.value, id(self.metadata)))

    def __repr__(self):
        return "Literal(%s, %s)" % (repr(self.metadata), self.value)

    def resolve(self):
        return self.value

class Compound(Value):
    """Compound values are composed of other compound values
       and literals.  The value of a compound value resolves to the
       concatenation of all its component values."""

    def __init__(self, metadata, components=[]):
        Value.__init__(self, metadata)
        self.field_components = components[:]

    def __eq__(self, other):
        return Value.__eq__(self, other) and \
               self.field_components == other.field_components

    def __hash__(self):
        return hash((repr(self), id(self.metadata)))

    def __repr__(self):
        return "%s(%s, %s)" % (self.__class__.__name__,
                               repr(self.metadata),
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

    def resolve(self):
        return "".join(c.resolve() for c in self.field_components)

class LazyCompound(Compound):
    """A Compound value which composes 3 independent lists of components:
       prepended, normal, and appended.  This is specifically to facilitate
       OpenEmbedded's _append/_prepend, which are not evaluated until the
       end of the processing.  In this implementation, they're applied at
       resolve time."""

    def __init__(self, metadata, components=[], append=[], prepend=[]):
        Compound.__init__(self, metadata, components)
        self.field_prepend = prepend[:]
        self.field_append = append[:]

    def __eq__(self, other):
        return Compound.__eq__(self, other) and \
               self.field_prepend == other.field_prepend and \
               self.field_append == other.field_append

    def __hash__(self):
        return hash((repr(self), id(self.metadata)))

    def __repr__(self):
        return "%s(%s, %s, %s, %s)" % (self.__class__.__name__,
                                       repr(self.metadata),
                                       repr(self.field_components),
                                       repr(self.field_append),
                                       repr(self.field_prepend))

    def lazy_prepend(self, value):
        """Add a value to the list of values to be prepended"""
        return self._append(value, self.field_prepend)

    def lazy_append(self, value):
        """Add a value to the list of values to be appended"""
        return self._append(value, self.field_append)

    def resolve(self):
        components = chain(self.field_prepend, self.field_components,
                           self.field_append)
        return "".join(c.resolve() for c in components)

class PythonValue(Compound):
    """A compound value that represents a value to be evaluated in Python.
       The resolution of a PythonValue takes the resolution of its
       components and returns that resolution as evaluated by Python."""

    def code(self):
        return super(PythonValue, self).resolve()

    def resolve(self):
        codeobj = compile(self.code().strip(), "<expansion>", "eval")
        try:
            value = str(utils.better_eval(codeobj, {"d": self.metadata}))
        except Exception, exc:
            raise PythonExpansionError(exc, self)
        return bbparse(value, self.metadata).resolve()

class VariableRef(Compound):
    """A compound value which holds a reference to another value.  The
       resolution of a CompundValue dereferences the value referenced and
       returns the resolution of the dereferenced value."""

    def __init__(self, metadata, components=[]):
        Compound.__init__(self, metadata, components)
        self.locked = False

    def referred(self):
        """Returns the name of the value being referred to."""

        return super(VariableRef, self).resolve()

    def resolve(self):
        refname = self.referred()
        if self.locked:
            raise RecursionError(refname)

        newvalue = bbvalue(refname, self.metadata)

        self.locked = True
        if newvalue:
            retvalue = newvalue.resolve()
        else:
            retvalue = "${%s}" % refname
        self.locked = False

        return retvalue

class ShellSnippet(Compound):
    """A compound value which holds shell code"""

class PythonSnippet(Compound):
    """A compound value which holds python code"""

def bbvalue(varname, metadata):
    """Constructs a new value from a variable defined in the BitBake
       metadata."""

    strvalue = metadata.getVar(varname, False)
    if strvalue is None:
        return None

    sigtup = (varname, strvalue, id(metadata))

    if sigtup in bbvalue.memory:
        return bbvalue.memory[sigtup]

    value = bbparse(strvalue, metadata)
    if metadata.getVarFlag(varname, "func"):
        if metadata.getVarFlag(varname, "python"):
            value = PythonSnippet(metadata, [value])
        else:
            value = ShellSnippet(metadata, [value])

    bbvalue.memory[sigtup] = value

    return value

bbvalue.memory = {}

def bbparse(str, metadata):
    """Parses a metadata string into a value Abstract Syntax Tree (AST) which
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
        clsmap = { '${': VariableRef, '${@': PythonValue }
        while toker.current:
            token = toker.current
            if token in clsmap:
                # Attempt to specutively parse the reference.  If the
                # reference never closes, then revert to a literal.
                value = _parse(toker.next(), clsmap[token](metadata))
                if toker.current == "}":
                    parent.append(value)
                else:
                    parent.extend(
                        [Literal(metadata, token)] + value.field_components)
            elif toker.current == "}" and \
                 isinstance(parent, (VariableRef, PythonValue)):
                return parent
            else:
                parent.append(Literal(metadata, toker.current))
            toker.next()
        return parent

    return _parse(Tokenizer(str), Compound(metadata))

def shparse(str, metadata):
    """Constructs a new shell value from a variable defined in the BitBake
       metadata."""

    return ShellSnippet(metadata, [bbparse(str, metadata)])

def pyparse(str, metadata):
    """Constructs a new Python value from a variable defined in the BitBake
       metadata."""

    return PythonSnippet(metadata, [bbparse(str, metadata)])
