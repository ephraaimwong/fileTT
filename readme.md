
 
 - Create SPAKE2 server
	 - 
 - Key Confirmation
	 - 
 - Use SPAKE2_Symmetric 


- Most of the writing will be done to defend why SPAKE2 is secure for this context, and why certain common attacks won't be able to do such things



___
# Introduction
Traditionally, SPAKE2 instances require both sides to agree on two key items: a shared password, and what roles both will play (either as the original client or server). 
The protocol requires atleast one message exchange to establish the one time unique session key (a second round is optional to check key-confirmation). Once both users obtain the given session key, and an encryption method of the server's choice, they can then proceed to send messages over a given websocket that the server setups. (Hence the session key acts as the generator for the shared secret key used to encrypt messages). Each message is then encrypted by the session key, and will last for the lifetime of the conversation. 

Of course, the presented limitations of pre-agreement information can be worked around (as will be discussed in a later section), though of course, those workarounds come with their tradeoffs. To start off we will first start with how a traditional SPAKE2 instance is setup. 
# SPAKE2 Setup
Initialing SPAKE2 is a pretty simple process. Alice and Bob both have the same principles in setting up, though one of them will use `SPAKE2_A` and the other `SPAKE2_B` (as part of the agreed items beforehand):
```python
from spake2 import SPAKE2_A #in this case we are the client
connection = socket.create_connection((host, port))   

s = SPAKE2_A(password) # SPAKE2 protocal occurs
msg_a = s.start()  # Generate message to send
send(connection, msg_a) # sends the message

msg_b = receive(connection)  # recieve B's message
key = spa.finish(msg_b)#generate the key
assert(recv_B != expected_confirm_B) #ensure keys equate to each other
```

Given security is our main priority, we also added the optional secondary key-check-confirmation doing something along the lines of the following psudeo-like code:
```python
confirm_A = HKDF(...)
expected_confirm_B = HKDF(...)
send(confirm_A)
confirm_B = receive()
assert confirm_B == expected_confirm_B
```
Which tests for identical keys. We will cover what and how HKDF is made later. 

If we wanted to settle not having to establish SPAKE2_A, we can implement ``SPAKE2_Symmetric``, which removes the issue of agreeing beforehand which roles both users will play. This is played out like so:
```python
s1 = SPAKE2_Symmetric(agreed_password) # Create password
outmsg1 = s1.start() #create msg
send(outmsg1)# send msg

inmsg2 = receive() # recieve the other users msg
key = s1.finish(inmsg2) #verify 
```
Though for our purposes, this isn't exactly needed, but is good to keep in mind that is possible to have. 
# Encryption 
We chose the standard cryptography python import from `pip install cryptography.hazmat.primitives`, given that we are dealing with the raw types. 

To encrypt our messages, we chose [HMAC-based Extract-and-Expand Key Derivation Function (HKDF)](https://datatracker.ietf.org/doc/html/rfc5869) along with a basic *SHA256* hash function and add *salt* resulting in the following:
```python
def hkdf_expand(session_secret_key, info, length=32):  
	salt = os.urandom(16)  
	return HKDF(  
    algorithm=hashes.SHA256(),  
    length=length,  
    salt=salt,  
    info=info.encode()  
).derive(session_secret_key)
```
We used `.encode()` since we wanted to pass the info through without having to specify the info string to be in byte form.

From [cryptography.io](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/) docs, we see the use cases for HKDF, in this case being quite fitting for our use case (if not a bit overkill):

> [!NOTE] Cryptographic key derivation
> Key derivation functions derive bytes suitable for cryptographic operations from passwords or other data sources using a pseudo-random function (PRF).
> 
> For Cryptographic key derivation:
> 
> Deriving a key suitable for use as input to an encryption algorithm. Typically this means taking a password and running it through an algorithm such as PBKDF2HMAC or HKDF. This process is typically known as key stretching.
> 
> [HKDF](https://en.wikipedia.org/wiki/HKDF) (HMAC-based Extract-and-Expand Key Derivation Function) is suitable for deriving keys of a fixed size used for other cryptographic operations.

# WebSockets & Frontend Implementation 
In order to actually communicate with each other, the server side sets up a websocket server, and the client connects to it. After they exchange their one-time password exchange, they both generate some secret key used for *that session only*. Hence, both users are assumed to be running with the same codebase, and hence will be sharing the same UI setup. After this, the UI/frontend portion takes care of how messages are actually transmitted (given both are using a one-time generated temporary key). 

Once one of the users end the session, the key should **no longer be used** and should be considered exposed. If both users wish to contact each other again they should go through the same protocal of a shared password and setting up the initial connection again. 

## Encryption
We chose to default to 

___
# Requirements
- [x] Alice and Bob share the same password (or passphrase), they must use the password to set up the tool to correctly encrypt and decrypt messages shared between each other.
- [x] Each message during Internet transmission must be encrypted using a key with length no less than 56 bits.
- [x] With a key no less than 56 bits, what cipher you should use?
- [x] DO NOT directly use the password as the key, how can you generate the same key between Alice and Bob to encrypt messages?
- [ ] What will be used for padding?
- [ ] A graphical user interface (GUI) is strongly preferred. When send a message, display the sent ciphertext. When receive a message, display the received ciphertext and decrypted plaintext.
- [ ] How should Alice and Bob set up an initial connection and also maintain the connection with each other on the Internet? (You may refer to socket/network programming in a particular computer language)
- [ ] If Alice or Bob sends the same message multiple times (e.g., they may say “ok” many times), it is desirable to generate different ciphertext each time. How to implement this?
- [ ] Design a key management mechanism to periodically update the key used between Alice and Bob. Justify why the design can enhance security.
