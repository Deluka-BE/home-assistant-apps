"""Linux Docker gates without credentials. No real public Funnel is created.

1. Real tailscaled userspace startup with all capabilities dropped.
2. Production PID1 with mocked CLI/daemon and real host-loopback HTTP origin.
The second test proves lifecycle orchestration, not Tailscale's public transport.
"""
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


BOOTSTRAP_PROBE = '''
import os,pathlib,stat
p=pathlib.Path('/tmp/bootstrap-probe')
def snapshot(label):
 s=p.stat() if p.exists() else None
 caps=next(x for x in pathlib.Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:'))
 print(label, 'euid/egid',os.geteuid(),os.getegid(), 'owner',None if s is None else (s.st_uid,s.st_gid), 'mode',None if s is None else oct(stat.S_IMODE(s.st_mode)),caps,flush=True)
snapshot('before mkdir')
p.mkdir(mode=0o700)
snapshot('after mkdir / before chown')
os.chown(p,10002,10002)
snapshot('after chown / before chmod')
try:
 os.chmod(p,0o700)
except PermissionError as e:
 print('PROVEN: chmod after chown fails:',repr(e),flush=True)
else:
 raise AssertionError('expected chmod to fail without FOWNER')
snapshot('after failed chmod')
q=pathlib.Path('/tmp/bootstrap-reordered'); q.mkdir(mode=0o700)
os.chmod(q,0o700); os.chown(q,10002,10002)
print('chmod before chown succeeds on fresh root-owned directory; persisted directory still needs owner privileges',flush=True)
'''

PREPARE_PROBE = '''
import os,pathlib,stat,runtime
p=pathlib.Path('/data/tailscale')
def snapshot(label):
 s=p.stat() if p.exists() else None
 caps=next(x for x in pathlib.Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:'))
 print(label,'euid/egid',os.geteuid(),os.getegid(),'owner',None if s is None else (s.st_uid,s.st_gid),'mode',None if s is None else oct(stat.S_IMODE(s.st_mode)),caps,flush=True)
def trace(name,original):
 def call(*args,**kwargs):
  snapshot('before '+name)
  result=original(*args,**kwargs)
  snapshot('after '+name)
  return result
 return call
original_mkdir=pathlib.Path.mkdir
pathlib.Path.mkdir=trace('mkdir',original_mkdir)
for name in ('chown','setgroups','setgid','setuid','chmod'):
 setattr(os,name,trace(name,getattr(os,name)))
runtime.prepare()
assert os.geteuid()==os.getegid()==10002
assert stat.S_IMODE(p.stat().st_mode)==0o700
assert 'CapEff:\\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
print('PASS: actual prepare with zero final capabilities',flush=True)
'''


def eventually(predicate, seconds=40):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(1)
    raise AssertionError("container gate timed out")


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
    def log_message(self, *_args):
        pass


FAKE_DAEMON = '''#!/usr/local/bin/python
import pathlib,signal,time,sys
print('TLS error FAKE-CREDENTIAL user@example.com',file=sys.stderr,flush=True)
p=pathlib.Path('/data/tailscale')
state=p/'tailscaled.state'
if not state.exists(): state.write_text('fake-private-state')
def stop(*args):
 (p/'stopped').write_text('yes')
 raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
while True: time.sleep(0.1)
'''
FAKE_CLI = '''#!/usr/local/bin/python
import json,pathlib,sys
p=pathlib.Path('/data/tailscale'); args=sys.argv[2:]
domain='mcp-funnel.test-tail.ts.net'; hp=domain+':443'
if args[0]=='up':
 print('permission denied FAKE-CREDENTIAL user@example.com',file=sys.stderr,flush=True)
 if not (p/'authorized').exists():
  print(json.dumps({'AuthURL':'https://login.tailscale.com/a/fake123','secret':'fake-private-state'}))
  (p/'authorized').write_text('fake-only')
 else: print(json.dumps({'BackendState':'Running'}))
elif args[0]=='status':
 print(json.dumps({'BackendState':'Running','Self':{'Online':True,'DNSName':domain+'.'}}))
elif args[:2]==['funnel','status']:
 print((p/'mock-funnel.json').read_text() if (p/'mock-funnel.json').exists() else '{}')
elif args[0]=='funnel' and '--bg' in args:
 config={'TCP':{'443':{'HTTPS':True}},'Web':{hp:{'Handlers':{'/':{'Proxy':'http://127.0.0.1:8000'}}}},'AllowFunnel':{hp:True}}
 (p/'mock-funnel.json').write_text(json.dumps(config))
 counter=p/'configure-count'; counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')
else: raise SystemExit(1)
'''


def main(image):
    name = "mcp-funnel-test-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    server = None
    docker("volume", "create", volume)
    try:
        print(docker("run", "--rm", "--network", "host", "--cap-drop", "ALL",
                     "--cap-add", "CHOWN", "--cap-add", "SETUID", "--cap-add", "SETGID",
                     "--entrypoint", "python", image, "-c", BOOTSTRAP_PROBE), flush=True)
        # Actual production bootstrap, both fresh storage and persisted ownership.
        for label in ("fresh /data", "persisted /data"):
            print(label, flush=True)
            print(docker("run", "--rm", "--network", "host", "--cap-drop", "ALL",
                         "--cap-add", "CHOWN", "--cap-add", "SETUID", "--cap-add", "SETGID",
                         "-v", volume + ":/data", "--entrypoint", "python", image,
                         "-c", PREPARE_PROBE), flush=True)
        # Real daemon: no login, no credentials, no Funnel, no TUN or capabilities.
        docker("run", "-d", "--name", name, "--network", "host", "--cap-drop", "ALL",
               "--user", "10002:10002", "--entrypoint", "tailscaled", image,
               "--tun=userspace-networking", "--port=0", "--state=/tmp/tailscaled.state",
               "--statedir=/tmp/ts", "--socket=/tmp/tailscaled.sock")
        eventually(lambda: subprocess.run(["docker", "exec", name, "tailscale",
                   "--socket=/tmp/tailscaled.sock", "status", "--json"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
        assert docker("exec", name, "python", "-c", "import os; print(os.path.exists('/dev/net/tun'))") == "False"
        assert "CapEff:\t0000000000000000" in docker("exec", name, "cat", "/proc/1/status")
        docker("stop", "-t", "20", name)
        docker("rm", name)
        print("PASS: real userspace daemon without TUN/capabilities (no authentication)")

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for filename, contents in (("tailscaled", FAKE_DAEMON), ("tailscale", FAKE_CLI)):
                path = folder / filename
                path.write_text(contents)
                path.chmod(0o755)
            args = ["run", "-d", "--name", name, "--network", "host", "--cap-drop", "ALL",
                    "--cap-add", "CHOWN", "--cap-add", "SETUID", "--cap-add", "SETGID",
                    "-v", volume + ":/data"]
            for filename in ("tailscaled", "tailscale"):
                args += ["-v", f"{folder / filename}:/usr/local/bin/{filename}:ro"]
            docker(*args, image)
            eventually(lambda: "Waiting for Gateway" in docker("logs", name))
            assert "CapEff:\t0000000000000000" in docker("exec", name, "cat", "/proc/1/status")
            assert "Uid:\t10002\t10002\t10002\t10002" in docker("exec", name, "cat", "/proc/1/status")
            assert "Gid:\t10002\t10002\t10002\t10002" in docker("exec", name, "cat", "/proc/1/status")
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 8000), Health)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            eventually(lambda: "Ready:" in docker("logs", name))
            assert docker("exec", name, "python", "/opt/mcp-funnel/healthcheck.py") == ""
            assert "fake-private-state" not in docker("logs", name)
            logs = docker("logs", name)
            assert "FAKE-CREDENTIAL" not in logs and "user@example.com" not in logs
            for message in ("start_login()", "PID=", "exitcode=0", "JSON received", "AuthURL found",
                            "daemon: TLS connection", "login stderr: permission denied"):
                assert message in logs, message
            assert docker("exec", "--user", "10002:10002", name, "cat", "/data/tailscale/configure-count") == "1"
            docker("stop", "-t", "25", name)
            assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0"
            docker("rm", name)
            docker(*args, image)
            eventually(lambda: "Ready:" in docker("logs", name))
            assert "Authorize this new node" not in docker("logs", name)
            assert docker("exec", "--user", "10002:10002", name, "cat", "/data/tailscale/configure-count") == "1"
            assert docker("exec", "--user", "10002:10002", name, "cat", "/data/tailscale/tailscaled.state") == "fake-private-state"
            assert docker("exec", "--user", "10002:10002", name, "cat", "/data/tailscale/stopped") == "yes"
            assert "fake-private-state" not in docker("logs", name)
            print("PASS: host-loopback health, offline-to-online, UID/capabilities, SIGTERM, persistent data, no unnecessary reconfiguration (mock Tailscale)")
    except BaseException:
        # Only disposable credential-free test containers; never inspect state files.
        print("FAILURE: container stdout/stderr follows", flush=True)
        subprocess.run(["docker", "logs", name], check=False)
        subprocess.run(["docker", "inspect", "--format", "{{json .State}}", name], check=False)
        raise
    finally:
        if server:
            server.shutdown()
            server.server_close()
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("NOT TESTED: authenticated public HTTPS Funnel transport, SSE and HAOS reboot/AppArmor")


if __name__ == "__main__":
    main(sys.argv[1])
