BB_HASH_OUTPUT ?= "${TMPDIR}/signatures/${PF}.${SIGNATURE}"

BB_HASH_BLACKLIST += "__* *DIR *_DIR_* PATH PWD BBPATH FILE PARALLEL_MAKE"
BB_HASH_BLACKLSIT += "SIGNATURE"

# These are blacklisted due to python exceptions
BB_HASH_BLACKLIST += "PYTHON_DIR"

python () {
    import kergoth
    if d.getVar("__RUNQUEUE_DO_NOT_USE_EXTERNALLY", False):
        try:
            value = kergoth.Signature(d)
            d.setVar("__SIGNATURE", value)
            d.setVar("SIGNATURE", str(value))
        except Exception, exc:
            from traceback import format_exc
            bb.fatal(format_exc(exc))

    if d.getVar("BB_HASH_DEBUG", True):
        deps = d.getVarFlag("do_build", "deps") or []
        d.setVarFlag("do_build", "deps", deps + ["do_write_signature_all"])
}

python do_write_signature () {
    import kergoth
    items = d.getVar("__SIGNATURE", False)
    output = d.getVar("BB_HASH_OUTPUT", True)
    if output:
        bb.mkdirhier(os.path.dirname(output))
        f = open(output, "w")
        for key, value in sorted(items.data.iteritems()):
            f.write("%s = %s\n" % (key, kergoth.stable_repr(value)))
}
addtask write_signature

do_write_signature_all[recrdeptask] = "do_write_signature"
addtask write_signature_all after do_write_signature
