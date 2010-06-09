BB_NUMBER_THREADS = "1"

python track_task_varrefs () {
    import bb.build
    import bb.data
    import bb.data_smart
    from collections import defaultdict
    from traceback import format_exc
    from pickle import UnpicklingError
    from contextlib import closing
    import shelve

    # NOTE: This is hardcoded because at the moment the Task* events don't
    # bring along a reference to the task metadata.
    varrefs_fn = "/home/kergoth/Code/oe/varrefs.shelf"
    excluded = ("do_display_shelf", "do_write_signature",
                "do_write_signature_all")

    def monkey_patch(e):
        if "orig" in __builtins__ or e._task in excluded:
            return

        orig = bb.data_smart.DataSmart.getVar
        myGetVar = None
        def _myGetVar(data, name, expand = False):
            if not name.startswith("@"):
                myGetVar.data[e._task].add(str(name))
            return orig(data, name, expand)
        __builtins__["orig"] = orig
        myGetVar = _myGetVar
        myGetVar.data = defaultdict(set)
        bb.data_smart.DataSmart.getVar = myGetVar

    def un_monkey_patch(e):
        if "orig" not in __builtins__ or e._task in excluded:
            return

        monitored = bb.data_smart.DataSmart.getVar.data
        bb.data_smart.DataSmart.getVar = __builtins__["orig"]
        del __builtins__["orig"]

        with closing(shelve.open(varrefs_fn, writeback=True)) as shelf:
            if shelf.has_key(e._task):
                data = set(shelf[e._task])
            else:
                data = set()

            data.update(monitored[e._task])
            shelf[e._task] = data

    def monkey_patch_exec(e):
        orig = bb.build.exec_func_python
        def newfunc(func, data, runfile, logfile):
            monkey_patch(e)
            ret = newfunc.orig(func, data, runfile, logfile)
            un_monkey_patch(e)
        newfunc.orig = orig
        bb.build.exec_func_python = newfunc

    def un_monkey_patch_exec(e):
        bb.build.exec_func_python = bb.build.exec_func_python.orig

    if isinstance(e, bb.build.TaskStarted):
        try:
            monkey_patch_exec(e)
        except Exception, exc:
            bb.fatal(format_exc(exc))
    elif isinstance(e, (bb.build.TaskSucceeded, bb.build.TaskFailed)):
        try:
            un_monkey_patch_exec(e)
        except Exception, exc:
            bb.fatal(format_exc(exc))
}
do_track_task_varrefs[lockfiles] = "${TOPDIR}/varrefs.lock"
addhandler track_task_varrefs

python do_display_shelf () {
    from contextlib import closing
    import shelve
    import kergoth

    with closing(shelve.open("/home/kergoth/Code/oe/varrefs.shelf", writeback=True)) as shelf:
        for var in d.keys():
            if not d.getVarFlag(var, "task") or not d.getVarFlag(var, "python"):
                continue

            if shelf.has_key(var):
                signature = kergoth.Signature(d, keys=[var])
                missing = shelf[var].difference(set(signature.data.keys()))
                for m in set(missing):
                    if d.getVarFlag(m, "export") or \
                       (d.getVarFlag(m, "func") and not d.getVarFlag(m, "python")):
                        missing.remove(m)

                if missing:
                    bb.note("%s[varrefs] += \"%s\"" % (var, " ".join(missing)))
}
do_display_shelf[lockfiles] = "${TOPDIR}/varrefs.lock"
do_display_shelf[nostamp] = "1"
addtask display_shelf after do_build
