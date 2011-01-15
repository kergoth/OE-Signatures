"""This module defines a set of classes and free functions for building
   BitBake values.  Values can either be created directly or parsed from
   a string."""

import re


class Value(object):
    """Base class for other values"""

class Literal(Value):
    """A simple value that resolves to whatever object it was initialized
       with."""

    def __init__(self, value):
        Value.__init__(self)
        self.value = value

    def __eq__(self, other):
        return isinstance(other, type(self)) and \
               self.value == other.value

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
        return isinstance(other, type(self)) and \
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


def bbvalue(varname, metadata):
    """Constructs a new value from a variable defined in the BitBake
       metadata."""

    strvalue = metadata.getVar(varname, False)
    if strvalue is None:
        return None

    if not isinstance(strvalue, basestring):
        return Literal(strvalue)

    sigtup = (varname, strvalue, id(metadata))

    if sigtup in bbvalue.memory:
        return bbvalue.memory[sigtup]

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
            self.tokens = [var for var in Tokenizer.variable_ref.split(str)
                               if var]
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

#  vim: set et fenc=utf-8 sts=4 sw=4 ts=4 :
