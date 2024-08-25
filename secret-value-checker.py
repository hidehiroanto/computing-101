import __main__ as checker
import random
import re

import chalconf #pylint:disable=import-error
addr_chain = getattr(chalconf, 'addr_chain', None)
secret_addr_reg = getattr(chalconf, 'secret_addr_reg', None)
secret_value_reg = getattr(chalconf, 'secret_value_reg', None)
value_offset = getattr(chalconf, 'value_offset', 0)
num_instructions = getattr(chalconf, 'num_instructions', 3)
final_reg_vals = getattr(chalconf, 'final_reg_vals', {})
must_set_regs = getattr(chalconf, 'must_set_regs', [])
must_get_regs = getattr(chalconf, 'must_get_regs', [])
secret_checks = getattr(chalconf, 'secret_checks', ['exit'])
secret_value = getattr(chalconf, 'secret_value', random.randint(15, 255))

#pylint:disable=global-statement

allow_asm = True
give_flag = True
returncode = None

assembly_prefix = ""
mapped_pages = set()
for n,_addr in enumerate(addr_chain):
	_page = _addr - _addr%0x1000
	assembly_prefix += "mov r9, 0x0; mov r8, 0xffffffff; mov r10, 0x32; mov rdx, 0x3; mov rsi, 0x1000;"
	assembly_prefix += f"mov rdi, {_page}; mov rax, 9; syscall;\n"
	try:
		assembly_prefix += f"mov qword ptr [{_addr}], {addr_chain[n+1]}\n"
	except IndexError:
		assembly_prefix += f"mov qword ptr [{_addr+value_offset}], {secret_value}\n"

if secret_addr_reg:
	assembly_prefix += f"mov {secret_addr_reg}, {addr_chain[0]}\n"

if secret_value_reg:
	assembly_prefix += f"mov {secret_value_reg}, {secret_value}\n"

if secret_value_reg is not None:
	check_runtime_prologue = f"""
Let's check what your exit code is! It should be our secret
value stored in register {secret_value_reg} (value {secret_value}) to succeed!
	""".strip()
elif secret_addr_reg and len(addr_chain) == 1:
	check_runtime_prologue = f"""
Let's check what your exit code is! It should be our secret
value pointed to by {secret_addr_reg} (value {secret_value}) to succeed!
	""".strip()
elif secret_addr_reg:
	check_runtime_prologue = f"""
Let's check what your exit code is! It should be our secret
value pointed to by a chain of pointers starting at {secret_addr_reg}!
	""".strip()
elif len(addr_chain) == 1:
	check_runtime_prologue = f"""
Let's check what your exit code is! It should be our secret value
stored at memory address {addr_chain[-1]} (value {secret_value}) to succeed!
	""".strip()
else:
	check_runtime_prologue = f"""
Let's check what your exit code is! It should be our secret
value pointed to by a chain of pointers starting at address {addr_chain[-1]}!
	""".strip()

check_runtime_success = """
Neat! Your program exited with the correct error code! You have
performed your first memory read. Great job!
""".strip()

check_runtime_failure = """
Your program exited with the wrong error code...
""".strip()

def check_disassembly(disas):
	mov_operands = [ d.op_str.split(", ") for d in disas if d.mnemonic == 'mov' ]

	set_regs, get_args = zip(*mov_operands)
	assert set(set_regs) >= set(must_set_regs), (
		"You must set each of the following registers (using the mov instruction):\n    "+", ".join(set_regs)
	)

	assert set(get_args) >= set(must_get_regs), (
		"You must get values from each of the following registers (using the\nmov instruction): "+", ".join(must_get_regs)
	)

	for r,vs in final_reg_vals.items():
		v = vs
		s = None
		if type(vs) in (tuple,list):
			v,s = vs
		assert ( [r,hex(v)] in mov_operands ), (
			f"You must properly set register {r} to the value {v}" +
			(f"\n({s})!" if s else "!")
		)
		last_mov_rax = max(i for i,m in enumerate(mov_operands) if m[0] == r)
		last_val_set = len(mov_operands) - mov_operands[::-1].index([r, hex(v)]) - 1
		assert last_mov_rax <= last_val_set, (
			f"You are overwriting the required value ({v}) that you need to put\n"
			"into 'rax'. You can use 'rax' for other stuff, but make sure to move\n"
			f"{v} into it afterwards!"
		)

	assert mov_operands.index(['rax','0x3c']) == max(
		i for i,m in enumerate(mov_operands) if m[0] == 'rax'
	), (
		"Uh oh! It looks like you're overwriting exit's syscall index (in rax) after\n"
		"setting it. If you overwrite it, then your eventual syscall instruction will\n"
		"trigger the wrong system call!"
	)

	if secret_addr_reg:
		try:
			idx_deref = max(i for i,m in enumerate(mov_operands) if secret_addr_reg in m[1] and "[" in m[1])
		except ValueError as e:
			raise AssertionError(
				"It looks like you never dereference the register with the secret\n"
				f"address ({secret_addr_reg})! You need to dereference it to read the\n"
				"required exit code!"
			) from e

		try:
			earliest_nonderef_overwrite = min(i for i,m in enumerate(mov_operands) if m[0] == secret_addr_reg and "[" not in m[1])
			assert earliest_nonderef_overwrite >= idx_deref, (
				f"Uh oh! It looks like you're overwriting the address in {secret_addr_reg} before\n"
				"dereferncing it. Once you overwrite this value, you will lose the secret\n"
				"address that we initialized it with! Dereference it first before overwriting\n"
				"it.\n"
			)
		except ValueError:
			pass

	all_derefs = [ m for m in mov_operands if "[" in m[1] ]
	for r,s in mov_operands:
		if r == 'rax' and s == hex(60):
			continue

		if len(all_derefs) == len(addr_chain):
			continue

		if '[' not in s and s.startswith("0x"):
			raise AssertionError(
				f"In the line 'mov {r}, {int(s,16)}', you are moving the _value_ {int(s,16)} into\n"
				f"{r}, rather than reading memory at the address {int(s,16)}. To read memory,\n"
				f"you must enclose the value in [], such as: [{int(s,16)}]."
			)
		if '[' not in s and re.match(r"[a-zA-Z]*", s):
			raise AssertionError(
				f"In the line 'mov {r}, {s}', you are moving the _value_ in register\n"
				f"{s} into {r}, rather than reading memory at the address pointed to by\n"
				"{s}. To read memory, you must enclose the register in [], such as: [{s}]."
			)

	#first_str = f"dereference {secret_addr_reg}" if secret_addr_reg else f"load memory from {addr_chain[0]}"
	#assert len(all_derefs) == len(addr_chain), (
	#	f"To retrieve the secret value in this level, you must do {len(all_derefs)}\n"
	#	"memory reads! First, " + first_str + "and then dereference the\n"
	#	f"loaded value {len(addr_chain)-1} times!\n"
	#) if len(addr_chain) > 1 else (
	#	f"To retrieve the secret value in this level, you must\n"+first_str+"!"
	#)

	operation = disas[-1].mnemonic
	assert operation == "syscall", (
		"Your last instruction should be the 'syscall' instruction to invoke\n"
		f"the exit system call, but you used the '{operation}' instruction!"
	)

	return True

def check_runtime(filename):
	global returncode
	#pylint:disable=c-extension-no-member

	try:
		print("")
		returncode = checker.dramatic_command(filename)
		checker.dramatic_command("echo $?", actual_command=f"echo {returncode}")
		assert returncode == secret_value
	finally:
		checker.dramatic_command("")
		print("")
