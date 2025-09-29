# In build_scripts.py

import subprocess
import os
import sys

# --- Configuration ---
PROTO_DIR = "../protos"
PROTO_FILE = os.path.join(PROTO_DIR, "mcs.proto")
OUTPUT_DIR = "../ModuleContextStreaming"
GENERATED_GRPC_FILE = os.path.join(OUTPUT_DIR, "mcs_pb2_grpc.py")


def build():
	"""Generates gRPC code and patches the import statement."""
	print("🚀 Starting gRPC code generation...")

	# Step 1: Run the protoc command
	protoc_command = [
		sys.executable,  # Use the current python interpreter
		"-m",
		"grpc_tools.protoc",
		f"-I{PROTO_DIR}",
		f"--python_out={OUTPUT_DIR}",
		f"--grpc_python_out={OUTPUT_DIR}",
		PROTO_FILE,
	]

	try:
		subprocess.run(protoc_command, check=True)
		print("✅ Code generated successfully.")
	except (subprocess.CalledProcessError, FileNotFoundError) as e:
		print(f"❌ Error during code generation: {e}")
		return

	# Step 2: Patch the generated file for relative imports
	print(f"🔧 Patching import statement in {GENERATED_GRPC_FILE}...")

	try:
		with open(GENERATED_GRPC_FILE, "r") as f:
			content = f.read()

		# The find-and-replace logic
		incorrect_import = "import mcs_pb2 as mcs__pb2"
		correct_import = "from . import mcs_pb2 as mcs__pb2"

		if incorrect_import in content:
			content = content.replace(incorrect_import, correct_import)
			with open(GENERATED_GRPC_FILE, "w") as f:
				f.write(content)
			print("✅ Patch applied successfully.")
		else:
			print("👍 No patch needed, import statement is already correct.")

	except FileNotFoundError:
		print(f"❌ Could not find generated file to patch: {GENERATED_GRPC_FILE}")
	except Exception as e:
		print(f"❌ An error occurred during patching: {e}")


if __name__ == "__main__":
	build()