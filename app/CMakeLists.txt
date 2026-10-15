# Empire Earth's hosts, included from mods/CMakeLists.txt. POP_ROOT is the kit.
#
#   recomp_app  the kit's windowed host (build/EmpireEarth.app: SDL window,
#               Metal Direct3D 7 renderer, keyboard and mouse), with
#               ee_standins.cpp linked in: the user directory and the
#               temporary stand-ins every Empire Earth host needs.
#               `tools/build.py --target app` builds it.
#   ee_boot     the same game booted headless: the kit's software Direct3D 7
#               renderer, frame dumps, a boot report and the boot test's
#               milestones (ee_main.cpp). docs/reference/boot.md.
set(EE_STANDIN_SOURCES ${CMAKE_CURRENT_LIST_DIR}/ee_standins.cpp
                       ${CMAKE_CURRENT_LIST_DIR}/pending_mss32.cpp)

if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB" AND TARGET recomp_gen_h_milesmix)
  add_executable(ee_boot ${CMAKE_CURRENT_LIST_DIR}/ee_main.cpp ${EE_STANDIN_SOURCES}
                 ${POP_ROOT}/host/page_overlay.cpp)
  target_include_directories(ee_boot PRIVATE ${POP_ROOT} ${POP_ROOT}/runtime ${POP_ROOT}/host
                             ${CMAKE_BINARY_DIR}/generated)
  target_link_libraries(ee_boot PRIVATE recomp_platform recomp_runtime recomp_snapshot
                        recomp_dx recomp_mods lua host_common "-framework Foundation")
  target_compile_options(ee_boot PRIVATE ${POP_WARN_HOST})
  pop_optimize(ee_boot 1)
  pop_link_gen(ee_boot)
endif()

if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB")
  # The kit's host directory is processed after this one, so the app target
  # does not exist yet: attach the stand-ins once the top-level directory is
  # done.
  # The call runs in the top-level scope, so the list travels as a global
  # property.
  set_property(GLOBAL PROPERTY EE_STANDIN_SOURCES ${EE_STANDIN_SOURCES})
  function(ee_attach_app_standins)
    get_property(sources GLOBAL PROPERTY EE_STANDIN_SOURCES)
    if(TARGET recomp_app)
      target_sources(recomp_app PRIVATE ${sources})
    endif()
  endfunction()
  cmake_language(DEFER DIRECTORY ${CMAKE_SOURCE_DIR} CALL ee_attach_app_standins)
  if(NOT TARGET recomp_gen_h_milesmix)
    message(STATUS "ee_boot: the translation lacks the eight DLLs; regenerate")
  endif()
endif()
