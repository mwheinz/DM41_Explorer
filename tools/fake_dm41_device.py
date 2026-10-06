import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ttyB"

STATE_BODY = (
    "DM41\n"
    "00  01234567890123  09876543210987  00000000000000  00000000000000\n"
    "A: 00000000c00020 B: f000002c0480fd C: f000002c0480fd\n"
    "M: 00011cd5ff73cb N: 000000000000c0 G: 00\n"
)

RESPONSES = {
    "b": "BAT: 3200mV",
    "t": "2026-07-29 22:40:00 WED",
    "s": STATE_BODY.strip(),
}


def main():
    ser = serial.Serial(PORT, 38400, timeout=0.2)
    print(f"[fake device] listening on {PORT}", flush=True)
    buf = ""
    loading = False
    while True:
        chunk = ser.read(256)
        if chunk:
            buf += chunk.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()

                if loading:
                    # Swallow the streamed state silently until the final
                    # "M: ... N: ... G: ..." line, matching Memory.to_string()'s
                    # fixed line order.
                    print(f"[fake device] (loading) received: {line!r}", flush=True)
                    if line.startswith("M:"):
                        loading = False
                        reply = "Read OK\nDM41 >> "
                        ser.write(reply.encode("utf-8"))
                        print(f"[fake device] sent: {reply!r}", flush=True)
                    continue

                print(f"[fake device] received: {line!r}", flush=True)
                if not line:
                    continue
                cmd = line.split()[0]
                if cmd == "l":
                    loading = True
                    # Echo the command itself; the "Read OK" comes later.
                    ser.write(f"{line}\n".encode("utf-8"))
                    print("[fake device] sent echo for 'l', now loading...", flush=True)
                    continue
                if cmd in RESPONSES:
                    reply = f"{line}\n{RESPONSES[cmd]}\nDM41 >> "
                elif cmd == "ts":
                    reply = f"{line}\nTime set.\nDM41 >> "
                else:
                    reply = f"{line}\nUnknown command.\nDM41 >> "
                ser.write(reply.encode("utf-8"))
                print(f"[fake device] sent: {reply!r}", flush=True)
        time.sleep(0.01)


if __name__ == "__main__":
    main()
