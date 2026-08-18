# Redcoder — Network Modes Guide

A complete, plain-language reference for redcoder's three network modes:
**Airgapped**, **Lab**, and **Online** — what each one does, how to use it, and
**how to prove for yourself that the isolation is real**.

Written to be understandable with no prior knowledge. Keep this on the USB as a reference.

---

## 0. The one idea that makes all of this make sense

Redcoder is an AI coding agent that runs a **local** model. There is no cloud, no
API — the "brain" is a model served by **Ollama** on your own machine at the address
`127.0.0.1:11434`.

That address, `127.0.0.1` (also called **localhost** or **loopback**), is special:
it is a *virtual* network interface that lives **entirely inside your computer**. Traffic
to `127.0.0.1` never goes to your Wi-Fi or ethernet card. It cannot leave the machine.
Think of it as talking to yourself in your own head — no sound leaves the room.

**Why that matters:** even if you physically rip out every network device, or disable
Wi-Fi in BIOS, redcoder still works, because the model lives locally and the agent talks
to it over loopback. "Offline" does not mean "broken" here — it means *fully self-contained*.

### So what is actually being isolated?

The redcoder program (the Python process) always talks to Ollama over loopback. That part
is the same in every mode and never touches the network card.

The thing that *could* reach the internet is different: it's the **shell commands the AI
decides to run** — things like `curl`, `nmap`, `apt`, `wget`, `git`. Those are ordinary
programs that use the real network. **The modes control what those shell commands can reach.**

- **Airgapped** — shell commands have **no network at all**.
- **Lab** — shell commands reach a **fake, offline practice network** (never the internet).
- **Online** — shell commands can reach the **real internet**, but every action asks you first.

Everything below is about those three walls, and how to trust them.

---

## 1. Launching redcoder and picking a mode

From a terminal on the Kali stick:

```bash
redcoder            # Airgapped (this is the DEFAULT — no network for shell commands)
redcoder --lab      # Lab (fake offline practice network)
redcoder --online   # Online (real internet, gated by your approval)
```

When it starts, look at the top of the screen. It prints the mode in one word:

```
  Airgapped
```
or `  Lab`  or  `  Online`. That word is your ground truth for which wall is active.

You can also **switch modes mid-session** by typing a command to redcoder:

```
/net            # show the current mode
/net airgap     # switch to Airgapped
/net lab        # switch to Lab
/net online     # switch to Online
```

(Type `/help` to see all the in-session commands.)

---

## 2. How you talk to it, and how it runs commands

You chat with redcoder in plain English. When it needs to *do* something, it runs a tool.
Some tools are harmless and run silently; others ask your permission first.

### The permission system (yellow vs red)

- **Free — no prompt:** reading files, listing folders, searching. These can't hurt anything.
- **Yellow prompt — normal action:** running an ordinary shell command, writing/editing a
  file. It shows the command and asks `Allow? [Y/n]`. Pressing **Enter approves** it.
- **Red prompt — dangerous or powerful action:** deleting things, changing the network,
  using `sudo`, or (in Online) running attack tools. It shows
  `Run this dangerous command? [y/N]`. Here **Enter DENIES** — you must actually type `y`.

**Important:** red does not *block* anything. Everything still runs if you approve it. Red
just means (1) it always asks even if you turned on auto-approve, (2) the safe default is
"No", and (3) there's no "always" shortcut. It's a speed bump, not a wall. You keep full
control and full capability — you just have to say yes on purpose.

---

## 3. AIRGAPPED mode (the default, most locked-down)

### What it is
Shell commands run with **no network whatsoever** — not the internet, not even a local
network. Any `curl`, `ping`, `apt`, or scan the AI tries will simply fail. This is the mode
to use when you want maximum certainty that nothing the AI does can reach out.

### How to start it
```bash
redcoder
```
The header shows `Airgapped`.

### How it works (in plain terms)
Every shell command is wrapped in a tool called `unshare -rn`. This does two things:

1. **`-n` gives the command a brand-new, empty network space** — it has no network
   interfaces except a disconnected loopback. There is literally nothing to send packets
   through. Isolation by *absence*, not by a rule that could be misconfigured.
2. **`-r` makes it "rootless"** — inside, the command *thinks* it is the administrator
   (root), but that is a fake, mapped identity with **zero real power** over the machine.
   It cannot turn networking back on, load drivers, or touch system settings.

If neither `unshare` nor `firejail` is available, redcoder **refuses to run shell commands
at all** rather than risk running them with a live network. This is called *failing closed*:
when in doubt, it does nothing.

### What Airgapped protects — and what it does NOT
- ✅ Protects against **any network access** by shell commands. Provably impossible.
- ⚠️ Does **not** sandbox your **files**. A command can still read, change, or delete files
  in your home folder (it runs against your real disk). That's what the yellow/red prompts
  are for. So: airgapped removes the *network* danger structurally, and the *approval
  prompts* handle the *file* danger. Use both.

### HOW TO VERIFY IT'S ACTUALLY AIRGAPPED

**Check A — see the empty network the mechanism creates (run in a plain terminal, no redcoder):**
```bash
unshare -rn ip -brief link
```
Expected output — only a loopback, and it's DOWN:
```
lo               DOWN           00:00:00:00:00:00 <LOOPBACK>
```
That is exactly the environment every airgapped command runs in. No `eth0`, no `wlan0`,
nothing to reach the internet with.

**Check B — ask redcoder to try to reach the internet and watch it fail.**
Start `redcoder`, then tell it:
> run this command: `curl -m 3 https://example.com`

Approve the prompt. Expected: it fails with something like
`Could not resolve host` or `Network is unreachable`. If it *fails*, the airgap is working.
Try `ping -c1 1.1.1.1` too — same story, it will fail.

If either of those ever *succeeds* in Airgapped mode, stop and tell someone — that would
mean the wall isn't holding. (In testing on a real Linux kernel, it always fails.)

---

## 4. LAB mode (offline practice network for security tools)

### What it is
A **fake, self-contained network** where the AI can run real security tools (`nmap`,
`sqlmap`, etc.) against **practice targets** that you control — with **no path to the
internet**. This is for QA and for exercising tools safely. It is the "give it real toys
in a padded room" mode.

### How to start it
```bash
redcoder --lab
```
The **first time each boot**, it needs to build the practice network, which requires
administrator rights, so **it will ask for your password once** (after that, sudo remembers
it for a few minutes). You'll see it build the network, prove the airgap, and scan:

```
  lab not built yet — running: sudo ./lab-net.sh up  (sudo may ask for your password)
  ...
  internet is UNREACHABLE from 'rclab' — airgap confirmed
  redcoder --lab is safe to use
  lab scan: 1 target(s) up on 10.66.0.0/24 — 10.66.0.20
  Lab
```

That `lab scan` line is redcoder proving there's a live practice target to work with.

### How it works (in plain terms)
Think of it as building a tiny toy network on a workbench, with the internet cable
**deliberately never plugged in**:

- **`rclab`** — a private network "room" (a *network namespace*) where the AI's commands run.
- **`rclab-br`** — a virtual network switch (*bridge*). **Its critical property: no real
  network cable is ever connected to it.** That single fact is what makes the internet
  physically unreachable — packets have nowhere to go except other toy machines on the switch.
- **A practice target** at address **`10.66.0.20`**, running a small web server on port 80,
  so tools like `nmap` find something real to scan.
- The AI's room has the address **`10.66.0.10`** and can talk to anything in the
  **`10.66.0.0/24`** range (the toy network) — and nothing else.

Because the switch has no uplink, there is **no route to the internet**, no matter what.

### Root, and why the AI has to *ask* for it
By default, commands in the lab run as **your normal user, not root** — least privilege.
That means the AI can't tamper with the host or try to escape the room. If a tool genuinely
needs administrator power (for example a stealth SYN scan, `nmap -sS`), the AI has to prefix
its command with `sudo`, which triggers a **red prompt you can deny**. So even inside the
padded room, gaining root is a deliberate, visible, approvable step.

- Plain command (e.g. `nmap 10.66.0.20`) → runs as you, **yellow** prompt.
- `sudo nmap -sS 10.66.0.20` → **red** "Run AS ROOT in the lab" prompt. Type `y` to allow.

### Talking to the practice target
Point tools at `10.66.0.0/24` (specifically `10.66.0.20`), never at real websites:
```
nmap 10.66.0.20            # scan the practice target
curl http://10.66.0.20/    # fetch its web page
nmap 10.66.0.0/24          # discover everything on the toy network
```

### HOW TO VERIFY THE LAB IS REALLY AIRGAPPED

The most trustworthy checks are the ones **you** run in a plain terminal — not asking the
AI to check its own cage.

**Check A — the built-in verifier (run in a plain terminal):**
```bash
cd /home/kali/redcoder-kali
sudo ./lab-net.sh verify
```
Expected:
```
    internet is UNREACHABLE from 'rclab' — airgap confirmed
    redcoder --lab is safe to use
```
This actively tries to open connections to public internet addresses **from inside the lab**
and requires them all to fail. If any succeeded, it would refuse with a loud "NOT SAFE".

**Check B — confirm no real network cable is on the toy switch (the whole safety story):**
```bash
ip link show master rclab-br
```
Expected: you should see only virtual `veth...` interfaces. You must **never** see `eth0`,
`wlan0`, or any real interface name here. If you do, the lab is not airgapped — run
`sudo ./lab-net.sh down` and investigate.

**Check C — try to reach the internet from inside the lab yourself:**
```bash
sudo ip netns exec rclab bash -c 'timeout 2 bash -c "exec 3<>/dev/tcp/1.1.1.1/443" && echo REACHED || echo BLOCKED'
```
Expected: `BLOCKED` (with "Network is unreachable"). That's you personally confirming the
AI's exact environment can't reach the internet.

**Check D — confirm the practice target IS reachable (so the lab actually works):**
```bash
sudo ip netns exec rclab bash -c 'timeout 2 bash -c "exec 3<>/dev/tcp/10.66.0.20/80" && echo REACHED || echo NOPE'
```
Expected: `REACHED`. Internet blocked, practice target reachable — exactly right.

**Check E — see the whole picture:**
```bash
sudo ./lab-net.sh status
```
Shows the namespace, the switch, and any attached targets. It will **refuse and warn** if a
real network interface is ever found on the switch.

### Managing the lab network by hand
```bash
sudo ./lab-net.sh up       # build it (also done automatically by 'redcoder --lab')
sudo ./lab-net.sh verify   # re-prove the internet is unreachable
sudo ./lab-net.sh status   # show what exists / safety check
sudo ./lab-net.sh down     # tear it all down (also removes the sudo rule)
```
The lab is temporary kernel state: it does **not** survive a reboot. `redcoder --lab`
rebuilds it for you each boot. Running `lab-net.sh` with no arguments prints a one-liner for
adding a second practice target if you want a bigger toy network.

---

## 5. ONLINE mode (real internet, maximum human-in-the-loop)

### What it is
Full capabilities — nothing is blocked — but **you approve everything that acts.** This is
the mode for when a task genuinely needs the internet (downloading a package, cloning a repo).

### How to start it
```bash
redcoder --online
```
Header shows `Online`.

### How it behaves
- Commands run **as your normal user** with **real network access** — exactly like a normal
  terminal, no isolation wrapper.
- **Every command that *acts*** (any shell command, file write, or edit) **asks first.**
  Reads and searches stay free.
- **Powerful/offensive tools go red** here (nmap, sqlmap, hydra, hashcat, metasploit,
  gobuster, netcat, etc.) — because in Online they could hit real, live targets. Red just
  means you type `y` to confirm; the tool still runs.
- The **"always" auto-approve shortcut is disabled** in Online, so a single keystroke can't
  quietly switch the whole session to hands-off. You stay in the loop on every action.

### Verifying Online is doing what you expect
Ask redcoder to run `curl -m 3 https://example.com` and approve it — it should **succeed**
(you have internet). That's the whole point of the mode. If you'd rather it not reach out,
switch back with `/net airgap` or `/net lab`.

---

## 6. Quick verification cheat-sheet

Run these in a **plain terminal** (not inside redcoder) to prove each wall yourself.

| Goal | Command | Want to see |
|---|---|---|
| See the airgapped environment | `unshare -rn ip -brief link` | only `lo ... DOWN` |
| Prove lab blocks internet | `sudo ./lab-net.sh verify` | `airgap confirmed` |
| No real cable on lab switch | `ip link show master rclab-br` | only `veth...`, never `eth0`/`wlan0` |
| Lab can't reach internet | `sudo ip netns exec rclab bash -c 'timeout 2 bash -c "exec 3<>/dev/tcp/1.1.1.1/443" && echo REACHED || echo BLOCKED'` | `BLOCKED` |
| Lab can reach the target | `sudo ip netns exec rclab bash -c 'timeout 2 bash -c "exec 3<>/dev/tcp/10.66.0.20/80" && echo REACHED || echo NOPE'` | `REACHED` |
| Full lab picture | `sudo ./lab-net.sh status` | namespace + switch, no physical NIC |

---

## 7. Belt-and-suspenders: killing the internet at the machine level

The modes above make it impossible for the *AI's shell commands* to reach the internet.
If you also want the *whole machine* physically unable to reach the internet (paranoia-grade,
recommended for serious QA), add a layer at the hardware level. Note: this does **not** break
redcoder — the model runs locally over loopback, which works with all network hardware off.

**Quick, reversible, this-session-only (software):**
```bash
sudo rfkill block all     # turns off all radios (Wi-Fi + Bluetooth)
ping -c1 -W2 1.1.1.1       # should now FAIL
sudo rfkill unblock all   # undo it
```
This does **not** survive a reboot, and a program with root could switch it back on.

**Permanent and certain (firmware — the real guarantee):**
Disable the network hardware in **BIOS/UEFI**. Once disabled there, the operating system
never even sees the device, so nothing in software can turn it on, and it stays off across
reboots.
1. Reboot, tap **Delete** to enter BIOS. Press **F7** for Advanced Mode if needed.
2. Go to **Settings → Advanced → Integrated Peripherals**.
3. Disable the **Wi-Fi / Bluetooth** device and the **Onboard LAN** controller.
4. **F10** to save and exit. (Fully reversible — re-enable the same way.)

Note: removing the external antenna is **not** enough — the built-in Wi-Fi still connects
faintly without it. Use BIOS.

**Verify from Kali:**
```bash
ip -brief link          # a disabled adapter is absent; only lo (and lab veths) remain
rfkill list             # radios show "blocked: yes"
ping -c1 -W2 1.1.1.1    # must fail
```

---

## 8. Troubleshooting

**"lab mode unavailable / falling back to Airgapped."**
The message names the failing check. Usual fixes: run `sudo ./lab-net.sh up` by hand and
read its output, or make sure `iproute2` is installed (`ip` command present).

**It keeps asking for my password.**
The password is needed once per boot to *build* the lab (making a network needs root). After
that, entering the already-built lab uses a narrow passwordless rule and won't prompt.

**`nmap` says "command not found".**
Install it: `sudo apt install -y nmap`. (Kali normally ships it.)

**"1000 packages upgradable" when I run apt.**
Normal on a rolling release. Do **not** run `apt upgrade` on a live USB — it fills the
persistence storage. You don't need to upgrade anything for redcoder to work.

**Is talking to Ollama a network connection?**
No — it's loopback (`127.0.0.1`), which never leaves the machine. See section 0.

---

## 9. One-paragraph summary

Redcoder's brain is local, so it works fully offline. The only thing that could touch the
internet is the shell commands it runs, and the mode controls that: **Airgapped** gives those
commands no network at all (proven by an empty network namespace and by watching `curl` fail);
**Lab** gives them a fake offline practice network with real targets but no internet cable
(proven by `lab-net.sh verify` and by checking no real NIC is on the switch); **Online** gives
them the real internet but makes you approve every action, with powerful tools flagged red. You
can verify every wall yourself with the cheat-sheet in section 6, and you can add a hardware
kill switch (BIOS) on top without breaking anything.
