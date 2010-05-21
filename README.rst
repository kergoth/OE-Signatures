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

- When constructing a Value, catch SyntaxError (if there may be a python
  snippet).
- When constructing a ShellValue, catch ShellSyntaxError, and
  NotImplementedError.
- When constructing a PythonValue, catch SyntaxError.
- When constructing a value via the new_value factory, catch all of the above.

- When resolving (or converting to a string), catch RecursionError,
  SyntaxError, and PythonExpansionError.


TODO
----

- Top Priority Tasks

  - Finish populating all necessary varrefs flags for the packaging classes in
    OpenEmbedded
  - Audit all OpenEmbedded metadata for changes to OVERRIDES followed by calls
    to update_data.  These cases can almost certainly be replaced with
    directly accessing the specific conditional variables they want (i.e.
    RDEPENDS_<pkg>).

  - Implement one or more checking / auditing mechanisms to determine if the
    Signature really does capture everything a task needs.
    - In TaskStarted (assuming the event is fired with the post-createCopy
      datastore for the task, and assuming its run within the task's process),
      we can monkeypatch bb.data.getVar() and bb.data.expand() to gather up a
      list of the variables the task really does use during its execution, and
      compare that to what the Signature captured.
    - An alternative approach would be to filter the datastore in TaskStarted,
      removing everything the signature didn't capture, and seeing what blows
      up.  The problem with this method is that it could not blow up, instead
      just producing different output, so ideally to implement this we'd also
      need to add capturing of task output for comparison.

  - Cache blacklist transformations
  - Do extensive profiling to improve performance

- General

  - Think about storing the PythonValue ast and utilizing it in PythonSnippet
    to compile the code from that, rather than having it re-parse the string.
    The 'compile' function can compile an ast object directly.
  - Handle the 'rogue dollar sign' case in shell more sanely.  Most shells
    seem just fine with 'install -d ${D}$', as the trailing $ ends up a part
    of the filename.  pysh chokes on it, however, since it's expecting to see
    the remainder of a ${} expansion.
  - Fix the AND-OR async issue in a way that can go upstream.
  - The path information for the runtime recursion check appears to leave out
    the top element, at least in some cases.
  - Consider reworking the classes to be more data only nodes in a tree with
    traversal tools, like traditional AST / semantic model, rather than the
    current method.  While the current method of including behavior in the
    classes is nicer from that perspective, it scatters the tree traversal
    code across the nodes, and that behavior is fairly common.

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
  - Revamp the methodpool functions.  We can't have them only in the
    methodpool as python objects, we need to retain an association with the
    metadata variables.

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
