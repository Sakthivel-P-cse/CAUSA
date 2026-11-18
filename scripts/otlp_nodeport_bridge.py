#!/usr/bin/env python3
import os, socket, threading, time

def node_ip():
    import subprocess
    return subprocess.check_output([
        "docker", "inspect", "-f",
        "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        "causa-local-control-plane",
    ], text=True).strip()

NODE_IP = os.environ.get("NODE_IP") or node_ip()
TARGETS = {4317: (NODE_IP, 30417), 4318: (NODE_IP, 30418), 13133: (NODE_IP, 31333)}

def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass

def handle(client, target):
    try:
        upstream = socket.create_connection(target, timeout=5)
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        return
    for a, b in ((client, upstream), (upstream, client)):
        threading.Thread(target=pipe, args=(a, b), daemon=True).start()

def listen(port, target):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(256)
    print(f"listening 127.0.0.1:{port} -> {target[0]}:{target[1]}", flush=True)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c, target), daemon=True).start()

for port, target in TARGETS.items():
    threading.Thread(target=listen, args=(port, target), daemon=True).start()
print("bridges up", flush=True)
while True:
    time.sleep(3600)
