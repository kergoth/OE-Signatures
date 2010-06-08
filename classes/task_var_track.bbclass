python track_task_varrefs () {
    import bb.build
    import bb.data
    import bb.data_smart
    from collections import defaultdict
    from traceback import format_exc
    from pickle import UnpicklingError
    import shelve

    excluded = ("do_display_shelf", "do_write_signature",
                "do_write_signature_all")

    def monkey_patch(e):
        if "orig" in __builtins__ or e._task in excluded:
            return

        orig = bb.data_smart.DataSmart.getVar
        myGetVar = None
        def _myGetVar(data, name, expand = False):
            myGetVar.data[e._task].add(name)
            return orig(data, name, expand)
        __builtins__["orig"] = orig
        myGetVar = _myGetVar
        myGetVar.data = defaultdict(set)
        bb.data_smart.DataSmart.getVar = myGetVar

    def un_monkey_patch(e):
        if "orig" not in __builtins__ or e._task in excluded:
            return
        shelf = shelve.open("/home/kergoth/Code/oe/varrefs.shelf")
        if shelf.has_key(e._task):
            try:
                data = set(shelf[e._task])
            except UnpicklingError:
                data = set()
        else:
            data = set()

        data.update(bb.data_smart.DataSmart.getVar.data[e._task])
        shelf[e._task] = data
        shelf.close()
        bb.data_smart.DataSmart.getVar = __builtins__["orig"]
        del __builtins__["orig"]

    if isinstance(e, bb.build.TaskStarted):
        try:
            monkey_patch(e)
        except Exception, exc:
            bb.note("exception: %s" % exc)
            bb.note(format_exc(exc))
    elif isinstance(e, (bb.build.TaskSucceeded, bb.build.TaskFailed)):
        try:
            un_monkey_patch(e)
        except Exception, exc:
            bb.note("exception: %s" % exc)
            bb.note(format_exc(exc))
}
do_track_task_varrefs[lockfiles] = "${TOPDIR}/varrefs.lock"
addhandler track_task_varrefs

python do_display_shelf () {
    import shelve
    import kergoth
    shelf = shelve.open("/home/kergoth/Code/oe/varrefs.shelf")
    for var in d.keys():
        if d.getVarFlag(var, "task"):
            value = kergoth.new_value(var, d)
            if shelf.has_key(var):
                missing = shelf[var].difference(set(value.references))
                missing = set(filter(lambda x: not x.startswith('@'), missing))
                if missing:
                    bb.note("%s[varrefs] += \"%s\"" % (var, " ".join(missing)))
}
do_display_shelf[lockfiles] = "${TOPDIR}/varrefs.lock"
do_display_shelf[nostamp] = "1"
addtask display_shelf after do_build
