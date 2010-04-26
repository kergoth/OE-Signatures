"""Staging area for OE python bits for kergoth"""

import re
from collections import deque
import bb.data

class Components(list):
    """A list of components, which concatenates itself upon str(), and runs
    str() on each component.  A given component is defined as being either a
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

    def __str__(self):
        name = str(self.components)
        value = Value(self.metadata.getVar(name, False), self.metadata)
        return str(value)

class PythonValue(object):
    """Lazy evaluation of a python snippet in the form of a Components object"""

    def __init__(self, components, metadata):
        self.components = components
        self.metadata = metadata

    def __str__(self):
        import bb
        code = "".join(self.components)
        locals()['d'] = self.metadata
        try:
            value = str(eval(code))
        except Exception, exc:
            bb.msg.note(1, bb.msg.domain.Data,
                        "%s:%s while evaluating:\n%s" % (type(exc), exc,
                                                         code))
            raise
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

    def parse(self):
        """Parse a value from the OE metadata into a Components object"""

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
                        value = PythonValue([current[0][1:]] +
                                             current[1:], self.metadata)
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