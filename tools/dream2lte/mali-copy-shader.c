/* SPDX-License-Identifier: MIT
 * Compile fixed G71 operations against Mesa's Bifrost backend.
 * GPU addresses are assigned by the driver within its own page tables.
 */
#include <stdio.h>
#include <string.h>
#include "compiler/glsl_types.h"
#include "compiler/nir/nir_builder.h"
#include "panfrost/compiler/pan_compiler.h"
#include "panfrost/lib/pan_encoder.h"
#include "panfrost/lib/pan_shader.h"
#include "util/u_dynarray.h"

/* Premultiplied 8-bit BGRA source-over. The multiply and shift implement
 * round(channel * (255 - source_alpha) / 255) for products up to 65025. */
static nir_def *source_over(nir_builder *b, nir_def *source, nir_def *below)
{
   nir_def *alpha = nir_ushr_imm(b, source, 24);
   nir_def *inverse = nir_isub(b, nir_imm_int(b, 255), alpha);
   nir_def *packed = nir_imm_int(b, 0);
   for (unsigned shift = 0; shift < 32; shift += 8) {
      nir_def *front = nir_iand_imm(b, nir_ushr_imm(b, source, shift), 255);
      nir_def *back = nir_iand_imm(b, nir_ushr_imm(b, below, shift), 255);
      nir_def *product = nir_iadd_imm(b, nir_imul(b, back, inverse), 128);
      nir_def *scaled = nir_ushr_imm(b, nir_iadd(b, product, nir_ushr_imm(b, product, 8)), 8);
      nir_def *sum = nir_umin(b, nir_iadd(b, front, scaled), nir_imm_int(b, 255));
      packed = nir_ior(b, packed, nir_ishl_imm(b, sum, shift));
   }
   return packed;
}

/* The driver owns the parameter block at 0x1700. Each lane handles one
 * pixel and checks the dimensions at partial workgroups. */
static void emit_rectangle(nir_builder *b, bool blend)
{
   b->shader->info.workgroup_size[0] = 8;
   b->shader->info.workgroup_size[1] = 4;
   nir_def *xy = nir_load_global_invocation_id(b, 32);
   nir_def *x = nir_channel(b, xy, 0), *y = nir_channel(b, xy, 1);
   nir_def *layout = nir_load_global(b, 4, 32, nir_imm_int64(b, 0x1700), .align_mul = 16);
   nir_def *extent = nir_load_global(b, blend ? 4 : 2, 32,
                                    nir_imm_int64(b, 0x1710), .align_mul = 8);
   nir_if *inside = nir_push_if(b, nir_iand(b, nir_ult(b, x, nir_channel(b, extent, 0)),
                                               nir_ult(b, y, nir_channel(b, extent, 1))));
   nir_def *column = nir_imul_imm(b, x, 4);
   nir_def *src = nir_iadd(b, nir_channel(b, layout, 0),
                           nir_iadd(b, column, nir_imul(b, y, nir_channel(b, layout, 2))));
   nir_def *dst = nir_iadd(b, nir_channel(b, layout, 1),
                           nir_iadd(b, column, nir_imul(b, y, nir_channel(b, layout, 3))));
   nir_def *pixel = nir_load_global(b, 1, 32, nir_u2u64(b, src), .align_mul = 4);
   if (blend) {
      nir_def *below_offset = nir_iadd(b, column, nir_imul(b, y, nir_channel(b, extent, 3)));
      nir_def *below_addr = nir_iadd(b, nir_channel(b, extent, 2), below_offset);
      nir_def *below = nir_load_global(b, 1, 32, nir_u2u64(b, below_addr), .align_mul = 4);
      pixel = source_over(b, pixel, below);
   }
   nir_store_global(b, pixel, nir_u2u64(b, dst), .align_mul = 4, .write_mask = 1);
   nir_pop_if(b, inside);
}

/* Independent packet reference from Mesa's v6 packers and the actual
 * compiler info. The Windows test compares this with the running ARM code. */
static bool reference(FILE *f, struct pan_shader_info *info, unsigned w, unsigned h,
                      unsigned bx, unsigned by)
{
   uint32_t job[pan_size(COMPUTE_JOB) / 4] = {0};
   struct mali_renderer_state_packed rsd;
   struct mali_local_storage_packed tls;
   pan_section_pack(job, COMPUTE_JOB, HEADER, cfg) {
      cfg.type = MALI_JOB_TYPE_COMPUTE;
      cfg.index = 1;
   }
   pan_pack_work_groups_compute(pan_section_ptr(job, COMPUTE_JOB, INVOCATION),
      DIV_ROUND_UP(w, bx), DIV_ROUND_UP(h, by), 1, bx, by, 1, false, false);
   pan_section_pack(job, COMPUTE_JOB, PARAMETERS, cfg) {
      cfg.job_task_split = util_logbase2_ceil(bx + 1) + util_logbase2_ceil(by + 1) + 1;
   }
   pan_section_pack(job, COMPUTE_JOB, DRAW, cfg) {
      cfg.state = 0x1500;
      cfg.thread_storage = 0x1600;
   }
   pan_pack(&rsd, RENDERER_STATE, cfg) {
      pan_shader_prepare_rsd(info, 0x1800, &cfg);
   }
   pan_pack(&tls, LOCAL_STORAGE, cfg) {
      cfg.wls_instances = MALI_LOCAL_STORAGE_NO_WORKGROUP_MEM;
   }
   return fwrite(job, 1, sizeof(job), f) == sizeof(job) &&
          fwrite(&rsd, 1, sizeof(rsd), f) == sizeof(rsd) &&
          fwrite(&tls, 1, sizeof(tls), f) == sizeof(tls);
}

int main(int argc, char **argv)
{
   bool blend = argc >= 3 && !strcmp(argv[2], "blend");
   bool rectangle = blend || (argc >= 3 && !strcmp(argv[2], "rectangle"));
   if (argc < 2 || argc > 4 || (argc >= 3 && !rectangle && strcmp(argv[2], "copy16"))) {
      fprintf(stderr, "usage: %s OUTPUT.bin [copy16|rectangle|blend [reference.bin]]\n", argv[0]);
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
   if (rectangle) {
      emit_rectangle(&builder, blend);
   } else {
      nir_def *source = nir_imm_int64(&builder, 0x2000);
      nir_def *destination = nir_imm_int64(&builder, 0x2200);
      nir_def *value = nir_load_global(&builder, 4, 32, source, .align_mul = 16);
      nir_store_global(&builder, value, destination, .align_mul = 16, .write_mask = 15);
   }
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
   if (argc == 4) {
      FILE *f = fopen(argv[3], "wb");
      if (!f) return 1;
      if (rectangle) {
         ok = reference(f, &info, 13, 7, 8, 4) && ok;
         ok = reference(f, &info, 1440, 2960, 8, 4) && ok;
      } else {
         ok = reference(f, &info, 1, 1, 1, 1) && ok;
      }
      ok = fclose(f) == 0 && ok;
      pan_disassemble(stderr, binary.data, binary.size, inputs.gpu_id, false);
   }
   printf("{\"gpu_id\":\"0x60a00001\",\"bytes\":%u,\"work_registers\":%u,"
          "\"preload\":\"0x%llx\",\"tls\":%u,\"wls\":%u}\n",
          binary.size, info.work_reg_count, (unsigned long long)info.preload,
          info.tls_size, info.wls_size);
   util_dynarray_fini(&binary);
   ralloc_free(builder.shader);
   glsl_type_singleton_decref();
   return ok ? 0 : 1;
}
