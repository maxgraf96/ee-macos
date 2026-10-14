# The M1 harness (m1_harness.cpp), included from mods/CMakeLists.txt, which
# the kit adds to its build. POP_ROOT is the kit; paths are relative to this file.
#
#   ee_m1_harness         dlopen()s the M1 modules' freestanding bundles (ee_m1_bundles)
#   ee_m1_harness_static  links their generated archives instead
#   ee_m1_bundles         bundle.py for the three modules only (not the EXE)
#
# Neither harness links the executable's translation (recomp_gen): the
# runtime's dispatch serves the registered modules.
set(EE_M1_KEYS a_sxlrt233 b_engine c_default)
set(EE_M1_SOURCES ${CMAKE_CURRENT_LIST_DIR}/m1_harness.cpp
    # --um: ADR-002 criterion 2, the engine's maths against the recorded calls.
    ${CMAKE_CURRENT_LIST_DIR}/um_vectors.cpp
    # Stand-ins for msvcrt functions the kit lacked; registered before imports_init, so
    # the kit's own shims (x87 control word, EH, now all present) take precedence.
    ${CMAKE_CURRENT_LIST_DIR}/crt_pending.cpp)
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

    # ee_um_harness: the static harness with the executable's translation
    # linked too, so --um also reaches the executable's own maths helpers
    # (ADR-002 criterion 2). Built only on request: it compiles the EXE.
    ee_m1_target(ee_um_harness)
    target_compile_definitions(ee_um_harness PRIVATE EE_UM_EXE=1)
    pop_link_gen(ee_um_harness)
  else()
    message(STATUS "M1 harness: the translation lacks ${EE_M1_KEYS}; regenerate")
  endif()
endif()
