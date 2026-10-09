/*
 * FEXCore references rpm_cas_snapshot_take unconditionally — Core.cpp declares
 * it and calls it from CompileBlock's periodic sampler (the ml622 rpmalloc CAS
 * diagnostic), with no guard. The definition only exists in the rpmalloc fork
 * (FEX/External/rpmalloc, willfaust/rpmalloc branch ios-madeira, in
 * rpmalloc/rpmalloc.c), and nothing here can supply it from there:
 *
 *   - FEX's CMakeLists skips add_subdirectory(External/rpmalloc) on APPLE,
 *     because it forces ENABLE_FEX_ALLOCATOR=FALSE for the whole platform ("Apple
 *     platform detected — disabling jemalloc and rpmalloc"), so the target is
 *     never configured for this build.
 *   - The pinned rpmalloc.c does not compile for iOS anyway: its diagnostics call
 *     WriteFile/GetStdHandle with no #ifdef around them (rpmalloc.c:1932 and
 *     :3169), which only resolve in a Windows build.
 *
 * With the FEX allocator disabled there is no rpmalloc state to publish a
 * snapshot, and 0 is exactly what the real function returns when none is
 * pending ("if (!rpm_cas_snap_ready) return 0;"). So this reports "nothing to
 * drain", which is the truth for this configuration, and keeps the link closed.
 */
int
rpm_cas_snapshot_take(void *out) {
    (void)out;
    return 0;
}
