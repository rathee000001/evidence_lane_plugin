"""Disposable loopback SSH Git server and an in-memory test-key agent.

Qualification only. No user key, known_hosts entry, service or remote is used.
Paramiko is supplied by the isolated test environment, not a product dependency.
"""
from __future__ import annotations

import ctypes
import os
import shlex
import socket
import struct
import subprocess
import threading
from uuid import uuid4

import paramiko


class MemoryAgent:
    def __init__(self, folder, key):
        self.key, self.signatures = key, 0
        self.stop = threading.Event()
        self.endpoint = r'\\.\pipe\evidence-lane-ssh-' + str(uuid4()) if os.name == 'nt' else str(folder / 'agent.sock')
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def exchange(self, read, write):
        def exact(size):
            output = b''
            while len(output) < size:
                block = read(size - len(output))
                if not block:
                    return None
                output += block
            return output
        while not self.stop.is_set():
            header = exact(4)
            if header is None:
                break
            size = struct.unpack('>I', header)[0]
            if not 1 <= size <= 256 * 1024:
                break
            data = exact(size)
            if data is None:
                break
            request = paramiko.Message(data[1:])
            response = paramiko.Message()
            if data[0] == 11:
                response.add_byte(bytes([12]))
                response.add_int(1)
                response.add_string(self.key.asbytes())
                response.add_string('isolated-fixture-key')
            elif data[0] == 13:
                blob, payload, flags = request.get_binary(), request.get_binary(), request.get_int()
                assert blob == self.key.asbytes()
                algorithm = 'rsa-sha2-512' if flags & 4 else 'rsa-sha2-256' if flags & 2 else 'ssh-rsa'
                response.add_byte(bytes([14]))
                response.add_string(self.key.sign_ssh_data(payload, algorithm=algorithm).asbytes())
                self.signatures += 1
            else:
                response.add_byte(bytes([5]))
            value = response.asbytes()
            write(struct.pack('>I', len(value)) + value)

    def serve(self):
        if os.name != 'nt':
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(self.endpoint)
                listener.listen(5)
                listener.settimeout(.2)
                self.ready.set()
                while not self.stop.is_set():
                    try:
                        conn, _ = listener.accept()
                    except TimeoutError:
                        continue
                    with conn:
                        self.exchange(conn.recv, conn.sendall)
            return
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
        while not self.stop.is_set():
            handle = kernel.CreateNamedPipeW(self.endpoint, 3, 0, 1, 262144, 262144, 0, None)
            assert handle != ctypes.c_void_p(-1).value
            self.ready.set()
            try:
                connected = kernel.ConnectNamedPipe(handle, None)
                if not connected and ctypes.get_last_error() != 535:
                    continue
                def read(size, handle=handle):
                    buffer, count = ctypes.create_string_buffer(size), wintypes.DWORD()
                    if not kernel.ReadFile(handle, buffer, size, ctypes.byref(count), None):
                        return b''
                    return buffer.raw[:count.value]
                def write(value, handle=handle):
                    count = wintypes.DWORD()
                    buffer = ctypes.create_string_buffer(value)
                    assert kernel.WriteFile(handle, buffer, len(value), ctypes.byref(count), None)
                    assert count.value == len(value)
                self.exchange(read, write)
            finally:
                kernel.DisconnectNamedPipe(handle)
                kernel.CloseHandle(handle)

    def close(self):
        self.stop.set()
        if os.name == 'nt':
            try:
                with open(self.endpoint, 'r+b', buffering=0) as stream:
                    stream.write(b'\0\0\0\0')
            except OSError:
                pass
        self.thread.join(5)
        assert not self.thread.is_alive()


class GitSSHServer:
    def __init__(self, repository, client_key):
        self.repository, self.client_key = repository, client_key
        self.host_key = paramiko.RSAKey.generate(2048)
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(5)
        self.listener.settimeout(.2)
        self.port = self.listener.getsockname()[1]
        self.url = f'ssh://fixture@127.0.0.1:{self.port}/fixture.git'
        self.stop = threading.Event()
        self.commands, self.connections, self.children, self.errors = [], [], [], []
        self.completions = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self.handle, args=(conn,), daemon=True)
            self.connections.append(thread)
            thread.start()

    def handle(self, conn):
        outer = self
        ready = threading.Event()
        class Server(paramiko.ServerInterface):
            command = None
            def get_allowed_auths(self, username):
                return 'publickey'
            def check_auth_publickey(self, username, key):
                return paramiko.AUTH_SUCCESSFUL if username == 'fixture' and key == outer.client_key else paramiko.AUTH_FAILED
            def check_channel_request(self, kind, chanid):
                return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
            def check_channel_exec_request(self, channel, command):
                parts = shlex.split(command.decode())
                if len(parts) != 2 or parts[0] not in {'git-upload-pack', 'git-receive-pack'} or parts[1] != '/fixture.git':
                    return False
                self.command = parts[0].removeprefix('git-')
                outer.commands.append(command.decode())
                ready.set()
                return True
        transport = paramiko.Transport(conn)
        child = None
        try:
            transport.add_server_key(self.host_key)
            server = Server()
            transport.start_server(server=server)
            channel = transport.accept(10)
            if channel is None or not ready.wait(10):
                return
            environment = {key: value for key, value in os.environ.items() if not key.startswith(('GIT_', 'GCM_', 'SSH_'))}
            environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
            child = subprocess.Popen(['git', '-c', 'core.hooksPath=' + os.devnull,
                server.command, str(self.repository)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=environment, cwd=self.repository, bufsize=0)
            self.children.append(child)
            def incoming():
                try:
                    while block := channel.recv(65536):
                        child.stdin.write(block)
                except (OSError, EOFError):
                    pass
                finally:
                    child.stdin.close()
            def outgoing(stream, send):
                try:
                    while block := stream.read(65536):
                        send(block)
                except (OSError, EOFError):
                    pass
            readers = [threading.Thread(target=incoming, daemon=True),
                threading.Thread(target=outgoing, args=(child.stdout, channel.sendall), daemon=True),
                threading.Thread(target=outgoing, args=(child.stderr, channel.sendall_stderr), daemon=True)]
            for reader in readers:
                reader.start()
            code = child.wait(30)
            for reader in readers[1:]:
                reader.join(5)
            channel.send_exit_status(code)
            channel.shutdown_write()
            channel.close()
            readers[0].join(5)
            # Keep the TCP transport alive for the client's SSH disconnect.
            # Closing it immediately after our channel-close packet can reset
            # Windows OpenSSH while it sends its final disconnect message.
            transport.join(5)
            self.completions.append({'command': server.command, 'returncode': code,
                'peer_disconnected': not transport.is_active(),
                'transport_thread_ended': not transport.is_alive(),
                'stream_threads_ended': all(not reader.is_alive() for reader in readers)})
        except (EOFError, ConnectionResetError, paramiko.SSHException):
            pass  # Host-key and authentication rejection are negative fixtures.
        except Exception as error:  # noqa: BLE001 - record background fixture failures for the owning test.
            self.errors.append(repr(error))
        finally:
            if child and child.poll() is None:
                child.kill()
                child.wait(5)
            transport.close()
            conn.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(5)
        for thread in self.connections:
            thread.join(12)
            assert not thread.is_alive()
        assert all(child.poll() is not None for child in self.children)
        assert not self.errors, self.errors
