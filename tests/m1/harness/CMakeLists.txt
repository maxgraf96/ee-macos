# The M1 harness (m1_harness.cpp), included from mods/CMakeLists.txt, which
# the kit adds to its build. POP_ROOT is the kit; paths here are relative to
# this file, so a derived game directory can include it as well.
#
#   ee_m1_harness         dlopen()s the M1 modules' freestanding bundles (ee_m1_bundles)
#   ee_m1_harness_static  links their generated archives instead
#   ee_m1_bundles         bundle.py for the three modules only (not the EXE)
#
# Neither harness links the executable's translation (recomp_gen): the
# runtime's dispatch serves the registered modules.
set(EE_M1_KEYS a_sxlrt233 b_engine c_default)
set(EE_M1_SOURCES ${CMAKE_CURRENT_LIST_DIR}/m1_harness.cpp
    # TEMPORARY until the kit's msvcrt lands (feat/crt).
    ${CMAKE_CURRENT_LIST_DIR}/crt_stopgap.cpp)
set(EE_M1_LIBS recomp_platform recomp_runtime recomp_dx_null)

function(ee_m1_target target)
  add_executable(${target} ${EE_M1_SOURCES})
  target_include_directories(${target} PRIVATE ${POP_ROOT} ${POP_ROOT}/runtime ${CMAKE_BINARY_DIR}/generated)
  target_link_libraries(${target} PRIVATE ${EE_M1_LIBS})
  target_compile_options(${target} PRIVATE ${POP_WARN_HOST})
  pop_optimize(${target} 1)
  if(APPLE)
    target_link_libraries(${target} PRIVATE "-framework Foundation")
  endif()
endfunction()

if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB")
  set(_ee_m1_ready ON)
  foreach(key ${EE_M1_KEYS})
    if(NOT TARGET recomp_gen_${key})
      set(_ee_m1_ready OFF)
    endif()
  endforeach()
  if(_ee_m1_ready)
    ee_m1_target(ee_m1_harness)
    target_compile_definitions(ee_m1_harness PRIVATE EE_M1_BUNDLES=1)
    recomp_add_bundles(ee_m1_bundles MODULES ${EE_M1_KEYS})
    add_dependencies(ee_m1_harness ee_m1_bundles)

    ee_m1_target(ee_m1_harness_static)
    foreach(key ${EE_M1_KEYS})
      target_link_libraries(ee_m1_harness_static PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,recomp_gen_${key}>")
    endforeach()
  else()
    message(STATUS "M1 harness: the translation lacks ${EE_M1_KEYS}; regenerate")
  endif()
endif()
