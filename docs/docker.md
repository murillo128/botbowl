# Botbowl :heart: docker
[Docker](http://docker.com) is a platform designed to help developers build, share, and run modern applications. 

## Trust and resource boundary

The legacy socket protocol uses Python pickle and is only for mutually trusted
processes on the same host. Receiving a pickle may execute arbitrary code.
Loopback is an exposure restriction, not authentication of local users. Tokens
and request IDs are checked after deserialization and only detect protocol
mixups. Do not use untrusted images, remote daemons, public bindings, proxies,
or port tunnels. Remote untrusted use requires the replacement authenticated
data protocol tracked by #2 API-08/09.

`PythonSocketClient` and `PythonSocketServer` default to `127.0.0.1` and reject
non-loopback addresses; `localhost` is pinned to `127.0.0.1`. There is no remote
opt-in. Raw `send_data` / `receive_data` helpers accept Unix sockets or connected TCP
loopback peers; they still require a trusted peer.
The configurable `max_frame_size` defaults to 64 MiB of serialized payload and
must be a positive integer fitting the ten-byte header. Zero-length messages
are invalid. The configurable `timeout` defaults to 30 seconds (`None` uses
that default, zero expires immediately). One monotonic deadline covers each
frame or the entire client connect/send/receive exchange, also capped by the
game clock. The server uses one deadline for request and response. These are
I/O deadlines: they cannot preempt Python pickle code or an agent callback.
The frame limit does not bound decoded object memory or CPU; peer trust is
still required.

Use `close()` or context managers for clients, servers, and `DockerAgent`.
Each client command closes its connection on success or failure. Invalid
frames/connections are discarded by the server. `SocketProtocolError` subclasses
distinguish malformed frames (`InvalidFrameError`), excessive length
(`FrameTooLargeError`), premature EOF (`UnexpectedEOFError`), expired deadlines
(`ProtocolTimeoutError`), and invalid envelopes (`InvalidMessageError`). After
a protocol error, raw-helper callers must discard their connection. `MultiAgentCompetition`
closes agents created by its factories, including temporary name probes and
failed matchups. Direct `Competition` callers retain ownership of their
supplied agents. There is no destructor-based normal cleanup.

## Submitting a docker image to the Bot Bowl competition
`DockerAgent` starts a trusted local image with host networking and a loopback-only server. It requires a local Unix-socket Docker daemon and host networking support (normally Linux). It passes a free port in `BOTBOWL_SOCKET_PORT`; `PythonSocketServer` reads it by default. No bridge ports are published. Images must use this server or honor the port setting and bind only to loopback. Host networking is not a sandbox for untrusted images. Below follows detailed instructions to build and submit your bot in a docker image. In the example, we'll build a bot called "nuffle".


Modify (../examples/containerized_bot.py) so it starts your bot instead of the scripted bot. 

Add your additional dependencies into (../examples/extra_requirements.txt), if you're bot is based on the [A2C example](a2c.md), you want to add `torch` for example. 

Build the docker image and tag it as `nuffle_bot_image`. Call this command in **the root folder of this repository**: 
```shell
docker build . -t nuffle_bot_image --file docker/Dockerfile.comptetition_bot
```

Confirm that it's working: 
```shell
docker run --network host -e BOTBOWL_SOCKET_PORT=5100 -it nuffle_bot_image
```
You should see: `Agent listening on 127.0.0.1:5100 (trusted local pickle)`.

Create the image file to be uploaded: 
```shell
docker save -o nuffle_bot_image.tar nuffle_bot_image
```
It will create the file `nuffle_bot_image.tar` which is the one you upload in the submission form. Optionally, the image can be compress, e.g. with gzip: `gzip nuffle_bot_image.tar`, it compresses the file quite a lot! 
 
## Troubleshooting: 
 - if you get `ModuleNotFoundError` when running your bot in the docker it could mean that your dependencies (e.g. PyTorch) weren't installed in the image. Make sure they are by adding the to (../examples/extra_requirements.txt)
- Python version. The python version installed in the docker image is specified on the first line of (../docker/Dockerfile.comptetition_bot), you can change it to another version.
