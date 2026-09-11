<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:5865F2,50:7C3AED,100:00D4FF&height=180&section=header&text=Discord%20VC%20Controller&fontSize=42&fontColor=ffffff&animation=fadeIn&fontAlignY=38" width="100%" alt="Discord VC Controller banner">

### 🎙️ Control • Track • Automate

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=20&duration=2800&pause=900&color=7C3AED&center=true&vCenter=true&width=650&lines=Powerful+voice+channel+control;Accurate+VC+session+tracking;Server+analytics+and+workflows" alt="Animated project description">

<p>
<img src="https://img.shields.io/badge/Discord-Bot-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
<img src="https://img.shields.io/badge/Python-3.x-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/PostgreSQL-Database-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
<img src="https://img.shields.io/github/last-commit/Jack-chithdhu/Discord-vc-controller?style=for-the-badge" alt="Last commit">
</p>

<p>
<a href="#-features">Features</a> •
<a href="#-visual-demo">Demo</a> •
<a href="#-architecture">Architecture</a> •
<a href="#-setup">Setup</a> •
<a href="#-roadmap">Roadmap</a>
</p>

</div>

---

## ✨ What is it?

**Discord VC Controller** is a Discord server tool focused on voice-channel management, reliable VC activity tracking, analytics, and custom approval workflows.

> 🎮 Built for a growing gaming community — with the tools to control the server and understand how people actually use it.

---

## 🔥 Features

<table>
<tr>
<td width="50%">

### 🔊 Voice Control

Move, manage and control members and voice channels from one system.

- Member movement
- Lock / unlock controls
- Voice limits
- Access control
- VC state handling

</td>
<td width="50%">

### 📊 VC Analytics

Turn Discord voice activity into useful statistics.

- Session tracking
- Today / Week / Month / All Time
- Channel activity
- Session history
- Leaderboards

</td>
</tr>
<tr>
<td width="50%">

### 📋 Approval Workflows

A form-based request flow for server operations.

- Submit forms
- Admin review
- Approve / reject
- Custom actions
- WhatsApp approval workflow

</td>
<td width="50%">

### 🌐 Dashboard

Bring important server controls and analytics together in one place.

- Remote management
- VC activity
- Analytics
- Centralised controls

</td>
</tr>
</table>

---

## 🎬 Visual Demo

<div align="center">

| 🔊 VC Control | 📊 Analytics |
|:---:|:---:|
| `Discord → Action → Done` | `Events → Sessions → Stats` |

</div>

> 📸 **Add your real screenshots/GIFs here** when ready. The README is already structured for them:
>
> `assets/vc-control.gif` · `assets/vc-analytics.gif` · `assets/approval.gif`

<!--
<div align="center">
<img src="assets/vc-control.gif" width="48%" alt="VC control demo">
<img src="assets/vc-analytics.gif" width="48%" alt="VC analytics demo">
</div>

<div align="center">
<img src="assets/approval.gif" width="70%" alt="Approval workflow demo">
</div>
-->

---

## 🧠 Architecture

```text
                         DISCORD
                            │
                            ▼
                 ┌────────────────────┐
                 │  VC CONTROLLER     │
                 └──────────┬─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
     🔊 Controls        📊 Tracking       📋 Forms
          │                 │                 │
          └─────────────────┼─────────────────┘
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

The tracking system separates raw Discord events from interpreted sessions:

```text
Discord Voice Event
        │
        ▼
    ┌─────────┐
    │ vc_logs │  ← Raw event history
    └────┬────┘
         │
         ▼
 ┌────────────────┐
 │  vc_sessions   │  ← Actual sessions
 └───────┬────────┘
         │
         ▼
   📊 Analytics
         │
         ▼
    🌐 Dashboard
```

This provides an audit trail while keeping statistics based on session data.

---

## 🗄️ Data Model

| Data | Purpose |
|---|---|
| `vc_logs` | Raw Discord voice events / audit history |
| `vc_sessions` | Interpreted voice sessions |
| Analytics | Statistics calculated from session data |

---

## 🛡️ Tracking Reliability

The VC system is designed around the important Discord voice states:

- 🟢 JOIN
- 🔴 LEAVE
- 🔄 MOVE
- ⚡ DISCONNECT
- 🔁 Bot restart / reconnect
- 🚦 Startup reconciliation
- ⏱️ Long-running sessions
- 📅 Date/time boundaries
- 🗃️ Historical session data

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

Configure the environment values required by the project, including the Discord bot token and database connection.

```text
DISCORD_TOKEN
DATABASE_URL
```

### 4. Start

```bash
python app.py
```

Follow the repository's configuration files for any additional settings required by your deployment.

---

## 🗺️ Roadmap

<div align="center">

| Feature | Status |
|:---|:---:|
| 🔊 VC Control | ✅ Done |
| 📊 VC Tracking | ✅ Done |
| 📈 VC Analytics | ✅ Done |
| 📋 Approval Workflows | ✅ Done |
| 🌐 Dashboard | ✅ Done |
| 🔊 VC Reservations | 🚧 Next |
| 👥 Who Was With Me? | 🚧 Next |
| 📊 Personal VC Reports | 🚧 Next |

</div>

---

## 💡 Project Philosophy

This project isn't trying to be another bot with hundreds of unrelated commands.

It focuses on **useful systems for an active community** — voice control, reliable activity tracking, analytics and workflows that work together.

---

## 🤝 Contributing

Suggestions, improvements and bug reports are welcome.

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

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:00D4FF,50:7C3AED,100:5865F2&height=120&section=footer&animation=fadeIn" width="100%" alt="Animated footer">

</div>
