# The M1 replay on the Mac (rmg_replay_mac.cpp, docs/reference/m1-replay.md),
# included from mods/CMakeLists.txt after the M1 harness, whose readiness
# check (_ee_m1_ready) and bundle target (ee_m1_bundles) it reuses.
#
#   ee_m1_replay         dlopen()s the M1 modules' freestanding bundles
#   ee_m1_replay_static  links their generated archives instead
#
# Both take packs to replay, or --record OUT to make a capture on the Mac.
set(EE_M1_REPLAY_SOURCES
    ${CMAKE_CURRENT_LIST_DIR}/rmg_replay_mac.cpp
    ${CMAKE_CURRENT_LIST_DIR}/rmg_record_mac.cpp
    ${CMAKE_CURRENT_LIST_DIR}/rmg_mac_common.cpp
    # The portable core, shared with the Windows replay (C99).
    ${CMAKE_CURRENT_LIST_DIR}/rmg_replay_core.c)

function(ee_m1_replay_target target)
  add_executable(${target} ${EE_M1_REPLAY_SOURCES})
  target_include_directories(${target} PRIVATE ${CMAKE_CURRENT_FUNCTION_LIST_DIR} ${POP_ROOT}
                             ${POP_ROOT}/runtime ${CMAKE_BINARY_DIR}/generated)
  target_link_libraries(${target} PRIVATE recomp_platform recomp_runtime recomp_dx_null)
  target_compile_options(${target} PRIVATE $<$<COMPILE_LANGUAGE:CXX>:${POP_WARN_HOST}>
                         $<$<COMPILE_LANGUAGE:C>:-std=c99 -Wall -Wextra>)
  pop_optimize(${target} 1)
  target_link_libraries(${target} PRIVATE "-framework Foundation")
endfunction()

if(_ee_m1_ready)
  ee_m1_replay_target(ee_m1_replay)
  target_compile_definitions(ee_m1_replay PRIVATE EE_M1_BUNDLES=1)
  add_dependencies(ee_m1_replay ee_m1_bundles)

  ee_m1_replay_target(ee_m1_replay_static)
  foreach(key ${EE_M1_KEYS})
    target_link_libraries(ee_m1_replay_static PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,recomp_gen_${key}>")
  endforeach()
endif()
