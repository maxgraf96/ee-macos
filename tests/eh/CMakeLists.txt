# The EH harness (eh_harness.cpp): Empire Earth's own C++ exceptions on the
# executable's translation, through the kit's MSVC exception handling.
# Included from mods/CMakeLists.txt; POP_ROOT is the kit.
#
#   ee_eh_harness   dlopen()s the bundles of sxlrt233, the engine and the EXE
#   ee_eh_bundles   bundle.py for those three (the EXE's is the big one)
if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB"
   AND TARGET recomp_gen_a_sxlrt233 AND TARGET recomp_gen_b_engine)
  add_executable(ee_eh_harness ${CMAKE_CURRENT_LIST_DIR}/eh_harness.cpp
                 ${CMAKE_CURRENT_LIST_DIR}/../m1/harness/crt_pending.cpp)
  target_include_directories(ee_eh_harness PRIVATE ${POP_ROOT} ${POP_ROOT}/runtime
                             ${CMAKE_BINARY_DIR}/generated)
  target_link_libraries(ee_eh_harness PRIVATE recomp_platform recomp_runtime recomp_dx_null
                        "-framework Foundation")
  target_compile_options(ee_eh_harness PRIVATE ${POP_WARN_HOST})
  pop_optimize(ee_eh_harness 1)
  recomp_add_bundles(ee_eh_bundles MODULES main a_sxlrt233 b_engine)
  add_dependencies(ee_eh_harness ee_eh_bundles)
endif()
