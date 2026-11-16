/* SPDX-License-Identifier: GPL-2.0-only
 * SM-G955F display test. S-Boot hands off an initialized 1440x2960
 * framebuffer. Show RGB bands without starting XNU or touching UFS.
 */
	.section .text.head, "ax"
	.global _head
_head:
	b probe
	.long 0
	.quad 0x80000
	.quad image_end - _head
	.quad 0xa
	.quad 0, 0, 0
	.ascii "ARM\x64"
	.long 0

probe:
	msr daifset, #0xf
	mrs x0, CurrentEL
	cmp x0, #4
	b.ne park
	mrs x0, sctlr_el1
	tbnz x0, #0, park

	mov x0, #0xcc000000
	mov x1, x0
	mov w3, #0
row:
	mov w4, #0xffff0000
	cmp w3, #987
	b.lo pixels
	mov w4, #0xff00
	movk w4, #0xff00, lsl #16
	cmp w3, #1974
	b.lo pixels
	mov w4, #0xff
	movk w4, #0xff00, lsl #16
pixels:
	mov w5, #1440
pixel:
	str w4, [x0], #4
	subs w5, w5, #1
	b.ne pixel
	add w3, w3, #1
	cmp w3, #2960
	b.lo row

	/* Clean the written scanout lines to the point of coherency. */
	mrs x2, ctr_el0
	ubfx x2, x2, #16, #4
	mov x3, #4
	lsl x3, x3, x2
clean:
	dc cvac, x1
	add x1, x1, x3
	cmp x1, x0
	b.lo clean
	dsb sy

	/* Same DECON register and mode as the donor's Exynos 8895 path. */
	mov x0, #0x0070
	movk x0, #0x1286, lsl #16
	mov w1, #0x1281
	str w1, [x0]
	dsb sy
park:
	wfe
	b park
image_end:
