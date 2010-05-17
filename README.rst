OE Signatures
=============

This bitbake layer (OpenEmbedded Overlay) exists to test some code which I've
thrown together.  This code changes the way variables are expanded, to allow
more information to be retained easily about what it references.  It also adds
tracking of what shell functions are called by other shell functions, and what
variables are used from the metadata by python code.

There are multiple purposes of this, but the most immediate is to implement
intelligent signature/hash generation of the metadata (or chunks of the
metadata).  This is necessary in order to provide sane binary caching support,
and in the future, will allow bitbake to move away from the "stamp" concept
entirely, instead relying on tracking of the input and output of tasks, and
the pieces of the metadata they use.

Next, this should allow us, in the long term, to allow one to edit a
configuration file without reparsing the world.  Of course, Holger's ast work
is a step in the right direction for that also, but this would allow us to
bypass re-execution of the ast statements as well, simply letting 'dirty'
state information flow through the variable references.


Recommendations for Exception Handling
------------------------------

- When constructing a Value, catch RecursionError and SyntaxError (if there may
  be a python snippet).
- When constructing a ShellValue, catch RecursionError, ShellSyntaxError, and
  NotImplementedError.
- When constructing a PythonValue, catch SyntaxError.
- When constructing a value via the new_value factory, catch all of the above.

- When resolving (or converting to a string), catch RecursionError,
  SyntaxError, and PythonExpansionError.


TODO
----

- Top Priority Tasks

  - Fix pysh lexing issues.  "fi" isn't always a reserved word, so it needs to
    be handled more like "in".  In angstrom-feed-configs.bb, 'fi' is one of
    the words a for loop is iterating over, and that seems to be legal.  It
    appears that this is the case for multiple so called "reserved" words in
    the lexer.  You can use 'case', 'esac', 'for', 'do', 'fi', etc as 'for x
    in' words, as well as variable names.
  - Add support for a variable flag which indicates more explicitly which
    variables are being referenced by this variable.  This should allow us to
    work around the current issues where the referenced variable name is
    constructed programmatically.
  - Determine how best to handle exceptions for the signature generation
    process.  Currently new_value is called but we aren't catching the
    necessary exceptions there.  I expect we'll just want to warn for each,
    but continue with the generation of the signature, but an alternative
    would be to either let them get passed up as is, or wrap them in some sort
    of signature failure exception.

  - Cache blacklist transformations
  - Do extensive profiling to improve performance

- General

  - The path information for the runtime recursion check appears to leave out
    the top element, at least in some cases.
  - Create generally useful Visitor/Transformer classes for a Value tree, and
    consider using them for the resolve process, rather than doing it so
    implicitly in code scattered across the classes.

- BitBake Integration

  - Teach the Value objects to append/prepend to one another, as this is
    necessary to handle the append/prepend operations from the files we
    parse.
  - Try constructing the Value objects directly from the AST statements and
    storing it in the metadata rather than a string.
  - Determine how to handle Value objects with regard to the COW metadata
    objects.  Should getvar return a new value bound to the current object,
    or should the original know something about the layering?  I expect the
    former, but needs thought.
  - Longer term: potentially construct non-string values based on flags.

- Cleanup

  - Potentially, we could either move bits out of parse() to make them more
    lazy, likely via properties, or we could move more into parse, to do as
    much as we can up front, or somewhere in between.  Not sure what's best.
    Also, doing this much called via the constructor could be bad, maybe we
    should move that logic into a factory, since its more about how this
    thing is created than anything else..
  - In pysh, add Case support to format_commands.
  - Split up the python and shell unit tests into multiple tests in a suite
    for each, rather than one big string that tries to do it all.  Note that I
    have started splitting up the shell test as a part of the work to support
    case statements.

  - Sanitize the property names amongst the Value implementations

    - Rename 'references', as it is specifically references to variables in
      the metadata.  This isn't the only type of reference we have anymore, as
      we'll also be tracking calls to the methods in the methodpool.

- Performance

  - Check the memory impact of potentially using Value objects rather than
    the strings in the datastore.
  - In addition to caching/memoization, once we add dirty state tracking,
    it'd be possible to pre-generate the expanded version, to reduce the
    amount of work at str/getVar time.
  - May want to add a tree simplification phase.  If a Value contains only
    one component, the wrapping Value could go away in favor of the
    underlying object, reducing the amount of tree traversal necessary to do
    the resolve/expansion operation.

    - Tested a first attempt at this, results in the recursion checks
      failing to do their jobs for some reason.  Needs further
      investigation.

Known Issues / Concerns
-----------------------

- It has to expand the shell and python code in order to scan it to extract
  the variable reference information.  In some cases, this means the expansion
  may be occurring sooner than it would normally expect to happen.  As an
  example, a variable which runs a function in a ${@} snippet that reads from
  a file in staging -- this will not be happy if expanded before the task is
  actually to be run.  It may be that we'll want to avoid this sort of thing,
  as it also causes problems with bitbake -e.
- Currently it pays zero attention to flags, as flags generally instruct
  bitbake in *how* to make something happen, not *what* will happen, for a
  given task.

- ShellValue

  - The variables which are flagged as 'export' are added to the references
    for the ShellValue at object creation time currently.  In addition, the
    external command executions are filtered based on the available shell
    functions defined in the metadata.  This will be an issue if we start
    constructing value objects in the AST as the statements are evaluated, due
    to the order of operations.  Either references should become a property
    for ShellValue which adds the current exports to the internally held
    references, or we'll have to add the current exports in the finalize step
    or something.
  - The shell code which identifies defined functions and excludes them from
    the list of executed commands does not take into account context.  If one
    defined a function in a subshell, it would still exclude it from the list.
  - Cannot currently determine what variable (if a variable) is being
    referenced if it's a shell variable expansion.  As an example: 'for x in 1
    2 3; eval $x; done'

- PythonValue

  - Cannot determine what variable is being referenced when the argument to
    the getVar is not a literal string.  As an example, '"RDEPENDS_" + pkg'
    bites us.
  - Does not exclude locally imported functions from the list of executed
    functions.  If you run 'from collections import defaultdict', and run
    defaultdict, it will include defaultdict in the list of executed
    functions.  We should check for those import statements.
  - It captures a list of functions which are executed directly (that is,
    they're names, not attributes), but does not exclude functions which are
    actually defined in this same block of code.  We should try to do so,
    though it will be difficult to be full proof without taking into account
    contexts.

..  vim: set et fenc=utf-8 sts=2 sw=2 :
