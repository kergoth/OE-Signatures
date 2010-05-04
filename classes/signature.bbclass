# We need a hash of the metadata.  We cannot rely on the built in python
# hash() method, which means we need to use hashlib.  To use that, we need to
# supply it a bytestream/string, which means we should convert things into a
# string, which means we need a more intelligent repr().  We want to ensure
# that for objects which don't have a defined order, we show the arguments in
# the repr in a defined order.

BB_HASH_BLACKLIST += "__* *DIR *_DIR_* PATH PWD BBPATH FILE PARALLEL_MAKE"

python () {
    d.setVar("__RECIPEDATA", d)
}

#SIGNATURE = "${@kergoth.recipe_signature(d.getVar('__RECIPEDATA', d) or d)}"

python do_emit_signature () {
    import kergoth
    bb.note(kergoth.recipe_signature(d.getVar('__RECIPEDATA', False) or d))
}
do_emit_signature[nostamp] = "1"
addtask emit_signature

do_emit_signature_all[nostamp] = "1"
do_emit_signature_all[recrdeptask] = "do_emit_signature"
addtask emit_signature_all after emitsignature

python do_emit_data () {
    import pickle

    d = d.getVar("__RECIPEDATA", False) or d
    vars = {}
    flags = {}
    for key in d.keys():
        if not key.startswith("__"):
            vars[key] = d.getVar(key, False)
            flags[key] = d.getVarFlags(key)
    pickle.dump(vars, open(bb.data.expand("${TOPDIR}/signatures/${PF}.vars", d), "wb"))
    pickle.dump(flags, open(bb.data.expand("${TOPDIR}/signatures/${PF}.flags", d), "wb"))
}
do_emit_data[nostamp] = "1"
addtask emit_data
