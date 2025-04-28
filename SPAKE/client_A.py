# client.py  (Party A)
import os
import socket
import argparse
from spake2 import SPAKE2_A
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

def hkdf_expand(session_secret_key, info, length=32):
    """
    Expands key so we can use it for key confirmation.
    :param session_secret_key: sesh secret key generated frm SPAKE2
    :param info: Tag attached to key to identify it
    :param length: keyLength
    :return: HKDF key
    """
    salt = os.urandom(16)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info.encode()
    ).derive(session_secret_key)

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

def run_client(host, port, password):
    conn = socket.create_connection((host, port))
    print(f"[Client] Connected to {host}:{port}")

    # 1) Start SPAKE2_A
    spa = SPAKE2_A(password)
    msg_a = spa.start()
    send(conn, msg_a)

    # 2) Receive B’s message and finish
    msg_b = receive(conn)
    key = spa.finish(msg_b)
    print("[Client] Shared key derived.")

    # 3) Key confirmation
    confirm_A = hkdf_expand(key, "confirm_A")
    expected_confirm_B = hkdf_expand(key, "confirm_B")

    # send A’s confirm and verify B’s
    send(conn, confirm_A)
    recv_B = receive(conn)
    if recv_B != expected_confirm_B:
        raise ValueError("Key confirmation failed at client!")
    print("[Client] Key confirmation succeeded. Secure channel established.")

    conn.close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host",     required=True)
    p.add_argument("--port",     type=int, required=True)
    p.add_argument("--password", required=True,
                   help="Pre-shared password (bytes)")
    args = p.parse_args()
    run_client(args.host, args.port, args.password.encode())
