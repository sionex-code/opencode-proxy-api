<div align="center">
  <h1>Kiro Agent Proxy 🚀</h1>
  <p>A robust local proxy bridging OpenAI-compatible editors with the native Kiro AWS Event Stream protocol.</p>

  <p>
    <a href="https://github.com/yourusername/kiro-proxy/stargazers"><img src="https://img.shields.io/github/stars/yourusername/kiro-proxy" alt="Stars Badge"/></a>
    <a href="https://github.com/yourusername/kiro-proxy/network/members"><img src="https://img.shields.io/github/forks/yourusername/kiro-proxy" alt="Forks Badge"/></a>
    <a href="https://github.com/yourusername/kiro-proxy/pulls"><img src="https://img.shields.io/github/issues-pr/yourusername/kiro-proxy" alt="Pull Requests Badge"/></a>
    <a href="https://github.com/yourusername/kiro-proxy/issues"><img src="https://img.shields.io/github/issues/yourusername/kiro-proxy" alt="Issues Badge"/></a>
    <a href="https://github.com/yourusername/kiro-proxy/blob/main/LICENSE"><img src="https://img.shields.io/github/license/yourusername/kiro-proxy" alt="License Badge"/></a>
  </p>
</div>

---

## 📖 Overview

The **Kiro Agent Proxy** enables advanced AI IDEs (like Opencode) that expect standard OpenAI-compatible API responses to communicate seamlessly with Kiro's backend infrastructure. 

It accomplishes this by translating standard JSON-based chat completions into AWS Binary Event Streams, injecting custom tool definitions, and converting the resulting streams back into OpenAI-compatible Server-Sent Events (SSE). 

Additionally, it features a **Web Dashboard** for managing multiple Kiro accounts, handling OAuth PKCE flows, dynamic proxy controlling, and automatic token refreshing.

![Dashboard Screenshot](dashboard.png)

## ✨ Features

- **Multi-Account Dashboard:** Easily manage and switch between different Kiro authenticated profiles.
- **Dynamic Active Switching:** The proxy automatically routes requests through whichever account is currently marked "Active" in the dashboard.
- **Auto-Refreshing Tokens:** Background monitoring of token lifespans. Automatically hits the `/refreshToken` endpoint when an active token is within 5 minutes of expiring.
- **Detailed Quota Tracking:** Real-time visibility into usage metrics, properly aggregating standard allocations and Free Trial limits.
- **Native Tool Translation:** Transparently maps generic OpenAI tool calls into the Kiro `toolSpec` schema.
- **Subprocess Management:** Start and stop the proxy server directly from the web interface without touching the terminal.

## 📂 Project Structure

```text
.
├── auth_capture_v2.py    # Main Web Dashboard & Profile Manager (Run this!)
├── proxy.py              # FastAPI proxy server (Auto-managed by dashboard)
├── kiro_api.py           # Core AWS binary event stream parsing client
├── profiles.json         # Local database for OAuth tokens & limits (Auto-generated)
├── tools.json            # Kiro-native tool definitions
├── opencode.json         # Example IDE connection config
├── requirements.txt      # Python dependencies
└── LICENSE               # MIT License
```

## 🚀 Usage Guide

### 1. Installation

Clone the repository and install the required dependencies:

```bash
git clone https://github.com/yourusername/kiro-proxy.git
cd kiro-proxy
pip install -r requirements.txt
```

### 2. Start the Dashboard

Run the main management script. This acts as both your OAuth handler and your proxy controller.

```bash
python auth_capture_v2.py
```

### 3. Add an Account
1. Open [http://localhost:3128](http://localhost:3128) in your browser.
2. Click **+ Add Account**.
3. Copy the provided authentication URL and open it in your preferred browser to log in via Kiro.
4. Your account will appear on the dashboard with its active token, refresh token, and accurate usage limits.

### 4. Start the Proxy
From the dashboard interface, click the green **Start Proxy** button. The proxy server will spin up on port `8000`.

### 5. Configure Your IDE
Configure your AI editor (like Opencode) to point to the local proxy. An exact copy-paste JSON configuration is provided at the bottom of the dashboard page. 

It will look something like this:
```json
{
  "provider": {
    "kiro": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Kiro Backend Proxy",
      "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "apiKey": "dummy-key-not-used-by-proxy"
      },
      "models": {
        "claude-sonnet-4.5": {
          "name": "Claude Sonnet 4.5 (via Kiro)",
          "limit": {
            "context": 200000,
            "output": 65536
          }
        }
      }
    }
  }
}
```

## 📈 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=yourusername/kiro-proxy&type=Date)](https://star-history.com/#yourusername/kiro-proxy&Date)

## 📄 License

This project is licensed under the [MIT License](LICENSE).
