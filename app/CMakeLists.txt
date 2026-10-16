# Empire Earth's hosts, included from mods/CMakeLists.txt. POP_ROOT is the kit.
#
#   recomp_app  the kit's windowed host (build/EmpireEarth.app: SDL window,
#               Metal Direct3D 7 renderer, keyboard and mouse), with
#               ee_standins.cpp linked in: the user directory and the
#               temporary stand-ins every Empire Earth host needs.
#               `tools/build.py --target app` builds it. Never distributed:
#               it links the translation.
#   recomp_player  the same host with no translation (kit host/code_dir.h:
#               RECOMP_CODE_DIR names the bundles the first run built), and
#               the same stand-ins: build/player/EmpireEarth.app, which
#               tools/package_app.py turns into the shareable app.
#   ee_boot     the same game booted headless: the kit's software Direct3D 7
#               renderer, frame dumps, a boot report, the boot test's
#               milestones and scripted input (ee_main.cpp --script, the
#               menu test). docs/reference/boot.md, docs/reference/menus.md.
set(EE_STANDIN_SOURCES ${CMAKE_CURRENT_LIST_DIR}/ee_standins.cpp
                       ${CMAKE_CURRENT_LIST_DIR}/ee_assertions.cpp
                       ${CMAKE_CURRENT_LIST_DIR}/ee_sched.cpp
                       ${CMAKE_CURRENT_LIST_DIR}/ee_input.cpp
                       ${CMAKE_CURRENT_LIST_DIR}/ee_sort.cpp)

if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB" AND TARGET recomp_gen_h_milesmix)
  # The kit's input state and scripted input (host/input_script.h) with the
  # headless input path; miniz writes the script's PNG frames.
  add_executable(ee_boot ${CMAKE_CURRENT_LIST_DIR}/ee_main.cpp ${EE_STANDIN_SOURCES}
                 ${POP_ROOT}/host/page_overlay.cpp ${POP_ROOT}/host/input.cpp
                 ${POP_ROOT}/host/input_script.cpp ${POP_ROOT}/host/input_script_host.cpp
                 $<TARGET_OBJECTS:recomp_miniz>)
  target_include_directories(ee_boot PRIVATE ${POP_ROOT} ${POP_ROOT}/runtime ${POP_ROOT}/host
                             ${CMAKE_BINARY_DIR}/generated)
  target_link_libraries(ee_boot PRIVATE recomp_platform recomp_runtime recomp_snapshot
                        recomp_dx recomp_mods lua host_common "-framework Foundation")
  target_compile_options(ee_boot PRIVATE ${POP_WARN_HOST})
  pop_optimize(ee_boot 1)
  pop_link_gen(ee_boot)
endif()

# The stand-ins go into both windowed hosts: the developer's recomp_app (the
# translation linked in) and the player host recomp_player (none linked: the
# shareable app's, tools/package_app.py, built in a tree with no translation
# at all). The kit's host directory is processed after this one, so neither
# target exists yet: attach the stand-ins once the top-level directory is
# done. The call runs in the top-level scope, so the list travels as a global
# property.
if(APPLE)
  set_property(GLOBAL PROPERTY EE_STANDIN_SOURCES ${EE_STANDIN_SOURCES})
  function(ee_attach_app_standins)
    get_property(sources GLOBAL PROPERTY EE_STANDIN_SOURCES)
    foreach(target recomp_app recomp_player)
      if(TARGET ${target})
        target_sources(${target} PRIVATE ${sources})
      endif()
    endforeach()
  endfunction()
  cmake_language(DEFER DIRECTORY ${CMAKE_SOURCE_DIR} CALL ee_attach_app_standins)
endif()

if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB" AND NOT TARGET recomp_gen_h_milesmix)
  message(STATUS "ee_boot: the translation lacks the eight DLLs; regenerate")
endif()
