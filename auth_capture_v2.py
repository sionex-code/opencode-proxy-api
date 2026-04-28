import hashlib
import secrets
import base64
import requests
import uvicorn
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import json
import urllib.parse
import time
import os
import uuid
import subprocess
import psutil

app = FastAPI()

# Configuration
TOKEN_ENDPOINT = "https://prod.us-east-1.auth.desktop.kiro.dev/oauth/token"
REFRESH_ENDPOINT = "https://prod.us-east-1.auth.desktop.kiro.dev/refreshToken"
USAGE_ENDPOINT = "https://q.us-east-1.amazonaws.com/getUsageLimits"
PROFILES_FILE = "profiles.json"
PROXY_SCRIPT = "proxy.py"

# State management for current OAuth flow
oauth_flow = {
    "verifier": "",
    "challenge": "",
    "state": ""
}

# Proxy Process Management
proxy_process = None

def start_proxy():
    global proxy_process
    if proxy_process is None or proxy_process.poll() is not None:
        try:
            proxy_process = subprocess.Popen(["python", PROXY_SCRIPT])
            return True
        except Exception as e:
            print(f"Failed to start proxy: {e}")
            return False
    return True

def stop_proxy():
    global proxy_process
    if proxy_process is not None and proxy_process.poll() is None:
        try:
            # On Windows, we might need to terminate child processes too, but proxy.py is simple
            proxy_process.terminate()
            proxy_process.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass
        except Exception:
            proxy_process.kill()
        proxy_process = None
    return True

def is_proxy_running():
    global proxy_process
    return proxy_process is not None and proxy_process.poll() is None

# --- Profile Helpers ---

def load_profiles() -> dict:
    if not os.path.exists(PROFILES_FILE):
        return {"active_profile_id": None, "profiles": []}
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"active_profile_id": None, "profiles": []}

def save_profiles(data: dict):
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def fetch_usage_limits(access_token: str, profile_arn: str) -> dict:
    """Fetch credits and plan info from Kiro API"""
    try:
        url = f"{USAGE_ENDPOINT}?origin=AI_EDITOR&profileArn={urllib.parse.quote(profile_arn)}&resourceType=AGENTIC_REQUEST"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "x-amz-user-agent": "aws-sdk-js/1.0.0",
            "user-agent": "aws-sdk-js/1.0.0 ua/2.1 os/win32#10.0.26200 lang/js md/nodejs#22.22.0"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"[ERROR] Failed to fetch usage: {e}")
    return {}

def refresh_profile_token(profile: dict) -> dict:
    """Perform token refresh using refreshToken"""
    try:
        payload = {"refreshToken": profile["refresh_token"]}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json"
        }
        resp = requests.post(REFRESH_ENDPOINT, json=payload, headers=headers, timeout=10)
        if resp.ok:
            data = resp.json()
            if "data" in data and isinstance(data["data"], dict):
                data = data["data"]
            
            profile["access_token"] = data.get("accessToken") or data.get("access_token")
            if "refreshToken" in data or "refresh_token" in data:
                profile["refresh_token"] = data.get("refreshToken") or data.get("refresh_token")
            profile["expires_at"] = int(time.time()) + data.get("expiresIn", 3600)
            
            usage = fetch_usage_limits(profile["access_token"], profile["profile_arn"])
            if usage:
                update_profile_usage(profile, usage)
            
            profile["last_refreshed"] = int(time.time())
            return profile
        else:
            print(f"[ERROR] Refresh failed with status {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[ERROR] Refresh failed for {profile.get('email')}: {e}")
    return profile

def update_profile_usage(profile: dict, usage: dict):
    profile["email"] = usage.get("userInfo", {}).get("email", profile.get("email", "Kiro User"))
    breakdown_list = usage.get("usageBreakdownList", [])
    if breakdown_list:
        breakdown = breakdown_list[0]
        
        # Fetch base limits
        base_limit = breakdown.get("usageLimitWithPrecision", breakdown.get("usageLimit", 0))
        base_used = breakdown.get("currentUsageWithPrecision", breakdown.get("currentUsage", 0))
        
        # Fetch free trial limits (if any)
        free_trial = breakdown.get("freeTrialInfo", {})
        ft_limit = free_trial.get("usageLimitWithPrecision", free_trial.get("usageLimit", 0)) if free_trial else 0
        ft_used = free_trial.get("currentUsageWithPrecision", free_trial.get("currentUsage", 0)) if free_trial else 0
        
        # Combine both
        profile["credits_total"] = base_limit + ft_limit
        profile["credits_used"] = base_used + ft_used
        
        plan = usage.get("subscriptionInfo", {}).get("subscriptionTitle", "Free")
        if ft_limit > 0:
            plan += " (+ Trial)"
            
        profile["plan_name"] = plan
        
    profile["usage_reset_at"] = usage.get("nextDateReset")

# --- OAuth PKCE Logic ---

def generate_pkce():
    verifier = secrets.token_urlsafe(32)
    sha256 = hashlib.sha256(verifier.encode('utf-8')).digest()
    challenge = base64.urlsafe_b64encode(sha256).decode('utf-8').rstrip('=')
    return verifier, challenge

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    oauth_flow["verifier"], oauth_flow["challenge"] = generate_pkce()
    oauth_flow["state"] = secrets.token_urlsafe(16)
    
    params = {
        "state": oauth_flow["state"],
        "code_challenge": oauth_flow["challenge"],
        "code_challenge_method": "S256",
        "redirect_uri": "http://localhost:3128",
        "redirect_from": "KiroIDE"
    }
    encoded = urllib.parse.urlencode(params)
    signin_url = f"https://app.kiro.dev/signin?{encoded}"

    data = load_profiles()
    profiles = data.get("profiles", [])
    active_id = data.get("active_profile_id")
    proxy_running = is_proxy_running()
    
    proxy_status_html = f"""
    <div style="background:white; border:1px solid #e2e8f0; border-radius:12px; padding:20px; margin-bottom:24px; display:flex; justify-content:space-between; align-items:center;">
        <div>
            <h3 style="margin:0; color:#2d3748;">Proxy Status</h3>
            <p style="margin:4px 0; font-size:14px; color:{'#48bb78' if proxy_running else '#e53e3e'}; font-weight:bold;">
                {'Running' if proxy_running else 'Stopped'}
            </p>
        </div>
        <div>
            <a href="/proxy/toggle" style="background:{'#e53e3e' if proxy_running else '#48bb78'}; color:white; padding:8px 16px; text-decoration:none; border-radius:6px; font-weight:bold;">
                {'Stop Proxy' if proxy_running else 'Start Proxy'}
            </a>
        </div>
    </div>
    """

    profile_cards = ""
    for p in profiles:
        is_active = p["id"] == active_id
        is_expired = p["expires_at"] < time.time()
        
        status_badge = '<span style="background:#48bb78; color:white; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px;">Active</span>' if is_active else ""
        expired_warn = '<p style="color:red; font-size:12px; margin-top:8px;">⚠ Token Expired</p>' if is_expired else ""
        
        total = p.get("credits_total", 0)
        used = p.get("credits_used", 0)
        percent = (used / total * 100) if total > 0 else 0
        
        profile_cards += f"""
        <div style="background:{'#ebf8ff' if is_active else 'white'}; border:1px solid {'#3182ce' if is_active else '#e2e8f0'}; border-radius:12px; padding:20px; margin-bottom:16px; box-shadow:0 2px 4px rgba(0,0,0,0.05);">
            <div style="display:flex; justify-content:space-between; align-items:start;">
                <div>
                    <h3 style="margin:0; color:#2d3748; display:flex; align-items:center;">{p.get('email', 'Unknown User')} {status_badge}</h3>
                    <p style="margin:4px 0; color:#718096; font-size:14px;">{p.get('login_provider', 'Kiro')} | {p.get('plan_name', 'Free')}</p>
                </div>
                <div style="text-align:right;">
                    <a href="/activate/{p['id']}" style="background:#3182ce; color:white; padding:6px 12px; text-decoration:none; border-radius:6px; font-size:14px; display:{'none' if is_active else 'inline-block'}">Make Active</a>
                    <a href="/refresh/{p['id']}" style="background:#edf2f7; color:#2d3748; padding:6px 12px; text-decoration:none; border-radius:6px; font-size:14px; margin-left:8px;">Refresh</a>
                    <a href="/delete/{p['id']}" style="color:#e53e3e; margin-left:12px; font-size:14px;" onclick="return confirm('Delete profile?')">Delete</a>
                </div>
            </div>
            
            <div style="margin-top:16px;">
                <div style="display:flex; justify-content:space-between; font-size:13px; color:#4a5568; margin-bottom:4px;">
                    <span>Credits: {used:.2f} / {total:.2f}</span>
                    <span>{percent:.1f}%</span>
                </div>
                <div style="background:#edf2f7; height:8px; border-radius:4px; overflow:hidden;">
                    <div style="background:#3182ce; width:{percent}%; height:100%;"></div>
                </div>
            </div>
            {expired_warn}
        </div>
        """

    if not profile_cards:
        profile_cards = "<p style='color:#718096; text-align:center; padding:40px;'>No profiles found. Add one to get started.</p>"

    opencode_config_html = """
    <div style="margin-top:24px; padding:20px; background:white; border-radius:12px; border:1px solid #e2e8f0; box-shadow:0 2px 4px rgba(0,0,0,0.05);">
        <h3 style="margin-top:0; color:#2d3748;">Opencode Configuration</h3>
        <p style="color:#718096; font-size:14px; margin-bottom:12px;">Add this to your <strong>opencode.json</strong> to connect to the proxy:</p>
        <div style="position:relative;">
            <pre id="opencodeConf" style="background:#2d3748; color:#f7fafc; padding:16px; border-radius:8px; overflow-x:auto; font-size:13px; line-height:1.4; margin:0;">{
  "provider": {
    "kiro": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Kiro Backend Proxy",
      "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "apiKey": "dummy-key-not-used-by-proxy"
      },
      "models": {
        "claude-sonnet-4.0": {
          "name": "Claude Sonnet 4.0 (via Kiro)",
          "limit": {
            "context": 200000,
            "output": 65536
          }
        },
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
}</pre>
            <button onclick="copyConfig()" style="position:absolute; top:8px; right:8px; background:#4a5568; color:white; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:12px; transition: background 0.2s;">Copy</button>
        </div>
        <p id="confMsg" style="color:#48bb78; font-size:12px; margin-top:8px; text-align:right; display:none; font-weight:600;">Copied to clipboard!</p>
    </div>
    <script>
        function copyConfig() {
            var text = document.getElementById("opencodeConf").innerText;
            navigator.clipboard.writeText(text);
            document.getElementById("confMsg").style.display = "block";
            setTimeout(() => { document.getElementById("confMsg").style.display = "none"; }, 3000);
        }
    </script>
    """

    add_account_modal = f"""
    <div id="addAccountModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:1000; justify-content:center; align-items:center;">
        <div style="background:white; padding:24px; border-radius:12px; width:90%; max-width:500px; box-shadow:0 4px 6px rgba(0,0,0,0.1);">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                <h2 style="margin:0; color:#2d3748;">Add Account</h2>
                <button onclick="closeModal()" style="background:none; border:none; font-size:24px; cursor:pointer; color:#718096; padding:0;">&times;</button>
            </div>
            <p style="color:#718096; margin-bottom:16px;">Authenticate via the Kiro portal. Copy the link or open it in your browser.</p>
            <textarea id="authUrl" rows="4" style="width:100%; padding:12px; font-size:14px; background:#edf2f7; border:1px solid #cbd5e0; border-radius:6px; margin-bottom:16px; resize:none; box-sizing:border-box;" readonly>{signin_url}</textarea>
            <div style="display:flex; justify-content:flex-end;">
                <button onclick="copyAuthUrl()" style="background:#edf2f7; color:#2d3748; padding:10px 16px; border:none; border-radius:8px; cursor:pointer; font-weight:bold; margin-right:8px; font-size:14px;">Copy URL</button>
                <a href="{signin_url}" target="_blank" style="background:#3182ce; color:white; padding:10px 16px; text-decoration:none; border-radius:8px; font-weight:bold; font-size:14px;">Open Browser</a>
            </div>
            <p id="modalMsg" style="color:#48bb78; font-size:14px; margin-top:16px; text-align:right; display:none; font-weight:bold;">Copied to clipboard!</p>
        </div>
    </div>
    <script>
        function openModal() {{ document.getElementById('addAccountModal').style.display = 'flex'; }}
        function closeModal() {{ document.getElementById('addAccountModal').style.display = 'none'; }}
        function copyAuthUrl() {{
            var copyText = document.getElementById("authUrl");
            copyText.select();
            copyText.setSelectionRange(0, 99999);
            navigator.clipboard.writeText(copyText.value);
            document.getElementById("modalMsg").style.display = "block";
            setTimeout(() => {{ document.getElementById("modalMsg").style.display = "none"; }}, 3000);
        }}
    </script>
    """

    return f"""
    <html>
    <head>
        <title>Kiro Profile Manager</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; background:#f7fafc; color:#1a202c; line-height:1.5; }}
            .container {{ max-width: 600px; margin: 40px auto; padding: 0 20px 40px 20px; }}
            .header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:30px; }}
            .btn-add {{ background:#2d3748; color:white; padding:10px 20px; border:none; border-radius:8px; font-weight:600; cursor:pointer; font-size:16px; transition:all 0.2s; }}
            .btn-add:hover {{ background:#1a202c; transform:translateY(-1px); }}
        </style>
    </head>
    <body>
        {add_account_modal}
        <div class="container">
            <div class="header">
                <h1 style="margin:0; font-size:24px; padding-top:40px;">Kiro Profiles</h1>
                <button onclick="openModal()" class="btn-add" style="margin-top:40px;">+ Add Account</button>
            </div>
            {proxy_status_html}
            <h2 style="font-size:18px; margin-bottom:16px;">Accounts</h2>
            {profile_cards}
            <div style="margin-top:24px; padding:20px; background:#ebf8ff; border-radius:12px; border:1px solid #bee3f8; font-size:14px; color:#2c5282;">
                <strong>Proxy Status:</strong> The proxy server will automatically use the active profile highlighted above. If a profile token expires, it will automatically refresh it.
            </div>
            {opencode_config_html}
        </div>
    </body>
    </html>
    """

@app.get("/proxy/toggle")
async def toggle_proxy():
    if is_proxy_running():
        stop_proxy()
    else:
        start_proxy()
    return RedirectResponse("/")



@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str, login_option: str = "google"):
    if state != oauth_flow["state"]:
        return HTMLResponse("State mismatch", status_code=400)
    
    payload = {
        "code": code,
        "code_verifier": oauth_flow["verifier"],
        "redirect_uri": f"http://localhost:3128/oauth/callback?login_option={login_option}"
    }
    
    try:
        resp = requests.post(TOKEN_ENDPOINT, json=payload, timeout=10)
        if not resp.ok:
            payload["redirect_uri"] = "http://localhost:3128"
            resp = requests.post(TOKEN_ENDPOINT, json=payload, timeout=10)
        
        if resp.ok:
            data = resp.json()
            if "data" in data: data = data["data"]
            
            new_id = str(uuid.uuid4())
            profile = {
                "id": new_id,
                "email": "Pending Fetch...",
                "login_provider": login_option.capitalize(),
                "access_token": data["accessToken"],
                "refresh_token": data["refreshToken"],
                "profile_arn": data["profileArn"],
                "expires_at": int(time.time()) + data.get("expiresIn", 3600),
                "credits_total": 0,
                "credits_used": 0,
                "plan_name": "Free",
                "last_refreshed": int(time.time())
            }
            
            usage = fetch_usage_limits(profile["access_token"], profile["profile_arn"])
            if usage:
                update_profile_usage(profile, usage)
            
            db = load_profiles()
            db["profiles"].append(profile)
            if not db["active_profile_id"]:
                db["active_profile_id"] = new_id
            save_profiles(db)
            
            return RedirectResponse("/")
    except Exception as e:
        return HTMLResponse(f"Exchange failed: {e}", status_code=500)
    
    return HTMLResponse("Failed to complete OAuth", status_code=500)

@app.get("/activate/{profile_id}")
async def activate_profile(profile_id: str):
    db = load_profiles()
    db["active_profile_id"] = profile_id
    save_profiles(db)
    return RedirectResponse("/")

@app.get("/refresh/{profile_id}")
async def refresh_manual(profile_id: str):
    db = load_profiles()
    for i, p in enumerate(db["profiles"]):
        if p["id"] == profile_id:
            db["profiles"][i] = refresh_profile_token(p)
            break
    save_profiles(db)
    return RedirectResponse("/")

@app.get("/delete/{profile_id}")
async def delete_profile(profile_id: str):
    db = load_profiles()
    db["profiles"] = [p for p in db["profiles"] if p["id"] != profile_id]
    if db["active_profile_id"] == profile_id:
        db["active_profile_id"] = db["profiles"][0]["id"] if db["profiles"] else None
    save_profiles(db)
    return RedirectResponse("/")

# --- API for Proxy ---

@app.get("/api/active-profile")
async def get_active_profile():
    db = load_profiles()
    active_id = db.get("active_profile_id")
    if not active_id:
        return JSONResponse({"error": "No active profile"}, status_code=404)
    
    for p in db["profiles"]:
        if p["id"] == active_id:
            if p["expires_at"] < time.time() + 300:
                p = refresh_profile_token(p)
                for idx, orig in enumerate(db["profiles"]):
                    if orig["id"] == active_id:
                        db["profiles"][idx] = p
                save_profiles(db)
            
            return p
    return JSONResponse({"error": "Profile not found"}, status_code=404)

if __name__ == "__main__":
    print("Kiro Profile Dashboard running at http://localhost:3128")
    uvicorn.run(app, host="0.0.0.0", port=3128)
