# ModuleContextStreaming (MCS) 🚀

A high-performance gRPC service designed to provide real-time, streamable context to Large Language Models (LLMs). This project serves as a robust backend for AI applications that require secure, low-latency communication with server-side tools and resources.

-----

## Features

  * **High-Performance Streaming:** Built on gRPC and HTTP/2 for efficient, multiplexed, and low-latency data streaming.
  * **Strict API Contract:** Uses Protocol Buffers (`.proto`) as the single source of truth for the API, with code generation for type-safe communication.
  * **Authentication-Ready:** Designed for secure communication with JWT-based authentication via Keycloak (implementation pending).
  * **Asynchronous-Capable:** The gRPC foundation allows for highly concurrent and scalable server implementations.

-----

You are absolutely right. Thank you for the correction. The `README.md` should reflect your actual project structure.

Here is the corrected "Project Structure" section. You can replace the old one in your `README.md` with this updated version.

-----

## Project Structure

The project is organized with the root directory containing the Python package, API definitions, and supporting files.

```
ModuleContextStreaming/         # The project root directory
├── ModuleContextStreaming/     # The main, installable Python package
│   ├── __init__.py
│   ├── server.py
│   ├── client.py
│   ├── auth.py
│   ├── exceptions.py
│   ├── mcs_pb2.py
│   └── mcs_pb2_grpc.py
├── protos/
│   └── mcs.proto             # The API contract source of truth
├── examples/
│   ├── simple_server.py      # Example runnable server (To-Do)
│   └── simple_client.py      # Example runnable client (To-Do)
├── tests/
│   └── ...                   # Unit and integration tests (To-Do)
├── build.py                  # Script to generate and patch gRPC code
├── DESIGN.md                 # Your design document
├── pyproject.toml            # Project metadata and dependencies
└── README.md
```

-----

## Setup and Installation

Follow these steps to set up your local development environment.

### \#1. Prerequisites

  * Python 3.10+
  * Git

### \#2. Installation Steps

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/armstrongsam25/ModuleContextStreaming.git
    cd ModuleContextStreaming
    ```

2.  **Create and activate a virtual environment:**

    ```bash
    # Create the virtual environment
    python -m venv .venv

    # Activate it (Windows)
    .\.venv\Scripts\activate

    # Activate it (macOS/Linux)
    source .venv/bin/activate
    ```

3.  **Install dependencies:**
    This command installs the project in editable mode (`-e`) along with all core and development dependencies.

    ```bash
    pip install -e .[dev]
    ```

4.  **Generate gRPC Code:**
    This is the final setup step. Run the `build.py` script to compile the `.proto` file and automatically apply the necessary import patch.

    ```bash
    python build_scripts.py
    ```

    You only need to re-run this command when you make changes to the `protos/mcs.proto` file.

-----

## Usage

To test the client-server communication, you will need two separate terminals.

1.  **Start the Server:**
    In your first terminal (with the virtual environment activated), run:

    ```bash
    python -m ModuleContextStreaming.server
    ```

    You should see the message: `✅ Server started, listening on port 50051.`

2.  **Run the Client:**
    In your second terminal (with the virtual environment activated), run:

    ```bash
    python -m ModuleContextStreaming.client
    ```

    The client will make several calls to the server and print the responses, including the streamed data.

-----

## Roadmap (Not Yet Implemented) 🗺️

This project is in the early stages of development. The following key features from the design document are planned but not yet implemented:

  * **🔐 Authentication & Authorization:**

      * Implement the server-side gRPC **Interceptor** in `auth.py` to validate Keycloak JWTs on every protected RPC call.
      * Implement a real OAuth 2.0 flow in the client to acquire tokens from Keycloak instead of using a placeholder.

  * **🛡️ Secure Transport (TLS):**

      * Upgrade the server and client from using an `insecure_channel` to a `secure_channel` with SSL/TLS encryption.

  * **⚙️ Real Tool Implementation:**

      * Connect the `StreamToolCall` method in `server.py` to actual backend logic instead of returning placeholder data.

  * **📄 Configuration Management:**

      * Load settings like the server port and Keycloak URLs from a configuration file (e.g., `.env`) instead of being hardcoded.

  * **✍️ Logging:**

      * Integrate a structured logging library (e.g., `logging`) for better debugging and monitoring.

  * **🧪 Testing:**

      * Add unit tests for server-side logic.
      * Add integration tests to verify the client-server communication and authentication flow.

-----

## License

Distributed under the MIT License.