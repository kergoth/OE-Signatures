import hashlib
import bbvalue
import bb.data
import reftracker
from fnmatch import fnmatchcase
from itertools import chain
from bb import msg, utils

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
    elif isinstance(value, bbvalue.Compound):
        return "%s(%s)" % (value.__class__.__name__, 
                           stable_repr(value.field_components))
    elif isinstance(value, bbvalue.Literal):
        return "Literal('%s')" % str(value)
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

        self.build_signature()

    def __repr__(self):
        return "Signature(%s, %s, %s)" % (self.metadata, self.keys, self.blacklist)

    def __hash__(self):
        return hash((id(self.metadata), self.keys, self.blacklist))

    def __str__(self):
        from base64 import urlsafe_b64encode

        return urlsafe_b64encode(self.md5.digest()).rstrip("=")

    def hash(self):
        """Return an integer version of the signature"""
        return int(self.md5.hexdigest(), 16)

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

        if isinstance(item, bbvalue.PythonValue):
            return bbvalue.Literal(self.metadata, str(item))
        elif isinstance(item, bbvalue.Compound):
            transformed = [self.transform_blacklisted(i) 
                              for i in item.field_components]
            if transformed != item.field_components:
                return item.__class__(self.metadata, transformed)
        elif isinstance(item, bbvalue.VariableRef):
            black = self.is_blacklisted(item)
            if black:
                return black
        return item

    def build_signature(self):
        def data_for_hash(key, seen):
            """Returns an iterator over the variable names and their values, including references"""

            if key in seen:
                return
            seen.add(key)
            if self.is_blacklisted(key):
                return

            valstr = self.metadata.getVar(key, False)
            if valstr is not None:
                try:
                    value = self.transform_blacklisted(
                        bbvalue.bbvalue(key, self.metadata))
                except (SyntaxError, NotImplementedError,
                        bbvalue.PythonExpansionError, 
                        bbvalue.RecursionError), exc:
                    msg.error(None, "Unable to parse %s, excluding from signature: %s" %
                                 (key, exc))
                else:
                    yield key, value

                    for ref in reftracker.references(value, self.metadata):
                        for other in data_for_hash(ref, seen):
                            yield other

        seen = set()
        self.data = dict(chain(*[data_for_hash(key, seen) for key in self.keys]))
        self.data_string = stable_repr(self.data)
        self.md5 = hashlib.md5(self.data_string)
