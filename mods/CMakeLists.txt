# Empire Earth's own targets in the kit's build (the kit adds this file when
# the game directory has one).
include(${CMAKE_CURRENT_LIST_DIR}/../tests/m1/harness/CMakeLists.txt)
include(${CMAKE_CURRENT_LIST_DIR}/../tests/m1/replay/CMakeLists.txt)
include(${CMAKE_CURRENT_LIST_DIR}/../tests/eh/CMakeLists.txt)
include(${CMAKE_CURRENT_LIST_DIR}/../tests/lan/plugin/CMakeLists.txt)
include(${CMAKE_CURRENT_LIST_DIR}/../app/CMakeLists.txt)

# The single-player checksum-stream oracle (P4-C, docs/reference/simparity.md):
# a mod plugin with its manifest in build/recomp/ee-mods/simparity/, which
# app/ee_standins.cpp names as the mod root when RECOMP_EE_CHECKSUM_TRACE is
# set. Built with ee_boot.
if(APPLE AND COMMAND pop_add_plugin)
  pop_add_plugin(ee_simparity SOURCE ${CMAKE_CURRENT_LIST_DIR}/simparity/simparity.c
                 OUTPUT_DIR ${POP_OUT}/ee-mods/simparity OUTPUT_NAME simparity
                 WARNINGS ${POP_WARN_HOST} OPTIONS -g)
  configure_file(${CMAKE_CURRENT_LIST_DIR}/simparity/mod.toml
                 ${POP_OUT}/ee-mods/simparity/mod.toml COPYONLY)
  if(TARGET ee_boot)
    add_dependencies(ee_boot ee_simparity)
  endif()
  # The keyboard, sound and frame-rate probe (P7-C, docs/reference/
  # performance.md): RECOMP_EE_INPUT_PROBE=FILE loads it the same way.
  pop_add_plugin(ee_inputprobe SOURCE ${CMAKE_CURRENT_LIST_DIR}/inputprobe/inputprobe.c
                 OUTPUT_DIR ${POP_OUT}/ee-mods/inputprobe OUTPUT_NAME inputprobe
                 WARNINGS ${POP_WARN_HOST} OPTIONS -g)
  configure_file(${CMAKE_CURRENT_LIST_DIR}/inputprobe/mod.toml
                 ${POP_OUT}/ee-mods/inputprobe/mod.toml COPYONLY)
  if(TARGET ee_boot)
    add_dependencies(ee_boot ee_inputprobe)
  endif()
endif()

# The performance probe (P7-B, docs/reference/performance.md): a mod plugin
# with no hooks, in build/recomp/ee-mods/perfprobe/. Loaded when
# RECOMP_MODS_DIR names that root and RECOMP_EE_PERF_TRACE is set.
if(APPLE AND COMMAND pop_add_plugin)
  pop_add_plugin(ee_perfprobe SOURCE ${CMAKE_CURRENT_LIST_DIR}/perfprobe/perfprobe.c
                 OUTPUT_DIR ${POP_OUT}/ee-mods/perfprobe OUTPUT_NAME perfprobe
                 WARNINGS ${POP_WARN_HOST} OPTIONS -g)
  configure_file(${CMAKE_CURRENT_LIST_DIR}/perfprobe/mod.toml
                 ${POP_OUT}/ee-mods/perfprobe/mod.toml COPYONLY)
  if(TARGET ee_boot)
    add_dependencies(ee_boot ee_perfprobe)
  endif()
endif()
