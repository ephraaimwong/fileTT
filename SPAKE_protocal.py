import socket
from spake2 import SPAKE2_A, SPAKE2_B
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

def alice_protocol(conn, pw):
    alice = SPAKE2_A(pw)
    conn.sendall(alice.start())
    # serialize state if desired:
    state = alice.serialize()

    bob_msg = conn.recv(1024)
    # restore if serialized:
    alice = SPAKE2_A.from_serialized(state)
    K = alice.finish(bob_msg)

    # Key confirmation
    confirm_A = HKDF(..., info=b"confirm_A").derive(K)
    conn.sendall(confirm_A)
    confirm_B = conn.recv(1024)
    expected_B = HKDF(..., info=b"confirm_B").derive(K)
    assert confirm_B == expected_B
    return K

def bob_protocol(conn, pw):
    bob = SPAKE2_B(pw)
    conn.sendall(bob.start())
    alice_msg = conn.recv(1024)
    K = bob.finish(alice_msg)

    # Key confirmation (mirror)
    confirm_A = conn.recv(1024)
    expected_A = HKDF(..., info=b"confirm_A").derive(K)
    assert confirm_A == expected_A
    confirm_B = HKDF(..., info=b"confirm_B").derive(K)
    conn.sendall(confirm_B)
    return K