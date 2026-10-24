# Portable DSP/ML core. src/host_api.c is host-only (ctypes tests) and not built here.
idf_component_register(
    SRCS src/dsp.c src/fama_net.c src/tier_b.c src/pipeline.c src/proto.c
    INCLUDE_DIRS include)
target_compile_options(${COMPONENT_LIB} PRIVATE -O2 -ffast-math -fno-finite-math-only)
