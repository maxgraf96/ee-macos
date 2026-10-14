# The TCP Protocol.dll harness (tcp_plugin_harness.cpp), included from
# mods/CMakeLists.txt. It links the generated archives of the three modules it
# runs (sxlrt233, the engine, the plugin) and not the executable's.
set(EE_LAN_KEYS a_sxlrt233 b_engine g_tcp)
if(APPLE AND POP_HAVE_GEN AND NOT POP_TRANSLATE STREQUAL "STUB")
  set(_ee_lan_ready ON)
  foreach(key ${EE_LAN_KEYS})
    if(NOT TARGET recomp_gen_${key})
      set(_ee_lan_ready OFF)
    endif()
  endforeach()
  if(_ee_lan_ready)
    add_executable(ee_tcp_plugin_harness ${CMAKE_CURRENT_LIST_DIR}/tcp_plugin_harness.cpp)
    target_include_directories(ee_tcp_plugin_harness PRIVATE ${POP_ROOT} ${POP_ROOT}/runtime
                               ${CMAKE_BINARY_DIR}/generated)
    target_link_libraries(ee_tcp_plugin_harness PRIVATE recomp_platform recomp_runtime recomp_dx_null)
    foreach(key ${EE_LAN_KEYS})
      target_link_libraries(ee_tcp_plugin_harness PRIVATE
                            "$<LINK_LIBRARY:WHOLE_ARCHIVE,recomp_gen_${key}>")
    endforeach()
    target_compile_options(ee_tcp_plugin_harness PRIVATE ${POP_WARN_HOST})
    pop_optimize(ee_tcp_plugin_harness 1)
    target_link_libraries(ee_tcp_plugin_harness PRIVATE "-framework Foundation")
  else()
    message(STATUS "TCP plugin harness: the translation lacks ${EE_LAN_KEYS}; regenerate")
  endif()
endif()
