/* SPDX-License-Identifier: MIT
 * Offline compiler fixture for a single-invocation G71 memory-copy shader.
 * GPU VAs refer to the driver's private mapped test page, not CPU addresses.
 */
#include <stdio.h>
#include "compiler/glsl_types.h"
#include "compiler/nir/nir_builder.h"
#include "panfrost/compiler/pan_compiler.h"
#include "util/u_dynarray.h"

int main(int argc, char **argv)
{
   if (argc != 2) {
      fprintf(stderr, "usage: %s OUTPUT.bin\n", argv[0]);
      return 2;
   }
   glsl_type_singleton_init_or_ref();
   struct pan_compile_inputs inputs = {.gpu_id = 0x60a00001};
   const nir_shader_compiler_options *options =
      pan_get_nir_shader_compiler_options(6, false);
   nir_builder builder = nir_builder_init_simple_shader(
      MESA_SHADER_COMPUTE, options, "s8-g71-copy16");
   for (unsigned i = 0; i < 3; ++i)
      builder.shader->info.workgroup_size[i] = 1;
   nir_def *source = nir_imm_int64(&builder, 0x2000);
   nir_def *destination = nir_imm_int64(&builder, 0x2200);
   nir_def *value = nir_load_global(&builder, 4, 32, source, .align_mul = 16);
   nir_store_global(&builder, value, destination, .align_mul = 16, .write_mask = 15);
   nir_validate_shader(builder.shader, "s8 GPU copy input");
   pan_preprocess_nir(builder.shader, inputs.gpu_id);
   struct pan_shader_info info = {0};
   pan_postprocess_nir(builder.shader, &inputs, &info);
   struct util_dynarray binary;
   util_dynarray_init(&binary, NULL);
   pan_shader_compile(builder.shader, &inputs, &binary, &info);
   if (!binary.size || info.tls_size || info.wls_size) {
      fprintf(stderr, "unexpected shader size or private memory requirements\n");
      return 1;
   }
   FILE *output = fopen(argv[1], "wb");
   if (!output) return 1;
   bool ok = fwrite(binary.data, 1, binary.size, output) == binary.size;
   ok = fclose(output) == 0 && ok;
   printf("{\"gpu_id\":\"0x60a00001\",\"bytes\":%u,\"work_registers\":%u,"
          "\"preload\":\"0x%llx\",\"tls\":%u,\"wls\":%u}\n",
          binary.size, info.work_reg_count, (unsigned long long)info.preload,
          info.tls_size, info.wls_size);
   util_dynarray_fini(&binary);
   ralloc_free(builder.shader);
   glsl_type_singleton_decref();
   return ok ? 0 : 1;
}
