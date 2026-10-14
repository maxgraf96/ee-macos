# The Empire Earth host (app/ee_main.cpp), included from mods/CMakeLists.txt.
# POP_ROOT is the kit.
#
#   ee_boot   the whole game: the executable's translation and all eight DLLs'
#             (recomp_gen + every aux archive), booted from the PE entry point
#             through the kit's shared boot (host/boot.cpp), headless: the
#             kit's DirectDraw 7 on its software renderer, null sound and
#             input. docs/reference/boot.md.
if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB" AND TARGET recomp_gen_h_milesmix)
  add_executable(ee_boot ${CMAKE_CURRENT_LIST_DIR}/ee_main.cpp
                 ${CMAKE_CURRENT_LIST_DIR}/pending_win32.cpp
                 ${CMAKE_CURRENT_LIST_DIR}/pending_mss32.cpp
                 ${CMAKE_CURRENT_LIST_DIR}/pending_dmusic.cpp
                 ${POP_ROOT}/host/page_overlay.cpp)
  target_include_directories(ee_boot PRIVATE ${POP_ROOT} ${POP_ROOT}/runtime ${POP_ROOT}/host
                             ${CMAKE_BINARY_DIR}/generated)
  target_link_libraries(ee_boot PRIVATE recomp_platform recomp_runtime recomp_snapshot
                        recomp_dx recomp_mods lua host_common "-framework Foundation")
  target_compile_options(ee_boot PRIVATE ${POP_WARN_HOST})
  pop_optimize(ee_boot 1)
  pop_link_gen(ee_boot)
elseif(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB")
  message(STATUS "ee_boot: the translation lacks the eight DLLs; regenerate")
endif()
