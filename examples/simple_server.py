# In examples/simple_server.py

from ModuleContextStreaming.server import serve

if __name__ == '__main__':
    """
    Runs the gRPC server by calling the main serve() function
    from the ModuleContextStreaming package.
    """
    print("Starting server from examples/simple_server.py...")
    serve()