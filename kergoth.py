"""Staging area for OE python bits for kergoth"""

import re
from collections import deque
import bb.msg
import bb.utils

class Components(list):
    """A list of components, which concatenates itself upon str(), and runs
    str() on each component.  A given component is defined as being either a
    string or a variable reference"""

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
            var = Value(self.metadata.getVar(name, False), self.metadata)
        return str(var)

class PythonValue(object):
    """Lazy evaluation of a python snippet in the form of a Components object"""

    def __init__(self, components, metadata):
        self.components = components
        self.metadata = metadata

    def __str__(self):
        code = str(self.components)
        codeobj = compile(code.strip(), "<expansion>", "eval")
        try:
            value = bb.utils.better_eval(codeobj, {"d": self.metadata})
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

        if not isinstance(self.value, basestring):
            return

        if "${" not in self.value:
            self.components = self.value
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
                        value = PythonValue(current, self.metadata)
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