#from spake2 import SPAKE2_A, SPAKE2_B
import receive
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A
from websocket import send

s = SPAKE2_A(b"our password")
msg_out = s.start()
send(msg_out) # this is message A->B
msg_in = receive()
key = s.finish(msg_in)

confirm_A = HKDF(key, info="confirm_A", length=32)
expected_confirm_B = HKDF(key, info="confirm_B", length=32)
send(confirm_A)
confirm_B = receive()
assert confirm_B == expected_confirm_B


from spake2 import SPAKE2_B
q = SPAKE2_B(b"our password")
msg_out = q.start()
send(msg_out)
msg_in = receive() # this is message A->B
key = q.finish(msg_in)


#password = b"our password"
#
#alice = SPAKE2_A(password)
#bob   = SPAKE2_B(password)
#
## Each side creates an outbound message
#msg_from_alice = alice.start()
#msg_from_bob   = bob.start()
#
## Simulate exchanging messages by swapping them
#shared_key_alice = alice.finish(msg_from_bob)
#shared_key_bob   = bob.finish(msg_from_alice)
#
## Both parties now share the same key
#print("Alice's key:", shared_key_alice.hex())
#print("Bob's key:  ", shared_key_bob.hex())