<div align="center">

# 🎙️ Discord VC Controller

### Control • Track • Automate

<p>
  <img src="https://img.shields.io/badge/Discord-Bot-5865F2?style=for-the-badge&logo=discord&logoColor=white">
  <img src="https://img.shields.io/badge/Python-3.x-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/PostgreSQL-Database-4169E1?style=for-the-badge&logo=postgresql&logoColor=white">
</p>

<a href="#-features">Features</a> •
<a href="#-demo">Demo</a> •
<a href="#-architecture">Architecture</a> •
<a href="#-setup">Setup</a> •
<a href="#-roadmap">Roadmap</a>

</div>

---

## ✨ What is it?

**Discord VC Controller** is a Discord bot focused on voice-channel management, VC activity tracking, analytics, and custom approval workflows.

> **Make your server easier to control without making it complicated.**

---

## 🔥 Features

<table>
<tr>
<td width="50%">

### 🔊 Voice Control
- Move members between VCs
- Lock / unlock voice channels
- Manage voice limits
- Control access
- Handle VC state changes

</td>
<td width="50%">

### 📊 VC Analytics
- Individual VC sessions
- Today / Week / Month / All Time
- Session history
- Channel activity
- Statistics & leaderboards

</td>
</tr>
<tr>
<td width="50%">

### 📋 Approval Workflows
- Form-based requests
- Admin approval / rejection
- Custom actions after approval
- WhatsApp approval workflow

</td>
<td width="50%">

### 🌐 Dashboard
- Remote server management
- VC activity
- Analytics
- Centralised controls

</td>
</tr>
</table>

---

## 🎬 Demo

> Add your real screenshots/GIFs to `assets/` and uncomment the image lines below.

### 🔊 VC Control

```text
Discord → Select member → Choose action → Done
```

<!-- <img src="assets/vc-control.gif" width="850"> -->

### 📊 VC Analytics

<!-- <img src="assets/vc-analytics.gif" width="850"> -->

### 📋 Approval Workflow

<!-- <img src="assets/approval.gif" width="850"> -->

---

## 🧠 Architecture

```text
                    ┌─────────────────┐
                    │     DISCORD     │
                    └────────┬────────┘
                             │
                             ▼
                  ┌────────────────────┐
                  │  VC CONTROLLER     │
                  └─────────┬──────────┘
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
        🔊 Controls     📊 Tracking     📋 Forms
             │              │              │
             └──────────────┼──────────────┘
                            ▼
                    ┌───────────────┐
                    │   PostgreSQL  │
                    └───────┬───────┘
                            │
                            ▼
                     🌐 Dashboard
```

---

## 📈 VC Tracking

The tracking system keeps **raw voice events** while using **session records** for statistics.

```text
Discord Voice Event
        │
        ▼
    vc_logs
  Raw history
        │
        ▼
  vc_sessions
Actual sessions
        │
        ▼
    Analytics
        │
        ▼
     Dashboard
```

This keeps an audit trail while allowing statistics to be calculated from session data.

---

## 🗄️ Data Model

| Data | Purpose |
|---|---|
| `vc_logs` | Raw Discord voice events / audit history |
| `vc_sessions` | Interpreted VC sessions |
| Analytics | Calculated from session data |

---

## 🚀 Setup

### 1. Clone

```bash
git clone https://github.com/Jack-chithdhu/Discord-vc-controller.git
cd Discord-vc-controller
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Set the variables required by the project, such as:

```text
DISCORD_TOKEN
DATABASE_URL
```

### 4. Start

```bash
python app.py
```

Follow the project's current configuration files for any additional settings.

---

## 🛡️ Reliability

The VC system is designed around:

- JOIN / LEAVE
- MOVE between voice channels
- DISCONNECT
- Bot restart/reconnect handling
- Startup reconciliation
- Long-running sessions
- Date/time boundaries
- Historical session data

---

## 🗺️ Roadmap

```text
VC Control              ████████████████████  DONE
VC Tracking             ████████████████████  DONE
VC Analytics            ████████████████████  DONE
Approval Workflows      ████████████████████  DONE
Dashboard               ████████████████████  DONE

VC Reservations         ░░░░░░░░░░░░░░░░░░░░  NEXT
Who Was With Me?        ░░░░░░░░░░░░░░░░░░░░  NEXT
Personal VC Reports     ░░░░░░░░░░░░░░░░░░░░  NEXT
```

---

## 💡 Project Philosophy

Most Discord bots try to do everything.

This project focuses on building **useful systems around the way a server actually works**.

Voice control, activity tracking, analytics, and approval workflows are designed to work together rather than being a collection of unrelated commands.

---

## 🤝 Contributing

Suggestions, bug reports and improvements are welcome.

For VC tracking issues, include:

- What happened
- Expected result
- Actual result
- Approximate time
- Relevant VC/channel

---

<div align="center">

### 🎙️ Control your VC. Understand your server.

**Built for friends. Built for gaming. Built to grow.**

<img src="https://capsule-render.vercel.app/api?type=waving&color=7C3AED&height=100&section=footer" alt="Animated footer">

</div>
