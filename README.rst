OE Signatures
=============

This repository holds code which revamps the way variables are expanded, in
order to add variable reference tracking.  This tracking includes direct
references, as well as getVar calls from python code, shell function
executions of other shell functions, and so on.

There are multiple purposes to this, but the most immediate is to implement
intelligent signature/hash generation of the metadata (or chunks of the
metadata).  This is necessary in order to provide sane binary caching support,
and in the future, will allow bitbake to move away from the "stamp" concept
entirely, instead relying on tracking of the input and output of tasks, and
the pieces of the metadata they use.

Beyond signature generation, we should be able to leverage this to allow us to
avoid reparsing recipes when configuration files change, as we can more
intelligently dirty the cached variable expansions with this knowledge.


Recommendations for Exception Handling
------------------------------

- When using a RefTracker or calling the convenience functions in reftracker,
  catch SyntaxError, ShellSyntaxError, and RecursionError.
- When resolving (or converting to a string), catch RecursionError,
  SyntaxError, and PythonExpansionError.


TODO
----

- Values and Reference Tracking

  - Create a subclass of Compound which associates a conditional with the
    compound value, to implement conditional append/prepend with the
    aforementioned fields
  - Potentially resurrect the inclusion of the list of nodes in the cycle in a
    RecursionError
  - Handle non-string objects:

    - Either convert non-string objects to a string before parsing them, or do
      not parse them at all
    - Run str() on self.value in Literal
    - Run str() on the object which comes out of the metadata, in VariableRef

- Signatures

  - Consider how to avoid inclusion of particular items from OVERRIDES in the
    signature, by determining which of those overrides have actually applied
    to this metadata:

    - Determine if any override specific variables been set
    - Determine if any override specific appends/prepends have occurred
    - Determine if any override specific file:// files will be used

  - We need a way to handle variable references from code in the bb and oe
    python packages.  Either we try to read in the .py files, compile to an
    ast, and analyze the functions from those that we call, or we need to set
    the necessary varrefs explicitly.  And if we set it explicitly, either we
    need to set it for every variable that calls the function, or we need to
    store, somewhere, a mapping of bb/oe function to a list of additional
    varrefs.

- Pysh

  - Handle the 'rogue dollar sign' case in shell more sanely.  Most shells
    seem just fine with 'install -d ${D}$', as the trailing $ ends up a part
    of the filename.  pysh chokes on it, however, since it's expecting to see
    the remainder of a ${} expansion.
  - Fix the AND-OR async issue in a way that can go upstream.

- BitBake Integration

  - Store methodpool functions in the metadata.
  - Add a method or methods to DataSmart which pull information about
    variables using the RefTracker.
  - Add a function similar to RP's emit_func, which leverages our shell
    parsing to get just the 'execs' for a function, to determine what other
    shell functions need to be emitted.  This is a good initial step, as it
    will not require full usage of the 'varrefs' flags in the metadata, the
    way the bitbake variable reference tracking does.

  - Alter DataSmart to know about bbvalue objects

    - Cache both the expanded value and the RefTracker data for variables.
    - Utilize the references information to more intelligently dirty cached
      information.
    - getVar must hand back a new copy of a bbvalue object bound to self, if
      we've retrieved an object from a previous datastore in the stack
    - Determine a good API for the retrieval of RefTracker data from the
      metadata -- we could simply add methods which are the same as the
      convenience functions in reftracker.py, or we could add a single method
      which returns a RefTracker object, or we could add faux flags (RP's
      idea).

  - Determine how to avoid reparsing recipes when a change occurs to a
    configuration file

    - Create a store which associates each bitbake file to the metadata object
      it's associated with, potentially as a list in parse order
    - Adjust VariableRef to always operate based upon the "current" metadata,
      potentially by using the last item in the file<->metadata mapping store
    - Alter getVar so instead of attempting to return a "copy" (from the
      Copy-On-Write implementation) of the bbvalue object, it instead returns
      a new bbvalue object which contains the old one.  The new one would be
      associated with the current metadata, but the old still associated with
      the old

- Performance

  - Check the memory impact of potentially using Value objects rather than
    the strings in the datastore.
  - In addition to caching/memoization, once we add dirty state tracking,
    it'd be possible to pre-generate the expanded version, to reduce the
    amount of work at str/getVar time.

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
