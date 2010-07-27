import codegen
import bbvalue
import bb.data
from pysh import pyshyacc, pyshlex
from fnmatch import fnmatchcase
from itertools import chain
import ast
from bb import msg, utils

from pysh.sherrors import ShellSyntaxError

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

class RefTracker(bbvalue.Vistor):
    class ValueVisitor(ast.NodeVisitor):
        """Visitor to traverse a python abstract syntax tree and obtain
        the variables referenced via bitbake metadata APIs, and the external
        functions called.
        """

        getvars = ("d.getVar", "bb.data.getVar", "data.getVar")
        expands = ("d.expand", "bb.data.expand", "data.expand")
        execs = ("bb.build.exec_func", "bb.build.exec_task")

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

        def __init__(self, value):
            self.var_references = set()
            self.var_execs = set()
            self.direct_func_calls = set()
            self.value = value
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
                msg.debug(2, None, "Failed to convert function and argument to source form")
            else:
                msg.debug(1, None, "Warning: in call to '%s', argument '%s' is not a literal" %
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
                    self.var_references.update(references(node.args[0].s,
                                               self.value.metadata))
                elif isinstance(node.args[0], ast.Call) and \
                     self.compare_name(self.getvars, node.args[0].func):
                    pass
                else:
                    self.warn(node.func, node.args[0])
            elif self.compare_name(self.execs, node.func):
                if isinstance(node.args[0], ast.Str):
                    self.var_execs.add(node.args[0].s)
                else:
                    self.warn(node.func, node.args[0])
            elif isinstance(node.func, ast.Name):
                self.direct_func_calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                # We must have a qualified name.  Therefore we need
                # to walk the chain of 'Attribute' nodes to determine
                # the qualification.
                attr_node = node.func.value
                identifier = node.func.attr
                while isinstance(attr_node, ast.Attribute):
                    identifier = attr_node.attr + "." + identifier
                    attr_node = attr_node.value
                if isinstance(attr_node, ast.Name):
                    identifier = attr_node.id + "." + identifier
                self.direct_func_calls.add(identifier)

    def __init__(self):
        self.execs = set()
        self.references = set()
        self.funcdefs = set()
        self.function_references = set()
        self.calls = None

    def visit_ShellSnippet(self, node):
        for subnode in node.field_components:
            self.visit(subnode)

        self.execs = self.parse_shell(str(node))
        for var in node.metadata.keys():
            flags = node.metadata.getVarFlags(var)
            if flags:
                if "export" in flags:
                    self.references.add(var)
                elif var in self.execs and \
                     "func" in flags and "python" not in flags:
                    self.references.add(var)

    def visit_VariableRef(self, node):
        self.references.add(node.referred())

    def visit_PythonSnippet(self, node):
        for subnode in node.field_components:
            self.visit(subnode)

        code = compile(dedent_python(str(node)), "<string>", "exec", 
                       ast.PyCF_ONLY_AST)

        self.visitor = self.ValueVisitor(node)
        self.visitor.visit(code)

        self.references.update(self.visitor.var_references)
        self.references.update(self.visitor.var_execs)
        self.calls = self.visitor.direct_func_calls
        env = {}
        for var in self.calls:
            try:
                func_obj = utils.better_eval(var, env)
                self.function_references.add((var, func_obj))
            except (NameError, AttributeError):
                pass

    def parse_shell(self, value):
        """Parse the supplied shell code in a string, returning the external
        commands it executes.
        """

        try:
            tokens, _ = pyshyacc.parse(value, eof=True, debug=False)
        except pyshlex.NeedMore:
            raise ShellSyntaxError("Unexpected EOF")

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

        def if_clause(value):
            main = chain(value.cond, value.if_cmds)
            rest = value.else_cmds
            if isinstance(rest, tuple) and rest[0] == "elif":
                return chain(main, if_clause(rest[1]))
            else:
                return chain(main, rest)

        def simple_command(value):
            return None, chain(value.words, 
                               (assign[1] for assign in value.assigns))

        token_handlers = {
            "and_or": lambda x: ((x.left, x.right), None),
            "async": lambda x: ([x], None),
            "brace_group": lambda x: (x.cmds, None),
            "for_clause": lambda x: (x.cmds, x.items),
            "function_definition": function_definition,
            "if_clause": lambda x: (if_clause(x), None),
            "pipeline": lambda x: (x.commands, None),
            "redirect_list": lambda x: ([x.cmd], None),
            "subshell": lambda x: (x.cmds, None),
            "while_clause": lambda x: (chain(x.condition, x.cmds), None),
            "until_clause": lambda x: (chain(x.condition, x.cmds), None),
            "simple_command": simple_command,
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

        words = list(words)
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

        usetoken = False
        for word in words:
            if word[0] in ("cmd_name", "cmd_word") or \
               (usetoken and word[0] == "TOKEN"):
                if "=" in word[1]:
                    usetoken = True
                    continue

                cmd = word[1]
                if cmd.startswith("$"):
                    msg.debug(1, None, 
                        "Warning: execution of non-literal command '%s'" % cmd)
                elif cmd == "eval":
                    command = " ".join(word for _, word in words[1:])
                    self.parse_shell(command)
                else:
                    self.execs.add(cmd)
                break

def references(value, metadata):
    tracker = RefTracker()
    tracker.visit(value)
    return tracker.references

def referencesFromName(varname, metadata):
    refs = references(bbvalue.bbvalue(varname, metadata), metadata)
     
    dirs = metadata.getVarFlag(varname, "dirs")
    if dirs:
        refs.update(references(dirs, metadata))

    varrefs = metadata.getVarFlag(varname, "varrefs")
    if varrefs:
        refs.update(references(varrefs, metadata))
        patterns = str(bbvalue.parse(varrefs, metadata)).split()
        for key in metadata.keys():
            if any(fnmatchcase(key, pat) for pat in patterns):
                refs.add(key)

    return refs

def execs(value, metadata):
    tracker = RefTracker()
    tracker.visit(value)
    return tracker.execs

def calls(value, metadata):
    tracker = RefTracker()
    tracker.visit(value)
    return tracker.calls

def function_references(value, metadata):
    tracker = RefTracker()
    tracker.visit(value)
    return tracker.function_references
