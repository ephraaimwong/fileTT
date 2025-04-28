import socket
import argparse
from spake2 import SPAKE2_B
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

def hkdf_expand(key_material, info, length=32):
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info.encode(),
        backend=default_backend()
    ).derive(key_material)

def send(conn, msg):
    conn.sendall(len(msg).to_bytes(4, 'big') + msg)

def receive(conn):
    length_bytes = conn.recv(4)
    if not length_bytes:
        raise ConnectionError("Connection closed")
    length = int.from_bytes(length_bytes, 'big')
    buf = b''
    while len(buf) < length:
        chunk = conn.recv(length - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed during recv")
        buf += chunk
    return buf

def run_server(host, port, password):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, port))
    s.listen(1)
    print(f"[Server] Listening on {host}:{port}…")
    conn, addr = s.accept()
    print(f"[Server] Connection from {addr}")

    # 1) Start SPAKE2_B
    spb = SPAKE2_B(password)
    msg_b = spb.start()
    send(conn, msg_b)

    # 2) Receive A’s message and finish
    msg_a = receive(conn)
    key = spb.finish(msg_a)
    print("[Server] Shared key derived.")

    # 3) Key confirmation
    expected_confirm_A = hkdf_expand(key, "confirm_A")
    confirm_B = hkdf_expand(key, "confirm_B")

    # send B’s confirm and verify A’s
    send(conn, confirm_B)
    recv_A = receive(conn)
    if recv_A != expected_confirm_A:
        raise ValueError("Key confirmation failed at server!")
    print("[Server] Key confirmation succeeded. Secure channel established.")

    conn.close()
    s.close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host",  default="0.0.0.0")
    p.add_argument("--port",  type=int, default=8000)
    p.add_argument("--password", required=True,
                   help="Pre-shared password (bytes)")
    args = p.parse_args()
    run_server(args.host, args.port, args.password.encode())
