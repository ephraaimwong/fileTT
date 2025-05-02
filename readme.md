# Introduction
Traditionally, SPAKE2 instances require both sides to agree on two key items: a *shared password*, and *what roles both will play* (either as the original client or server). 
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

Additionally we wan to settle not having to establish who is SPAKE2_A, so we implement ``SPAKE2_Symmetric``, which removes the issue of agreeing beforehand which roles both users will play. This plays out to something like such: 
```python
s1 = SPAKE2_Symmetric(agreed_password) # Create password
outmsg1 = s1.start() #create msg
send(outmsg1)# send msg

inmsg2 = receive() # recieve the other users msg
key = s1.finish(inmsg2) #verify 
```
Though for our purposes, this isn't exactly needed, we wanted to keep it simple with only one file/program to run out of and not give the user too much choice/option. 
# Encryption 
We chose the standard cryptography python import from `pip install cryptography.hazmat.primitives`, given that we are dealing with the raw types. 

To encrypt our messages, we follow the following model
1. SPAKE2 --> shared secret 
2. HKDF(shared secret) --> strong symmetric key
3. AES-GCM(message, key from HKDF) --> encrypted message

The SPAKE2 part is pretty simple as we saw above.

To create our we chose [HMAC-based Extract-and-Expand Key Derivation Function (HKDF)](https://datatracker.ietf.org/doc/html/rfc5869) along with a basic *SHA256* hash function and add *salt* results in a unique string every time, even if the *same message* is sent for n-large times, which leads us to the following implementation:
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
We used `.encode()` since we wanted to pass the info through without having to specify the info string to be in byte form. Given HKDF is *HMAC*-based, there is already in-built **padding** at both steps, and is much more than 56 bits (as noted by a length of 32 bytes or *256 bits)*.

From [cryptography.io](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/) docs, we see the use cases for HKDF, in this case being quite fitting for our use case (if not a bit overkill):

> [!NOTE] Cryptographic key derivation
> Key derivation functions derive bytes suitable for cryptographic operations from passwords or other data sources using a pseudo-random function (PRF).
> 
> For Cryptographic key derivation:
> 
> Deriving a key suitable for use as input to an encryption algorithm. Typically this means taking a password and running it through an algorithm such as PBKDF2HMAC or HKDF. This process is typically known as key stretching.
> 
> [HKDF](https://en.wikipedia.org/wiki/HKDF) (HMAC-based Extract-and-Expand Key Derivation Function) is suitable for deriving keys of a fixed size used for other cryptographic operations.

After creating the HKDF session key, we used [AES-GCM](https://cryptography.io/en/latest/hazmat/primitives/aead/) to actually encrypt each of our outbound messages.  

```python
def encrypt_data(data, key):
    """
    Encrypts data using AES-GCM.
    :param data: Data to encrypt
    :param key: 32-byte encryption key
    :return: (iv, ciphertext, tag)
    """
    iv = os.urandom(12)# salt the AES
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return iv, ciphertext, encryptor.tag

def decrypt_data(iv, ciphertext, tag, session_key):
    """
    Decrypts data using AES-GCM.
    :param iv: esentially salt
    :param ciphertext: Encrypted data
    :param tag: auth tag from original HKDF key
    :param session_key: key gen from HKDF
    :return: Decrypted data
    """
    cipher = Cipher(algorithms.AES(session_key), modes.GCM(iv, tag))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()
```

Once one of the users end the session, the key should **no longer be used** and should be considered exposed. If both users wish to contact each other again they should go through the same protocal of a shared password and setting up the initial connection again. Hence, the keys used to communicate should be treated as *one-time throwaway keys*, and are "*updated*" (more accurately remade) everytime the connection is re-established.  
# WebSockets & Frontend Implementation 
In order to actually communicate with each other, the server side sets up a websocket server, and the client connects to it. After they exchange their one-time password exchange, they both generate some secret key used for *that session only*. Hence, both users are assumed to be running with the same codebase, and hence will be sharing the same UI setup. After this, the UI/frontend portion takes care of how messages are actually transmitted (given both are using a one-time generated temporary key). 

The connection is established thru user A establishing a websocket server. User B connects to the server and both generate the session key locally using their pre-agreed upon password. The keys are generated and checked as described previously and both users can now start to talk to each other with each message being encrypted and decrypted by the same secret key.
# Extras

## Establishing a shared password over insecure channels (+5pts)
To share the initial key over an insecure channel is quite simple. Given only a max of 2 users can talk to each other, Alice and Bob can either call, meet in person, or use some end to end encrypted messaging to text each other a known password. This password could be directly exposed or something that both of them know with a hint. While directly exposing the agreed upon password will easily lead to it being leaked in the initial run, there are several factors that make this method possible to establish over insecure channels (even though PAKE is not built for this):
- The session key generated is only viewable to those who have the initial agreed upon password **at the correct time**. 
- Per each attempt an active attacker requires interaction with a legitimate party, with no way to verify the password to work offline. 
- The session key should **never** be exposed thru wire, where only the agreed upon password grants **only both users** the session key
- Both users can perform a third check round of verification by exchanging a message signed with their PGP keys. Once both users receive these messages, they can check the signature to ensure that it is indeed the correct user they are talking to. 
- Without knowledge of the password, an attacker cannot successfully mediate the exchange between both users.

## New Algo design: (+5pts)
(Given it only said to design and justify but no need to use in the codebase)

Say we wanted to further encrypt our current key for some reason. To start, instead of generating just one session key, we generate 2 session keys (or as we call them for this purpose ciphers). We create an encryption by running these 2 ciphers through, then XOR'ing both of them together to create our new cipher (aka session key). In this manner, the attacker would need to break both encryption schemes to recover the plaintext, assuming at least one cipher remains secure. To implement as such, say we have 2 HKDF instances is as K1 and K2. We use a bitwise XOR operation to create our final key K_3 and simply use that key instead of originally only using K1 to encrypt/decrypt. Both the SALT and info for K1 and K2 must be different. Due to HKDF being relatively lightweight, the additional processing time shouldn't affect processing times in any sigificant capactity,   
```python
K_3 = bytes(a ^ b for a, b in zip(K1, K2))
```
However, given we are using PAKE with well-known ciphers, there really isn't much reason to do this (save for the fact of extra credit). 

___
# Requirements
- [x] Alice and Bob share the same password (or passphrase), they must use the password to set up the tool to correctly encrypt and decrypt messages shared between each other.
- [x] Each message during Internet transmission must be encrypted using a key with length no less than 56 bits.
- [x] With a key no less than 56 bits, what cipher you should use?
- [x] DO NOT directly use the password as the key, how can you generate the same key between Alice and Bob to encrypt messages?
- [x] What will be used for padding?
- [ ] A graphical user interface (GUI) is strongly preferred. When send a message, display the sent ciphertext. When receive a message, display the received ciphertext and decrypted plaintext.
- [x] How should Alice and Bob set up an initial connection and also maintain the connection with each other on the Internet? (You may refer to socket/network programming in a particular computer language)
- [x] If Alice or Bob sends the same message multiple times (e.g., they may say “ok” many times), it is desirable to generate different ciphertext each time. How to implement this?
- [x] Design a key management mechanism to periodically update the key used between Alice and Bob. Justify why the design can enhance security.
- [x] Think about this scenario: if you can **hide the detailed procedure of your encryption algorithm**, how would you improve the security by designing a new algorithm? For example, you may do two encryptions using different standard ciphers, then XOR the two outputs together. Please give your new design and justify its security and efficiency. (5 pts) 
- [x] If Alice and Bob do not have a pre-shared password (or passphrase) and wish to establish a secure connection, **they should use a protocol that allows them to authenticate each other and negotiate a shared secret over an insecure channel**. Please explain your design and implement it in your project. Note, if you choose to complete this question, you do not need to assume that Alice and Bob share the same password. (5 pts)



# Deps:
Backend:
- Uvicorn for ASGI server (Maybe not using?)
- FastAPI as main web framework 
- [SPAKE2](https://github.com/warner/python-spake2)
- [cryptography.io](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/#cryptography.hazmat.primitives.kdf.hkdf.HKDF)
- 

Frontend:
- 