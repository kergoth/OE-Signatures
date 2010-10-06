"""Tools for traversing the bbvalue ast"""

from copy import copy
from bb import utils
import bbvalue


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
                    if isinstance(item, bbvalue.Value):
                        self.visit(item)
            elif isinstance(value, bbvalue.Value):
                self.visit(value)

class Transformer(Visitor):
    def generic_visit(self, node):
        newdata = []
        for field, value in iter_fields(node):
            if isinstance(value, list):
                newvalue = []
                for item in value:
                    if isinstance(item, bbvalue.Value):
                        item = self.visit(item)
                    newvalue.append(item)
            elif isinstance(value, bbvalue.Value):
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


class MetadataMapping(dict):
    def __init__(self, metadata, *args, **kwargs):
        self.metadata = metadata
        dict.__init__(self, *args, **kwargs)

    def __missing__(self, key):
        value = self.metadata.getVar(key, True)
        if value is None:
            raise KeyError(key)
        return value

class Resolver(Transformer):
    """Convert a bbvalue tree into a string, optionally resolving
       variable references"""

    def __init__(self, metadata, crossref=True):
        self.metadata = metadata
        self.mapping = MetadataMapping(self.metadata)
        self.mapping["d"] = metadata
        self.crossref = crossref
        super(Resolver, self).__init__()

    def values(self, node):
        for _, value in iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    yield self.visit(item)
            elif isinstance(value, bbvalue.Value):
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
            value = bbvalue.bbvalue(name, self.metadata)
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
            value = str(utils.better_eval(codeobj, self.mapping))
        except Exception, exc:
            raise PythonExpansionError(exc, self)
        return self.visit(bbvalue.bbparse(value))

    def visit_Conditional(self, node):
        if node.condition is None or node.condition(self.metadata):
            return self.generic_visit(node)
        else:
            return ""

def resolve(value, metadata, crossref=True):
    """Resolve a value using the supplied BitBake metadata"""
    return Resolver(metadata, crossref).visit(value)

def expand(variable, metadata):
    """Expand a variable from the metadata, given its name"""
    value = bbvalue.bbvalue(variable, metadata)
    return resolve(value, metadata)
