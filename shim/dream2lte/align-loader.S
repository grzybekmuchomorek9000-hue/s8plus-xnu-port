/* SPDX-License-Identifier: GPL-2.0-only
 * The C loader uses ADRP and has an aligned exception table. S-Boot may
 * load a BOOT kernel on a 2 KiB boundary. Copy the inner Image to the
 * next 4 KiB boundary inside this Image before entering it.
 */
	.section .text.head, "ax"
	.global _head
_head:
	b align_loader
	.long 0
	.quad 0x80000
	.quad loader_end - _head + 4096
	.quad 0xa
	.quad 0, 0, 0
	.ascii "ARM\x64"
	.long 0

align_loader:
	adr x9, _head		/* Report S-Boot's original load address. */
	adr x1, loader_image
	add x2, x1, #4095
	and x2, x2, #-4096
	cmp x1, x2
	b.eq enter

	/* Copy to a higher address, starting at the end so overlap is safe.
	 * Leave x0 intact: it carries S-Boot's device tree. */
	adr x4, loader_end
	sub x3, x4, x1
	add x5, x2, x3
	add x6, x2, x3
copy:
	ldr x7, [x4, #-8]!
	str x7, [x5, #-8]!
	cmp x4, x1
	b.hi copy

	mrs x3, ctr_el0
	ubfx x3, x3, #16, #4
	mov x4, #4
	lsl x4, x4, x3
	mov x5, x2
clean:
	dc cvac, x5
	add x5, x5, x4
	cmp x5, x6
	b.lo clean
	dsb sy
	ic iallu
	dsb sy
	isb
enter:
	br x2

	.balign 16
loader_image:
	.incbin INNER_IMAGE_PATH
loader_end:
